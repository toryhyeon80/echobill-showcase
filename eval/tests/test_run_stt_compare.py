"""eval/run_stt_compare.py — cells, owner lists, snapping, budgets, pair classes, newly-wrong, RTZR errors.

STT and LLM are fakes (no API); recordings are tiny generated wav files.
"""
import io
import json
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import eval.run_stt_compare as cmp  # puts backend/ on sys.path before anything from app is used
from eval.providers.rtzr_stt import RtzrClient, rtzr_keywords

Settings = cmp.voice.Settings
STTError = cmp.STTError

GOLDEN = cmp.voice.load_dev_golden()
BY_TEXT = {record["utterance_text"]: record for record in GOLDEN.values()}
PRICES = {"cost_cap_usd": 1.0,
          "models": {"gpt-4o-mini": {"input_usd_per_1m": 0.15, "output_usd_per_1m": 0.60}},
          "stt_models": {"whisper-1": {"usd_per_minute": 0.006}}}
EMPTY = {"counterparty": None, "items": [], "total_amount": None, "credit": None}
NOW = datetime(2026, 10, 21, 10, 0, tzinfo=cmp.baseline.KST)
ENTRIES = [("g-dev-003", "S1"), ("g-dev-002", "S1")]


@pytest.fixture(autouse=True)
def _clean_git_tree(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cmp.baseline, "_git_state", lambda: {"git_commit": "abc1234", "git_dirty": False})


def _settings() -> Settings:
    return Settings(_env_file=None, app_env="development", llm_model="gpt-4o-mini", ko_numeral_normalize=True,
                    llm_postprocess=False, llm_temperature=0.0, llm_max_completion_tokens=800,
                    llm_timeout_seconds=5.0, llm_max_retries=0)


def _wav(seconds: float = 2.0) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * int(16000 * seconds))
    return buffer.getvalue()


def _setup(tmp_path: Path, entries=ENTRIES) -> dict:
    audio = tmp_path / "audio"
    audio.mkdir()
    lines = ["golden_id,speaker,file"]
    for golden_id, speaker in entries:
        name = f"{speaker}_{golden_id}.wav"
        (audio / name).write_bytes(_wav())
        lines.append(f"{golden_id},{speaker},{name}")
    (tmp_path / "manifest.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"audio_dir": audio, "manifest": tmp_path / "manifest.csv", "out_dir": tmp_path / "out",
            "results_dir": tmp_path / "results"}


class FakeSTT:
    """Golden sentence back; `garble` maps (provider, golden_id) → a different counterparty spelling."""

    def __init__(self, garble=None, fail=()):
        self.calls = []
        self.garble = garble or {}
        self.fail = fail

    def __call__(self, audio, filename, mime, *, counterparty_hints=(), settings=None):
        golden_id = filename.split("_", 1)[1].rsplit(".", 1)[0]
        self.calls.append((settings.stt_provider, golden_id, tuple(counterparty_hints)))
        if (settings.stt_provider, golden_id) in self.fail:
            raise STTError("STT request failed: Boom")
        text = GOLDEN[golden_id]["utterance_text"]
        swap = self.garble.get((settings.stt_provider, golden_id))
        return text.replace(swap[0], swap[1]) if swap else text


def fake_parser(utterance, **_kwargs):
    """Golden answer; an unknown sentence (garbled) gets the Golden answer with the heard counterparty."""
    record = BY_TEXT.get(utterance)
    if record is None:
        original = next(r for r in GOLDEN.values() if r["expected_json"] and r["expected_json"]["counterparty"]
                        and r["utterance_text"].split()[1:] == utterance.split()[1:])
        candidate = {**original["expected_json"], "counterparty": utterance.split()[0].removesuffix("에")}
    else:
        candidate = record["expected_json"] or EMPTY
    return SimpleNamespace(candidate=SimpleNamespace(model_dump=lambda mode=None: candidate), model="gpt-4o-mini",
                           processing_time_ms=100, prompt_tokens=500, completion_tokens=60,
                           response_model="gpt-4o-mini-2024-07-18", system_fingerprint="fp_test")


def _run(paths, *, stt=None, vendors=("openai", "clova"), repeat=1, **kwargs):
    return cmp.run(**paths, vendors=list(vendors), conditions=["plain", "hint"], repeat=repeat, stt=stt or FakeSTT(),
                   parser=fake_parser, prices=PRICES, settings=_settings(), run_id="cmp-test", now=lambda: NOW,
                   **kwargs)


# ── owner list · snapping ──


def test_owner_list_holds_own_name_and_fits_hint_limits():
    names = cmp.owner_list("g-dev-003", GOLDEN)
    assert "대박마트" in names
    assert len(names) <= cmp.HINT_NAME_LIMIT
    assert len(", ".join(names)) <= cmp.MAX_HINT_CHARS
    assert names == cmp.owner_list("g-dev-003", GOLDEN)  # deterministic


@pytest.mark.parametrize("heard,expected", [
    ("대박마트", "대박마트"), ("대박 마트", "대박마트"), ("대밖마트", "대박마트"),
    ("완전히다른가게", "완전히다른가게"), (None, None),
])
def test_snap_counterparty(heard, expected):
    assert cmp.snap_counterparty(heard, ["대박마트", "영수네 가게"]) == expected


def test_snap_leaves_ties_alone():
    assert cmp.snap_counterparty("가나다", ["가나라", "가나마"]) == "가나다"


# ── budget ──


def test_budget_refuses_clova_over_a_day():
    ledger = [{"vendor": "clova", "at": "2026-10-21T09:00:00+09:00", "seconds": 3500}]
    with pytest.raises(cmp.VoiceE2EError, match="clova"):
        cmp.check_budget({"clova": 200}, ledger, NOW)
    cmp.check_budget({"clova": 200}, [{**ledger[0], "at": "2026-10-20T09:00:00+09:00"}], NOW)


def test_usage_seconds_come_from_the_converted_audio_not_the_header(tmp_path, monkeypatch):
    # iPhone m4a headers read as ~2 s (#160); a piped wav has no frame count — the byte length is the truth
    path = tmp_path / "S1_g-dev-003.m4a"
    path.write_bytes(b"fake")
    monkeypatch.setattr(cmp, "to_wav", lambda audio, *, timeout: b"h" * 44 + b"\0" * (32_000 * 7))
    assert cmp.usage_seconds({"path": path, "file": path.name, "seconds": 2.05}, 5.0) == 7.0


def test_clova_is_billed_in_15_second_units():
    assert cmp.billed_seconds("clova", 2.0) == 15.0
    assert cmp.billed_seconds("clova", 15.0) == 15.0
    assert cmp.billed_seconds("clova", 15.1) == 30.0
    assert cmp.billed_seconds("openai", 2.0) == 2.0


def test_rtzr_is_billed_at_least_10_seconds_per_file():
    assert cmp.billed_seconds("rtzr", 2.0) == 10
    assert cmp.billed_seconds("rtzr", 12.5) == 12.5


@pytest.mark.parametrize("repeat,words", [(2, "2회, 2번 모두 틀림"), (3, "3회, 3번 중 2번 이상 틀림"), (1, "1회, 1번 모두 틀림")])
def test_newly_wrong_rule_in_words(repeat, words):
    assert words in cmp.newly_wrong_rule(repeat)


def test_budget_counts_billed_clova_seconds_before_running(tmp_path):
    # 2 recordings × 2 s, plain+hint × 1 = 4 calls → 60 billed seconds (not 8); 3550 s spent today → refused
    paths = _setup(tmp_path)
    paths["out_dir"].mkdir()
    (paths["out_dir"] / "usage.jsonl").write_text(json.dumps(
        {"vendor": "clova", "at": "2026-10-21T09:00:00+09:00", "seconds": 3550}) + "\n", encoding="utf-8")
    with pytest.raises(cmp.VoiceE2EError, match="3550초 \\+ 이번 60초"):
        _run(paths, vendors=("clova",))


def test_budget_refuses_rtzr_over_80_minutes():
    with pytest.raises(cmp.VoiceE2EError, match="rtzr"):
        cmp.check_budget({"rtzr": 81 * 60}, [], NOW)


# ── whole run ──


def test_run_fills_cells_pairs_usage_and_sends_hints_only_in_hint_condition(tmp_path):
    paths = _setup(tmp_path)
    stt = FakeSTT()
    summary = _run(paths, stt=stt)
    assert set(summary["cells"]) == {f"{v}/{c}" for v in ("openai", "clova") for c in cmp.CELL_LABELS}
    assert summary["cells"]["openai/plain"]["correct"]["rate"] == 1.0
    assert summary["cells"]["clova/hint"]["usage_seconds"] == 4.0
    plain_hints = {hints for provider, gid, hints in stt.calls[:4]}
    assert plain_hints == {()}
    assert all(hints for *_, hints in stt.calls[4:])
    pair = summary["pairs"]["openai+clova/plain"]["classes"]
    assert pair["일치·맞음"] == 2
    usage = [json.loads(line) for line in (paths["out_dir"] / "usage.jsonl").read_text().splitlines()]
    assert len(usage) == 8 and {row["vendor"] for row in usage} == {"openai", "clova"}
    assert {row["seconds"] for row in usage if row["vendor"] == "clova"} == {15.0}
    assert {row["seconds"] for row in usage if row["vendor"] == "openai"} == {2.0}
    assert summary["cells"]["clova/plain"]["billed_seconds"] == 30.0
    report = (paths["results_dir"] / "cmp-test.report.md").read_text(encoding="utf-8")
    assert "- OpenAI 음성 인식 모델: whisper-1" in report  # local default; production uses STT_MODEL
    for record in GOLDEN.values():  # no sentence content in the report
        assert record["utterance_text"] not in report


def test_list_matching_fixes_a_misheard_counterparty_and_pairs_disagree(tmp_path):
    paths = _setup(tmp_path, [("g-dev-003", "S1")])
    stt = FakeSTT(garble={("clova", "g-dev-003"): ("대박마트", "대밖마트")})
    summary = _run(paths, stt=stt)
    assert summary["cells"]["clova/hint"]["correct"]["rate"] == 0.0
    assert summary["cells"]["clova/hint_list"]["correct"]["rate"] == 1.0
    hint = summary["pairs"]["openai+clova/hint"]
    assert hint["classes"]["불일치·openai만 맞음"] == 1 and hint["classes"]["불일치·clova만 맞음"] == 0
    assert hint["base_wrong_when_differ"] == {"wrong": 0, "differ": 1, "rate": 0.0}
    assert summary["pairs"]["openai+clova/hint_list"]["classes"]["일치·맞음"] == 1
    assert summary["cells"]["clova/hint_list"]["snap"] == {"changed": 1, "changed_wrong": 0, "changed_wrong_ids": []}
    report = (paths["results_dir"] / "cmp-test.report.md").read_text(encoding="utf-8")
    assert "불일치·clova만 맞음" in report and "다를 때 기본 업체 틀림" in report


def test_base_vendor_wrong_when_they_differ(tmp_path):
    paths = _setup(tmp_path, [("g-dev-003", "S1")])
    summary = _run(paths, stt=FakeSTT(garble={("openai", "g-dev-003"): ("대박마트", "대밖마트")}))
    plain = summary["pairs"]["openai+clova/plain"]
    assert plain["classes"]["불일치·clova만 맞음"] == 1
    assert plain["base_wrong_when_differ"]["rate"] == 1.0


def test_every_fourth_recording_leaves_its_own_name_out():
    with_name = sorted(gid for gid, r in GOLDEN.items() if (r["expected_json"] or {}).get("counterparty"))[:8]
    unlisted = cmp.unlisted_ids(set(with_name), GOLDEN)
    assert unlisted == {with_name[3], with_name[7]}
    own = GOLDEN[with_name[3]]["expected_json"]["counterparty"]
    assert own not in cmp.owner_list(with_name[3], GOLDEN, include_own=False)
    assert own in cmp.owner_list(with_name[3], GOLDEN)


def test_list_matching_that_turns_a_new_counterparty_into_a_saved_one_is_counted(tmp_path, monkeypatch):
    # 대박마트 is new (not on the list) but 대박마토 is saved → list matching changes it to the wrong name
    monkeypatch.setattr(cmp, "unlisted_ids", lambda ids, golden: {"g-dev-003"})
    monkeypatch.setattr(cmp, "owner_list", lambda gid, golden, include_own=True: ["대박마토", "영수네 가게"])
    summary = _run(_setup(tmp_path, [("g-dev-003", "S1")]))
    cell = summary["cells"]["openai/hint_list"]
    assert cell["snap"] == {"changed": 1, "changed_wrong": 1, "changed_wrong_ids": ["g-dev-003@S1"]}
    assert cell["by_owner_list"]["unlisted"] == {"correct": 0, "total": 1, "rate": 0.0}
    assert summary["cells"]["openai/hint"]["by_owner_list"]["unlisted"]["rate"] == 1.0


def test_newly_wrong_needs_two_of_three(tmp_path):
    paths = _setup(tmp_path, [("g-dev-003", "S1")])
    stt = FakeSTT(garble={("clova", "g-dev-003"): ("대박마트", "대밖마트")})
    summary = _run(paths, stt=stt, repeat=3)
    assert summary["newly_wrong"]["clova/plain"] == ["g-dev-003@S1"]
    report = (paths["results_dir"] / "cmp-test.report.md").read_text(encoding="utf-8")
    assert "음성 인식은 3회, 3번 중 2번 이상 틀림 기준" in report
    assert summary["newly_wrong"]["clova/hint_list"] == []


def test_stt_failure_counts_as_wrong_and_keeps_recordings(tmp_path):
    paths = _setup(tmp_path)
    summary = _run(paths, stt=FakeSTT(fail=(("clova", "g-dev-002"),)), delete_after=True)
    cell = summary["cells"]["clova/plain"]
    assert cell["stt_failed"] == 1 and cell["correct"]["correct"] == 1 and cell["correct"]["total"] == 2
    assert len(list(paths["audio_dir"].iterdir())) == 2


def test_conversion_time_is_recorded_through_the_production_path(tmp_path, monkeypatch):
    calls = []

    def fake_to_wav(audio, *, timeout):
        calls.append(timeout)
        return audio

    monkeypatch.setattr(cmp, "to_wav", fake_to_wav)

    def stt(audio, filename, mime, *, counterparty_hints=(), settings=None):
        # like stt._with_deadline: CLOVA converts on another worker thread, not the caller's
        if settings.stt_provider == "clova":
            with ThreadPoolExecutor(max_workers=1) as worker:
                worker.submit(cmp.stt_module.to_wav, audio, timeout=1.0).result()
        return GOLDEN[filename.split("_", 1)[1].rsplit(".", 1)[0]]["utterance_text"]

    summary = _run(_setup(tmp_path), stt=stt)
    assert summary["cells"]["clova/plain"]["latency_ms"]["convert"]["count"] == 2
    assert summary["cells"]["openai/plain"]["latency_ms"]["convert"]["count"] == 0
    assert cmp.stt_module.to_wav is not cmp._timed_to_wav  # restored


# ── RTZR adapter ──


def _rtzr(handler) -> RtzrClient:
    return RtzrClient("id", "secret", http=httpx.Client(transport=httpx.MockTransport(handler)),
                      clock=lambda: 1000.0, sleep=lambda _s: None)


def test_rtzr_keywords_and_polling():
    seen = {}
    polls = iter(["transcribing", "completed"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/authenticate"):
            return httpx.Response(200, json={"access_token": "t", "expire_at": 1000 + 6 * 3600})
        if request.method == "POST":
            seen["body"] = request.content
            return httpx.Response(200, json={"id": "job1"})
        status = next(polls)
        body = {"status": status}
        if status == "completed":
            body["results"] = {"utterances": [{"msg": "대박마트에"}, {"msg": "계란 두 판"}]}
        return httpx.Response(200, json=body)

    client = _rtzr(handler)
    job = client.submit(_wav(), "a.wav", "audio/wav", counterparty_hints=["대박마트", "가"])
    assert "대박마트" in seen["body"].decode("utf-8", "ignore") and job == "job1"
    assert client.poll(job, deadline_seconds=60) == "대박마트에 계란 두 판"
    assert rtzr_keywords([" 대박  마트 ", "가", "대박 마트"]) == ["대박 마트"]


@pytest.mark.parametrize("status", [429, 500])
def test_rtzr_vendor_errors_become_stt_error(status):
    def handler(request):
        if request.url.path.endswith("/authenticate"):
            return httpx.Response(200, json={"access_token": "t", "expire_at": 99999})
        return httpx.Response(status, json={"code": "A0001"})

    with pytest.raises(STTError) as caught:
        _rtzr(handler).submit(_wav(), "a.wav", "audio/wav")
    assert caught.value.status_code == status


def test_rtzr_timeout_and_failed_job_become_stt_error():
    def timeout(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(STTError):
        _rtzr(timeout).submit(_wav(), "a.wav", "audio/wav")

    def failed(request):
        if request.url.path.endswith("/authenticate"):
            return httpx.Response(200, json={"access_token": "t", "expire_at": 99999})
        return httpx.Response(200, json={"status": "failed", "error": {"code": "E500"}})

    with pytest.raises(STTError, match="failed"):
        _rtzr(failed).poll("job", deadline_seconds=60)


# ── 단위·외상 (PM 10/21) ──


def _variant_parser(utterance, **kwargs):
    """Golden answer, except 키로 → unit "키로" and a lost credit word → credit False (what CLOVA caused on 10/21)."""
    if "키로" in utterance:
        record = GOLDEN["g-dev-008"]
        items = [{**record["expected_json"]["items"][0], "unit": "키로"}]
        candidate = {**record["expected_json"], "items": items}
    elif "달 안 와" in utterance or ("계란" in utterance and "현금" in utterance):
        candidate = {**GOLDEN["g-dev-003"]["expected_json"], "credit": False}
    else:
        return fake_parser(utterance, **kwargs)
    return SimpleNamespace(candidate=SimpleNamespace(model_dump=lambda mode=None: candidate), model="gpt-4o-mini",
                           processing_time_ms=100, prompt_tokens=500, completion_tokens=60,
                           response_model="gpt-4o-mini-2024-07-18", system_fingerprint="fp_test")


def test_unit_variants_and_lost_credit_words_are_counted(tmp_path):
    paths = _setup(tmp_path, [("g-dev-003", "S1"), ("g-dev-008", "S1")])
    # clova loses the credit word (no payment word left → the rules ask again, nothing saved);
    # openai hears it as 현금 (a 200 with credit=False — saved as cash, the dangerous case)
    stt = FakeSTT(garble={("clova", "g-dev-003"): ("달아놔", "달 안 와"), ("clova", "g-dev-008"): ("일킬로", "1키로"),
                          ("openai", "g-dev-003"): ("달아놔", "현금")})
    summary = cmp.run(**paths, vendors=["openai", "clova"], conditions=["plain"], repeat=1, stt=stt,
                      parser=_variant_parser, prices=PRICES, settings=_settings(), run_id="cmp-test",
                      now=lambda: NOW)
    clova, openai = summary["unit_and_credit"]["clova/plain"], summary["unit_and_credit"]["openai/plain"]
    assert clova["unit_as_written"] == {"correct": 1, "total": 2, "rate": 0.5}  # 판 right, 키로 ≠ 킬로
    assert clova["unit_variants_same"]["correct"] == 2
    assert clova["credit_words_kept"] == {"correct": 0, "total": 1, "rate": 0.0}
    assert clova["credit_field"]["correct"] == 0 and clova["credit_saved_as_cash"] == 0
    assert openai["credit_words_kept"]["rate"] == 0.0 and openai["credit_saved_as_cash"] == 1
    report = (paths["results_dir"] / "cmp-test.report.md").read_text(encoding="utf-8")
    assert "단위 킬로/키로 같게" in report and "외상 표현 보존" in report
    assert "달 안 와" not in report and "대박마트" not in report  # numbers only


def test_unit_and_credit_can_be_added_to_a_finished_run(tmp_path):
    paths = _setup(tmp_path, [("g-dev-003", "S1")])
    summary = _run(paths, vendors=("openai",))
    report_path = paths["results_dir"] / "cmp-test.report.md"
    data_path = paths["results_dir"] / "cmp-test.summary.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    del data["unit_and_credit"]  # a run made before the section existed
    data_path.write_text(json.dumps(data), encoding="utf-8")
    report_path.write_text(cmp.render_report(data), encoding="utf-8")
    assert "외상 표현 보존" not in report_path.read_text(encoding="utf-8")
    again = cmp.add_unit_and_credit("cmp-test", paths["out_dir"], results_dir=paths["results_dir"])
    assert again["unit_and_credit"] == summary["unit_and_credit"]
    assert set(again["unit_and_credit"]) == {"openai/plain", "openai/hint", "openai/hint_list"}
    assert "외상 표현 보존" in report_path.read_text(encoding="utf-8")


# ── 측정 합치기 (CLOVA 하루 1시간 → 오늘 1회 + 내일 1회) ──


def test_two_single_runs_merge_into_the_same_table_as_one_double_run(tmp_path):
    garble = {("clova", "g-dev-003"): ("대박마트", "대밖마트")}

    def one(run_id, repeat, base):
        base.mkdir()
        paths = _setup(base, [("g-dev-003", "S1"), ("g-dev-002", "S1")])
        return paths, cmp.run(**paths, vendors=["openai", "clova"], conditions=["plain", "hint"], repeat=repeat,
                               stt=FakeSTT(garble=garble), parser=fake_parser, prices=PRICES, settings=_settings(),
                               run_id=run_id, now=lambda: NOW)

    _, double = one("cmp-double", 2, tmp_path / "a")
    paths, _ = one("cmp-day1", 1, tmp_path / "b")
    cmp.run(**paths, vendors=["openai", "clova"], conditions=["plain", "hint"], repeat=1, stt=FakeSTT(garble=garble),
            parser=fake_parser, prices=PRICES, settings=_settings(), run_id="cmp-day2", now=lambda: NOW)
    merged = cmp.merge_runs(["cmp-day1", "cmp-day2"], paths["out_dir"], results_dir=paths["results_dir"],
                            merged_id="cmp-merged", prices=PRICES)
    assert merged["repeat"] == 2 and merged["recordings"] == 2
    for name, cell in double["cells"].items():
        assert merged["cells"][name]["correct"] == cell["correct"], name
        assert merged["cells"][name]["fields"] == cell["fields"], name
        assert merged["cells"][name]["usage_seconds"] == cell["usage_seconds"], name
        assert merged["cells"][name]["latency_ms"]["stt"]["p50"] is not None  # from calls.jsonl
    assert merged["newly_wrong"] == double["newly_wrong"]
    assert merged["newly_wrong"]["clova/plain"] == ["g-dev-003@S1"]
    assert {k: p["classes"] for k, p in merged["pairs"].items()} == {k: p["classes"] for k, p in double["pairs"].items()}
    assert merged["unit_and_credit"] == double["unit_and_credit"]
    report = (paths["results_dir"] / "cmp-merged.report.md").read_text(encoding="utf-8")
    assert "합친 측정: cmp-day1, cmp-day2" in report and "음성 인식은 2회, 2번 모두 틀림 기준" in report
