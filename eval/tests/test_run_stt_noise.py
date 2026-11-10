"""eval/run_stt_noise.py — pass/fail judging, cost gate, clips never inside the repo (fake STT, no API, no ffmpeg)."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import eval.run_stt_noise as noise  # puts backend/ on sys.path, so app classes come through it

Settings, STTError = noise.Settings, noise.STTError

PRICES = {"cost_cap_usd": 1.0, "stt_models": {
    "whisper-1": {"usd_per_minute": 0.006}, "gpt-4o-transcribe": {"usd_per_minute": 0.006},
    "gpt-4o-mini-transcribe": {"usd_per_minute": 0.003}}}
PROMPT = "장부 입력: 외상, 현금, 박스, 상회, 만 원, 다섯"


@pytest.fixture(autouse=True)
def _clean_git_tree(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(noise.baseline, "_git_state", lambda: {"git_commit": "abc1234", "git_dirty": False})


BASE = Settings(_env_file=None, app_env="development")


class _ConditionSettings(SimpleNamespace):
    """Condition settings without .env: STT model and prompt file as #137 carries them, the rest from BASE."""

    def __getattr__(self, name):
        return getattr(BASE, name)


def _settings(model: str, prompt_file: Path | None) -> _ConditionSettings:
    return _ConditionSettings(stt_model=model, stt_prompt_file=str(prompt_file) if prompt_file else "")


def _fake_clips(work_dir: Path) -> list[dict]:
    clips = []
    for name, seconds in (("silence_1s", 1.0), ("pink_2s", 2.0)):
        path = work_dir / f"{name}.ogg"
        path.write_bytes(b"OggS-fake")
        clips.append({"name": name, "path": path, "seconds": seconds, "sha256": "x"})
    return clips


class ScriptedSTT:
    """Answers by (model, prompt on, clip): text, or None for the empty-transcript STTError."""

    def __init__(self, answers: dict):
        self.answers, self.calls = answers, []

    def __call__(self, audio, filename, mime, *, settings):
        key = (settings.stt_model, bool(settings.stt_prompt_file), Path(filename).stem)
        self.calls.append(key)
        text = self.answers.get(key)
        if text is None:
            raise STTError(noise.EMPTY_TRANSCRIPT)
        if text == "NETWORK":
            raise STTError("STT request failed: APIConnectionError")
        return text


def _run(tmp_path: Path, stt, *, prompt: bool = True, models=("whisper-1",), prices=PRICES) -> dict:
    prompt_file = None
    if prompt:
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text(PROMPT + "\n", encoding="utf-8")
    work_dir = tmp_path / "clips"
    work_dir.mkdir()
    return noise.run(out_dir=tmp_path / "out", models=list(models), prompt_file=prompt_file, repeat=1,
                     work_dir=work_dir, prices=prices, stt=stt, settings_for=_settings, clip_maker=_fake_clips,
                     run_id="t")


def test_empty_or_unfilled_passes_and_any_filled_field_fails(tmp_path: Path) -> None:
    stt = ScriptedSTT({("whisper-1", False, "pink_2s"): "시청해 주셔서 감사합니다.",
                       ("whisper-1", True, "silence_1s"): "외상",
                       ("whisper-1", True, "pink_2s"): "만 원"})
    result = _run(tmp_path, stt)
    off, on = result["conditions"]
    assert (off["prompt_on"], off["pass_empty"], off["pass_unfilled"], off["fail_filled"], off["verdict"]) == \
        (False, 1, 1, 0, "통과")
    assert (on["prompt_on"], on["fail_filled"], on["filled_fields"], on["verdict"]) == (True, 2, ["credit", "total"], "실패")
    filled = {c["clip"]: c["filled"] for c in result["calls"] if c["prompt_on"]}
    assert filled == {"silence_1s": {"credit": True}, "pink_2s": {"total": 10000}}


def test_counterparty_filled_by_a_prompt_suffix_fails(tmp_path: Path) -> None:
    stt = ScriptedSTT({("whisper-1", True, "silence_1s"): "상회"})
    call = next(c for c in _run(tmp_path, stt)["calls"] if c["prompt_on"] and c["clip"] == "silence_1s")
    assert call["outcome"] == "fail_filled" and call["filled"] == {"counterparty": "상회"}


def test_transcript_chars_prompt_word_hits_and_leak(tmp_path: Path) -> None:
    stt = ScriptedSTT({("whisper-1", True, "pink_2s"): "장부 입력: 외상, 현금, 박스"})
    call = next(c for c in _run(tmp_path, stt)["calls"] if c["prompt_on"] and c["clip"] == "pink_2s")
    assert call["chars"] == len("장부 입력: 외상, 현금, 박스")
    assert call["prompt_word_hits"] == 5
    assert call["prompt_leak"] is True
    assert call["outcome"] == "pass_unfilled"  # interpret already refuses the long echo


def test_other_stt_error_is_neither_pass_nor_fail(tmp_path: Path) -> None:
    stt = ScriptedSTT({("whisper-1", False, "silence_1s"): "NETWORK", ("whisper-1", False, "pink_2s"): "음"})
    off = _run(tmp_path, stt, prompt=False)["conditions"][0]
    assert (off["stt_error"], off["fail_filled"], off["verdict"]) == (1, 0, "판정 보류")


def test_prompt_off_only_without_prompt_file(tmp_path: Path) -> None:
    stt = ScriptedSTT({})
    result = _run(tmp_path, stt, prompt=False, models=("whisper-1", "gpt-4o-transcribe"))
    assert [(r["model"], r["prompt_on"]) for r in result["conditions"]] == [("whisper-1", False), ("gpt-4o-transcribe", False)]
    assert result["run"]["stt_prompt"] == {"on": False, "chars": 0, "sha256": None}


def test_writes_summary_and_report_with_transcripts(tmp_path: Path) -> None:
    stt = ScriptedSTT({("whisper-1", True, "pink_2s"): "외상"})
    _run(tmp_path, stt)
    summary = json.loads((tmp_path / "out" / "t.summary.json").read_text(encoding="utf-8"))
    assert summary["run"]["stt_prompt"]["chars"] == len(PROMPT)
    report = (tmp_path / "out" / "t.report.md").read_text(encoding="utf-8")
    assert "| whisper-1 | 있음 | pink_2s | 1 | 2 | 1 | credit=True | 외상 |" in report


def test_cost_estimate_and_cap() -> None:
    clips = [{"seconds": 1.0}, {"seconds": 2.0}, {"seconds": 3.0}]  # 0.1 min
    estimate = noise.estimate_cost(clips, ["whisper-1", "gpt-4o-mini-transcribe"], prompt_conditions=2, repeat=3,
                                   prices=PRICES)
    assert estimate["per_model_usd"] == {"whisper-1": 0.0036, "gpt-4o-mini-transcribe": 0.0018}
    assert estimate["total_usd"] == 0.0054
    assert estimate["calls"] == 36
    assert estimate["if_each_call_billed_1min_usd"] == pytest.approx(18 * 0.006 + 18 * 0.003)
    with pytest.raises(noise.VoiceE2EError, match="상한"):
        noise.check_cap({**estimate, "cap_usd": 0.001})


def test_cap_refusal_happens_before_any_stt_call(tmp_path: Path) -> None:
    stt = ScriptedSTT({})
    with pytest.raises(noise.VoiceE2EError, match="상한"):
        _run(tmp_path, stt, prices={**PRICES, "cost_cap_usd": 0.0})
    assert stt.calls == []


def test_missing_price_or_dirty_tree_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(noise.VoiceE2EError, match="단가"):
        _run(tmp_path, ScriptedSTT({}), models=("unknown-model",))
    monkeypatch.setattr(noise.baseline, "_git_state", lambda: {"git_commit": "abc", "git_dirty": True})
    with pytest.raises(noise.VoiceE2EError, match="커밋"):
        noise.run(out_dir=tmp_path, models=["whisper-1"], prices=PRICES, stt=ScriptedSTT({}),
                  settings_for=_settings, clip_maker=_fake_clips)


def test_missing_prompt_file_stops_before_making_clips(tmp_path: Path) -> None:
    made = []
    with pytest.raises(noise.VoiceE2EError, match="STT_PROMPT_FILE"):
        noise.run(out_dir=tmp_path / "out", models=["whisper-1"], prompt_file=tmp_path / "missing.txt",
                  prices=PRICES, stt=ScriptedSTT({}), settings_for=_settings, clip_maker=made.append)
    assert made == []


def test_real_stt_without_key_refuses(tmp_path: Path) -> None:
    settings_for = lambda model, path: Settings(_env_file=None, app_env="development", stt_model=model)
    with pytest.raises(noise.VoiceE2EError, match="OPENAI_API_KEY"):
        noise.run(out_dir=tmp_path, models=["whisper-1"], prices=PRICES, settings_for=settings_for,
                  clip_maker=_fake_clips)


def test_prompt_condition_refused_when_backend_has_no_stt_prompt_file(monkeypatch: pytest.MonkeyPatch,
                                                                     tmp_path: Path) -> None:
    """Before #137, Settings drops stt_prompt_file — a prompt-on run must stop, not measure without it."""
    monkeypatch.setattr(noise, "Settings", lambda **kwargs: SimpleNamespace(stt_model=kwargs["stt_model"]))
    with pytest.raises(noise.VoiceE2EError, match="#137"):
        noise.condition_settings("whisper-1", tmp_path / "prompt.txt")
    assert noise.condition_settings("whisper-1", None).stt_model == "whisper-1"


def test_clips_are_never_made_inside_the_repo() -> None:
    def runner(*_args, **_kwargs):
        raise AssertionError("ffmpeg must not run")

    with pytest.raises(noise.VoiceE2EError, match="레포 안"):
        noise.make_clips(noise.REPO_ROOT / "eval", ffmpeg="ffmpeg", runner=runner)


def test_make_clips_runs_fixed_ffmpeg_commands(tmp_path: Path) -> None:
    commands = []

    def runner(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"OggS")

    clips = noise.make_clips(tmp_path, ffmpeg="ffmpeg", runner=runner)
    assert [clip["name"] for clip in clips] == [name for name, *_ in noise.CLIPS]
    assert len(clips) == 7
    assert all(Path(c[-1]).parent == tmp_path.resolve() for c in commands)
    assert all("+bitexact" in c and "libopus" in c for c in commands)
    assert any("seed=1" in arg for arg in commands[2])
    assert "volume=0.005" in commands[-1]


def test_temp_clips_are_deleted_after_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    made = {}

    def clip_maker(work_dir: Path):
        made["dir"] = work_dir
        return _fake_clips(work_dir)

    noise.run(out_dir=tmp_path / "out", models=["whisper-1"], repeat=1, prices=PRICES, stt=ScriptedSTT({}),
              settings_for=_settings, clip_maker=clip_maker, run_id="t")
    assert not made["dir"].exists()
    assert noise.REPO_ROOT.resolve() not in made["dir"].resolve().parents


# ── short-answer recordings ───────────────────────────────────────────────────

JUDGE_ROWS = [
    {"id": "va-qty-01", "field": "quantity", "text": "세 단이요", "choices": None, "item_names": None,
     "expected_status": "ok", "expected_value": 3, "expected_unit": "단"},
    {"id": "va-mis-15", "field": "mismatch_choice", "text": "이만 천 원", "choices": [12000, 21000],
     "item_names": None, "expected_status": "ok", "expected_value": 21000, "expected_unit": None},
]


def _judge_file(tmp_path: Path) -> Path:
    path = tmp_path / "judge.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in JUDGE_ROWS) + "\n", encoding="utf-8")
    return path


def _recordings_dir(tmp_path: Path, names=("S1_va-qty-01.m4a", "S2_va-mis-15.m4a")) -> Path:
    folder = tmp_path / "answers"
    folder.mkdir(parents=True)
    for name in names:
        (folder / name).write_bytes(b"\x00\x00\x00\x18ftypM4A fake")
    return folder


def _fake_prepare(recordings, work_dir) -> None:
    for rec in recordings:
        rec["audio"], rec["seconds"], rec["remuxed"] = rec["path"].read_bytes(), 2.0, False


def _run_recordings(tmp_path: Path, answers: dict, *, delete: bool = True) -> dict:
    work_dir = tmp_path / "clips"
    work_dir.mkdir()
    return noise.run(out_dir=tmp_path / "out", models=["whisper-1"], repeat=2, work_dir=work_dir, prices=PRICES,
                     stt=ScriptedSTT(answers), settings_for=_settings, clip_maker=_fake_clips, run_id="t",
                     recordings_dir=_recordings_dir(tmp_path), recording_models=["whisper-1", "gpt-4o-transcribe"],
                     delete_recordings=delete, judge_set=_judge_file(tmp_path), recording_preparer=_fake_prepare)


def test_recordings_scored_with_expected_value_and_no_transcript_kept(tmp_path: Path) -> None:
    transcripts = ("세 단이요.", "이만 천 원 맞아요", "네 단이요.", "이만 원이요.")
    answers = dict(zip([("whisper-1", False, "S1_va-qty-01"), ("whisper-1", False, "S2_va-mis-15"),
                        ("gpt-4o-transcribe", False, "S1_va-qty-01"), ("gpt-4o-transcribe", False, "S2_va-mis-15")],
                       transcripts))
    result = _run_recordings(tmp_path, answers)
    whisper, four_o = result["recordings"]["conditions"]
    assert (whisper["model"], whisper["calls"], whisper["hit"], whisper["wrong_fill"]) == ("whisper-1", 4, 4, 0)
    assert four_o["model"] == "gpt-4o-transcribe"
    assert four_o["per_recording"]["S1_va-qty-01"] == {"runs": 2, "hit": 0, "wrong_fill": 2, "reasons": {}}  # 4단 ≠ 3단
    assert four_o["per_recording"]["S2_va-mis-15"]["hit"] == 0  # 20000 is not one of the two choices
    summary = (tmp_path / "out" / "t.summary.json").read_text(encoding="utf-8")
    report = (tmp_path / "out" / "t.report.md").read_text(encoding="utf-8")
    assert not any(text in summary or text in report for text in transcripts)
    assert "| S1_va-qty-01 | 3단 | 2/2 | 0/2 (틀린 값 2) |" in report
    assert not list((tmp_path / "answers").iterdir())  # deleted after a run without STT errors


def test_recordings_kept_when_stt_failed(tmp_path: Path) -> None:
    result = _run_recordings(tmp_path, {("whisper-1", False, "S1_va-qty-01"): "NETWORK"})
    assert result["recordings"]["conditions"][0]["stt_error"] == 2
    assert len(list((tmp_path / "answers").iterdir())) == 2


def test_recordings_run_with_prompt_off_and_count_in_cost(tmp_path: Path) -> None:
    stt = ScriptedSTT({})
    work_dir = tmp_path / "clips"
    work_dir.mkdir()
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text(PROMPT, encoding="utf-8")
    result = noise.run(out_dir=tmp_path / "out", models=["whisper-1"], prompt_file=prompt_file, repeat=1,
                       work_dir=work_dir, prices=PRICES, stt=stt, settings_for=_settings, clip_maker=_fake_clips,
                       run_id="t", recordings_dir=_recordings_dir(tmp_path), recording_models=["gpt-4o-transcribe"],
                       judge_set=_judge_file(tmp_path), recording_preparer=_fake_prepare)
    recording_calls = [c for c in stt.calls if c[2].startswith("S")]
    assert recording_calls and all(model == "gpt-4o-transcribe" and not on for model, on, _ in recording_calls)
    # clips 3s × 2 prompt conditions on whisper-1 + recordings 4s on 4o, 1 repeat
    assert result["cost_estimate"]["per_model_usd"] == {"whisper-1": 0.0006, "gpt-4o-transcribe": 0.0004}
    assert result["cost_estimate"]["calls"] == 4 + 2


def test_recording_names_and_ids_are_checked(tmp_path: Path) -> None:
    rows = JUDGE_ROWS
    with pytest.raises(noise.VoiceE2EError, match="가명_ID"):
        noise.load_recordings(_recordings_dir(tmp_path / "a", ("홍길동_va-qty-01.m4a",)), rows, 1000)
    with pytest.raises(noise.VoiceE2EError, match="판정 세트에 없는"):
        noise.load_recordings(_recordings_dir(tmp_path / "b", ("S1_va-qty-99.m4a",)), rows, 1000)
    with pytest.raises(noise.VoiceE2EError, match="크기"):
        noise.load_recordings(_recordings_dir(tmp_path / "c", ("S1_va-qty-01.m4a",)), rows, 5)
    with pytest.raises(noise.VoiceE2EError, match="레포 안"):
        noise.load_recordings(noise.REPO_ROOT / "eval", rows, 1000)
    recs = noise.load_recordings(_recordings_dir(tmp_path / "d"), rows, 1000)
    assert [(r["alias"], r["id"]) for r in recs] == [("S1", "va-qty-01"), ("S2", "va-mis-15")]


def test_3gp4_recording_is_remuxed_with_copy(tmp_path: Path) -> None:
    folder = tmp_path / "answers"
    folder.mkdir()
    (folder / "S1_va-qty-01.m4a").write_bytes(b"\x00\x00\x00\x18ftyp3gp4 android")
    (folder / "S2_va-mis-15.m4a").write_bytes(b"\x00\x00\x00\x18ftypM4A iphone")
    recs = noise.load_recordings(folder, JUDGE_ROWS, 1000)
    commands = []

    def runner(command, **_kwargs):
        commands.append(command)
        if "-c" in command:
            Path(command[-1]).write_bytes(b"remuxed")
        return SimpleNamespace(stdout="1.75\n")

    work = tmp_path / "work"
    work.mkdir()
    noise.prepare_recordings(recs, work, ffmpeg="ffmpeg", ffprobe="ffprobe", runner=runner)
    remux = [c for c in commands if "-c" in c]
    assert len(remux) == 1 and remux[0][remux[0].index("-c") + 1] == "copy"
    assert Path(remux[0][-1]).parent == work.resolve()
    assert (recs[0]["remuxed"], recs[0]["audio"], recs[0]["seconds"]) == (True, b"remuxed", 1.75)
    assert (recs[1]["remuxed"], recs[1]["audio"][8:11]) == (False, b"M4A")


def test_judge_recording_outcomes() -> None:
    qty, mis = JUDGE_ROWS
    assert noise.judge_recording(qty, "세 단", None, settings=BASE)["outcome"] == "hit"
    assert noise.judge_recording(qty, "네 단", None, settings=BASE)["outcome"] == "wrong_fill"
    assert noise.judge_recording(qty, "음", None, settings=BASE)["outcome"] == "miss"
    assert noise.judge_recording(qty, None, STTError(noise.EMPTY_TRANSCRIPT), settings=BASE)["outcome"] == "miss"
    assert noise.judge_recording(mis, "이만 천 원", None, settings=BASE)["value"] == 21000
    assert "transcript" not in noise.judge_recording(mis, "이만 천 원", None, settings=BASE)


def test_judge_recording_keeps_interpret_reason_code_only() -> None:
    qty, mis = JUDGE_ROWS
    assert noise.judge_recording(qty, "세 단", None, settings=BASE)["reason"] is None
    assert noise.judge_recording(qty, "음", None, settings=BASE)["reason"] == "no_answer"
    assert noise.judge_recording(mis, "이만 원이요.", None, settings=BASE)["reason"] == "not_in_choices"
    assert noise.judge_recording(qty, None, STTError(noise.EMPTY_TRANSCRIPT), settings=BASE)["reason"] == "stt_empty"
    network = STTError("STT request failed: APIConnectionError")
    assert noise.judge_recording(qty, None, network, settings=BASE)["reason"] is None  # stt_error, not a miss


def test_miss_reasons_by_model_and_recording_in_summary_and_report(tmp_path: Path) -> None:
    transcripts = ("세 단이요.", "이만 천 원 맞아요", "음", "이만 원이요.")
    answers = dict(zip([("whisper-1", False, "S1_va-qty-01"), ("whisper-1", False, "S2_va-mis-15"),
                        ("gpt-4o-transcribe", False, "S1_va-qty-01"), ("gpt-4o-transcribe", False, "S2_va-mis-15")],
                       transcripts))
    result = _run_recordings(tmp_path, answers)
    whisper, four_o = result["recordings"]["conditions"]
    assert whisper["miss_reasons"] == {}
    assert four_o["miss_reasons"] == {"no_answer": 2, "not_in_choices": 2}
    assert four_o["per_recording"]["S2_va-mis-15"]["reasons"] == {"not_in_choices": 2}
    report = (tmp_path / "out" / "t.report.md").read_text(encoding="utf-8")
    assert "| gpt-4o-transcribe | 4 | 0 | 4 | **0** | 0 | 0 | no_answer 2, not_in_choices 2 |" in report
    assert "| S2_va-mis-15 | 21000 | 2/2 | 0/2 [not_in_choices 2] |" in report
    summary = (tmp_path / "out" / "t.summary.json").read_text(encoding="utf-8")
    assert not any(text in summary or text in report for text in transcripts if len(text) > 1)


def test_counterparty_hints_go_the_server_way_and_a_refusal_passes(tmp_path: Path) -> None:
    """PM 10/21: the on condition sends counterparty_hints (no prompt file); a guard refusal counts as a pass."""
    seen = []

    def stt(audio, filename, mime, *, settings, counterparty_hints=()):
        seen.append(list(counterparty_hints))
        if not counterparty_hints:
            return "작은 집."  # hint off: a made-up name still fails
        if Path(filename).stem == "silence_1s":
            raise noise.STTRefused("recording has no speech")
        return "영수네 가게"  # a single listed name gets through both guards

    work_dir = tmp_path / "clips"
    work_dir.mkdir()
    result = noise.run(out_dir=tmp_path / "out", models=["gpt-4o-transcribe"], repeat=1, work_dir=work_dir,
                       prices=PRICES, stt=stt, settings_for=_settings, clip_maker=_fake_clips, run_id="t",
                       counterparty_hints=["영수네 가게", "김밥천하"])
    assert seen == [[], [], ["영수네 가게", "김밥천하"], ["영수네 가게", "김밥천하"]]
    on = next(row for row in result["conditions"] if row["prompt_on"])
    assert (on["pass_empty"], on["fail_filled"], on["stt_error"]) == (1, 1, 0)
    assert result["run"]["counterparty_hints"] == 2 and result["run"]["stt_prompt"]["chars"] == len("거래처: 영수네 가게, 김밥천하")
    report = (tmp_path / "out" / "t.report.md").read_text(encoding="utf-8")
    assert "거래처 힌트 2곳을 서버 경로" in report


def test_prompt_file_and_counterparty_hints_are_not_mixed(tmp_path: Path) -> None:
    with pytest.raises(noise.VoiceE2EError, match="따로"):
        noise.run(out_dir=tmp_path, models=["whisper-1"], prompt_file=tmp_path / "p.txt", prices=PRICES,
                  settings_for=_settings, clip_maker=_fake_clips, counterparty_hints=["가나상회"])
