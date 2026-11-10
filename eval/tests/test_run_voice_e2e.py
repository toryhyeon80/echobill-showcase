"""eval/run_voice_e2e.py — path·manifest rules, no sentence content in console/logs/report, deletion, cost gates.

STT and LLM are fakes; the text baseline is the committed DL-11 raw output re-scored offline (no API).
"""
import logging
import os
import struct
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import eval.run_voice_e2e as voice
from app.config import Settings
from app.services.stt import STTError

MARK = " 확인용표식QZ"
GOLDEN = voice.load_dev_golden()
BY_TEXT = {record["utterance_text"]: record for record in GOLDEN.values()}
PRICES = {"cost_cap_usd": 1.0,
          "models": {"gpt-4o-mini": {"input_usd_per_1m": 0.15, "output_usd_per_1m": 0.60}},
          "stt_models": {"whisper-1": {"usd_per_minute": 0.006, "checked_at": "test-only"}}}
EMPTY_CANDIDATE = {"counterparty": None, "items": [], "total_amount": None, "credit": None}


@pytest.fixture(autouse=True)
def _clean_git_tree(monkeypatch: pytest.MonkeyPatch):
    """Runs refuse a dirty tree; tests pin a clean state so local edits do not matter."""
    monkeypatch.setattr(voice.baseline, "_git_state", lambda: {"git_commit": "abc1234", "git_dirty": False})


def _settings(**overrides) -> Settings:
    """Same conditions as the DL-11 mini text baseline, without .env or keys."""
    values = dict(llm_model="gpt-4o-mini", ko_numeral_normalize=True, llm_postprocess=False, llm_temperature=0.0,
                  llm_max_completion_tokens=800, llm_timeout_seconds=5.0, llm_max_retries=0)
    return Settings(_env_file=None, app_env="development", **{**values, **overrides})


class FakeAPIError(Exception):
    """Stands in for an OpenAI error carrying an HTTP status."""
    status_code = 503


class FakeSTT:
    """Returns the Golden sentence plus a marker; files named in `fail` raise STTError like stt.py does."""

    def __init__(self, fail: tuple[str, ...] = ()):
        self.calls: list[str] = []
        self.fail = fail

    def __call__(self, audio: bytes, filename: str, mime: str, *, settings=None) -> str:
        self.calls.append(filename)
        if filename in self.fail:
            try:
                raise FakeAPIError("upstream said: " + GOLDEN["g-dev-003"]["utterance_text"])
            except FakeAPIError as exc:
                raise STTError("STT request failed: FakeAPIError") from exc
        golden_id = filename.split("_", 1)[1].rsplit(".", 1)[0]
        return GOLDEN[golden_id]["utterance_text"] + MARK


def fake_parser(utterance: str, **_kwargs):
    """Perfect structuring: the Golden answer for the sentence the fake STT returned."""
    record = BY_TEXT[utterance.removesuffix(MARK)]
    candidate = record["expected_json"] or EMPTY_CANDIDATE
    return SimpleNamespace(candidate=SimpleNamespace(model_dump=lambda mode=None: candidate), model="gpt-4o-mini",
                           processing_time_ms=100, prompt_tokens=500, completion_tokens=60,
                           response_model="gpt-4o-mini-2024-07-18", system_fingerprint="fp_test")


def _setup(tmp_path: Path, entries: list[tuple[str, str]], ext: str = ".m4a", data: bytes = b"fake-audio") -> dict:
    """Audio dir with one file per (golden_id, speaker), a manifest, and out/results dirs."""
    audio = tmp_path / "audio"
    audio.mkdir(parents=True)
    lines = ["golden_id,speaker,file"]
    for golden_id, speaker in entries:
        name = f"{speaker}_{golden_id}{ext}"
        (audio / name).write_bytes(data)
        lines.append(f"{golden_id},{speaker},{name}")
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"audio_dir": audio, "manifest": manifest, "out_dir": tmp_path / "out", "results_dir": tmp_path / "results"}


def _run(paths: dict, *, stt=None, prices=PRICES, settings=None, **kwargs) -> dict:
    return voice.run(**paths, stt=stt or FakeSTT(), parser=fake_parser, prices=prices,
                     settings=settings or _settings(), run_id="voice-test", **kwargs)


ENTRIES = [("g-dev-003", "S1"), ("g-dev-001", "S1"), ("g-dev-003", "S2"), ("g-dev-037", "S2")]


# ── paths ──


@pytest.mark.parametrize("which", ["audio_dir", "out_dir"])
def test_paths_inside_the_repo_are_refused(tmp_path: Path, which: str):
    paths = _setup(tmp_path, ENTRIES[:1])
    paths[which] = voice.REPO_ROOT / "eval"
    with pytest.raises(voice.VoiceE2EError, match="레포 안"):
        _run(paths)


def test_relative_paths_are_refused(tmp_path: Path):
    paths = _setup(tmp_path, ENTRIES[:1])
    paths["out_dir"] = Path("voice-out")
    with pytest.raises(voice.VoiceE2EError, match="절대경로"):
        _run(paths)


def _link_dir(link: Path, target: Path) -> None:
    """Directory symlink, or a junction on Windows without symlink rights; skip when neither works."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        pass
    if sys.platform == "win32":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=False, capture_output=True)
        if done.returncode == 0:
            return
    pytest.skip("cannot create a directory link here")


@pytest.mark.parametrize("which", ["audio_dir", "out_dir"])
def test_a_link_outside_pointing_into_the_repo_is_refused(tmp_path: Path, which: str):
    paths = _setup(tmp_path, ENTRIES[:1])
    link = tmp_path / "looks-outside"
    _link_dir(link, voice.REPO_ROOT / "eval")
    paths[which] = link
    with pytest.raises(voice.VoiceE2EError, match="레포 안"):
        _run(paths)


# ── manifest · files ──


def test_files_not_in_the_manifest_are_never_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = _setup(tmp_path, ENTRIES[:2])
    extra = paths["audio_dir"] / "S1_g-dev-002.m4a"
    extra.write_bytes(b"not listed")
    read: list[str] = []
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: read.append(self.name) or original(self))
    stt = FakeSTT()

    _run(paths, stt=stt, delete_after=True)

    assert extra.name not in read and extra.name not in stt.calls
    assert sorted(stt.calls) == ["S1_g-dev-001.m4a", "S1_g-dev-003.m4a"]
    assert extra.exists()


def test_unsupported_format_is_refused(tmp_path: Path):
    paths = _setup(tmp_path, ENTRIES[:1], ext=".flac")
    with pytest.raises(voice.VoiceE2EError, match="허용 형식"):
        _run(paths)


@pytest.mark.parametrize("data", [b"", b"x" * 11])
def test_empty_or_oversized_file_is_refused_before_stt(tmp_path: Path, data: bytes):
    paths = _setup(tmp_path, ENTRIES[:1], data=data)
    stt = FakeSTT()
    with pytest.raises(voice.VoiceE2EError, match="STT_MAX_BYTES"):
        _run(paths, stt=stt, settings=_settings(stt_max_bytes=10))
    assert stt.calls == []


@pytest.mark.parametrize("speaker", ["Kim", "s1", "S1a", "홍길동", "KSH", "S"])
def test_speaker_must_be_an_alias(tmp_path: Path, speaker: str):
    paths = _setup(tmp_path, [("g-dev-003", speaker)])
    with pytest.raises(voice.VoiceE2EError, match="가명만"):
        _run(paths)


def test_file_name_must_be_speaker_underscore_golden_id(tmp_path: Path):
    paths = _setup(tmp_path, ENTRIES[:1])
    paths["manifest"].write_text("golden_id,speaker,file\ng-dev-003,S1,S1_g-dev-002.m4a\n", encoding="utf-8")
    with pytest.raises(voice.VoiceE2EError, match="파일명"):
        _run(paths)


def test_only_dev_ids_are_accepted(tmp_path: Path):
    paths = _setup(tmp_path, [("g-test-003", "S1")])
    with pytest.raises(voice.VoiceE2EError, match="Dev ID"):
        _run(paths)
    paths = _setup(tmp_path / "b", [("g-dev-999", "S1")])
    with pytest.raises(voice.VoiceE2EError, match="Dev Golden에 없는"):
        _run(paths)


# ── no sentence content ──


def _sentences(entries) -> list[str]:
    """Utterances, counterparties and the STT marker that must never reach console, logs or results."""
    texts = [MARK.strip()]
    for golden_id, _ in entries:
        record = GOLDEN[golden_id]
        texts.append(record["utterance_text"])
        if record["expected_json"] and record["expected_json"]["counterparty"]:
            texts.append(record["expected_json"]["counterparty"])
    return texts


def test_console_and_logs_carry_no_sentence_content(tmp_path: Path, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    paths = _setup(tmp_path, ENTRIES)
    _run(paths, stt=FakeSTT(fail=("S2_g-dev-003.m4a",)))

    shown = capsys.readouterr().out + caplog.text
    assert "[STT] g-dev-003 S2 길이 미확인 실패 FakeAPIError status=503" in shown
    assert "[STT] g-dev-003 S1 길이 미확인 성공" in shown
    for text in _sentences(ENTRIES):
        assert text not in shown


def test_results_hold_numbers_only_and_transcripts_stay_in_out_dir(tmp_path: Path):
    paths = _setup(tmp_path, ENTRIES)
    _run(paths)

    results = sorted(p.name for p in paths["results_dir"].iterdir())
    assert results == ["voice-test.report.md", "voice-test.summary.json"]
    written = "".join((paths["results_dir"] / name).read_text(encoding="utf-8") for name in results)
    for text in _sentences(ENTRIES):
        assert text not in written
    transcripts = (paths["out_dir"] / "voice-test.transcripts.jsonl").read_text(encoding="utf-8")
    assert MARK.strip() in transcripts
    # Audio is never copied: the audio dir holds exactly the original recordings.
    assert sorted(p.name for p in paths["audio_dir"].iterdir()) == sorted(f"{s}_{g}.m4a" for g, s in ENTRIES)


# ── deletion ──


def test_recordings_are_kept_by_default(tmp_path: Path, capsys):
    paths = _setup(tmp_path, ENTRIES)
    _run(paths)
    assert len(list(paths["audio_dir"].iterdir())) == 4
    assert "[삭제 남음] 녹음 4건" in capsys.readouterr().out


def test_delete_after_removes_only_manifest_files_after_success(tmp_path: Path, capsys):
    paths = _setup(tmp_path, ENTRIES)
    keep = paths["audio_dir"] / "notes.txt"
    keep.write_text("x", encoding="utf-8")
    _run(paths, delete_after=True)
    assert [p.name for p in paths["audio_dir"].iterdir()] == ["notes.txt"]
    assert "[삭제] 녹음 4건 삭제" in capsys.readouterr().out


def test_delete_after_keeps_recordings_when_stt_failed(tmp_path: Path, capsys):
    paths = _setup(tmp_path, ENTRIES)
    _run(paths, stt=FakeSTT(fail=("S1_g-dev-001.m4a",)), delete_after=True)
    assert len(list(paths["audio_dir"].iterdir())) == 4
    assert "STT 실패가 있어 지우지 않음" in capsys.readouterr().out


def test_delete_after_keeps_recordings_when_the_run_breaks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = _setup(tmp_path, ENTRIES)

    def broken(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(voice.baseline, "run_baseline", broken)
    with pytest.raises(RuntimeError):
        _run(paths, delete_after=True)
    assert len(list(paths["audio_dir"].iterdir())) == 4


# ── cost · git gates ──


def test_over_the_cost_cap_nothing_runs(tmp_path: Path, capsys):
    paths = _setup(tmp_path, ENTRIES)
    stt = FakeSTT()
    with pytest.raises(voice.VoiceE2EError, match="상한"):
        _run(paths, stt=stt, prices={**PRICES, "cost_cap_usd": 0.001})
    assert stt.calls == []
    assert "[예상 비용·실행 전]" in capsys.readouterr().out


@pytest.mark.parametrize("stt_models", [{"whisper-1": {"usd_per_minute": None}}, {}, None])
def test_blank_whisper_price_refuses_the_run(tmp_path: Path, stt_models):
    paths = _setup(tmp_path, ENTRIES)
    stt = FakeSTT()
    with pytest.raises(voice.VoiceE2EError, match="공식 가격표 확인 필요"):
        _run(paths, stt=stt, prices={**PRICES, "stt_models": stt_models})
    assert stt.calls == []


def test_price_file_has_the_checked_whisper_price():
    """Whisper $0.006/min checked on the OpenAI pricing page 2026-10-13; models stays LLM-only."""
    prices = voice.baseline.load_prices()
    whisper = prices["stt_models"]["whisper-1"]
    assert (whisper["usd_per_minute"], whisper["checked_at"]) == (0.006, "2026-10-13")
    assert whisper["source"].startswith("https://")
    assert "whisper-1" not in prices["models"]


# 선정 20건 (Dev, 유형 분포 기준) — 팀원 4명이 같은 20건을 읽는 구성
PLANNED_IDS = ["g-dev-" + n for n in "003 001 012 017 002 022 036 029 016 058 050 030 037 021 011 004 053 051 059 054".split()]


def _m4a(seconds: float) -> bytes:
    """Minimal m4a bytes whose mvhd header says `seconds` long (timescale 1000)."""
    mvhd = b"mvhd" + b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, 1000, round(seconds * 1000))
    return b"\x00\x00\x00\x6c" + mvhd + b"\x00" * 80


def test_planned_run_estimates_about_8_cents_of_whisper(tmp_path: Path, capsys):
    """4 speakers × 20 sentences × 10 s = 800 s = 13.3 min × $0.006 (price file) = $0.08 before any STT call."""
    paths = _setup(tmp_path, [(g, s) for s in ("S1", "S2", "S3", "S4") for g in PLANNED_IDS], data=_m4a(10))
    stt = FakeSTT()
    summary = _run(paths, stt=stt, prices=voice.baseline.load_prices())

    assert summary["cost"]["estimate_upper"]["stt_usd"] == 0.08
    out = capsys.readouterr().out
    assert out.index("[예상 비용·실행 전] STT $0.0800") < out.index("[STT]")
    assert summary["cost"]["stt_usd_by_length"] == 0.08
    assert len(stt.calls) == 80


def test_uncommitted_changes_refuse_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(voice.baseline, "_git_state", lambda: {"git_commit": "abc1234", "git_dirty": True})
    paths = _setup(tmp_path, ENTRIES)
    stt = FakeSTT()
    with pytest.raises(voice.VoiceE2EError, match="커밋 안 된 변경"):
        _run(paths, stt=stt)
    assert stt.calls == []


def test_conditions_must_match_the_text_baseline(tmp_path: Path):
    paths = _setup(tmp_path, ENTRIES[:1])
    with pytest.raises(voice.VoiceE2EError, match="llm_postprocess"):
        _run(paths, settings=_settings(llm_postprocess=True))


# ── report values ──


def test_gap_exclude_reports_both_values_and_a_separate_row(tmp_path: Path):
    paths = _setup(tmp_path, ENTRIES)
    summary = _run(paths, gap_exclude=["g-dev-037"])

    excluded = summary["e2e"]["gap_excluded"]
    included = summary["e2e"]["all_included"]
    key = "confirmation_appropriate"
    # 037 (추가 확인) is one recording × 3 runs in voice and text alike.
    assert included["voice"][key]["total"] - excluded["voice"][key]["total"] == 3
    assert included["text"][key]["total"] - excluded["text"][key]["total"] == 3
    assert [row["golden_id"] for row in summary["excluded_rows"]] == ["g-dev-037"]
    report = (paths["results_dir"] / "voice-test.report.md").read_text(encoding="utf-8")
    assert "하락 폭 계산 (g-dev-037 빼고)" in report and "전체 포함" in report
    assert "하락 폭에서 뺀 ID (별도 표시)" in report


class PromptSettings(Settings):
    """Settings with the STT_PROMPT_FILE field the backend STT prompt PR adds."""
    stt_prompt_file: str = ""


def _prompt_settings(prompt_file: Path) -> PromptSettings:
    base = _settings()
    return PromptSettings(_env_file=None, app_env="development", stt_prompt_file=str(prompt_file),
                          **{k: getattr(base, k) for k in ("llm_model", "ko_numeral_normalize", "llm_postprocess",
                                                           "llm_temperature", "llm_max_completion_tokens",
                                                           "llm_timeout_seconds", "llm_max_retries")})


def test_stt_prompt_is_recorded_by_hash_and_leakage_is_counted(tmp_path: Path):
    prompt = "장부 입력: 외상, 현금, 계좌이체, 박스, 포기, 상회"
    prompt_file = tmp_path / "stt_prompt_ko.txt"
    prompt_file.write_text(prompt + "\n", encoding="utf-8")
    paths = _setup(tmp_path, [("g-dev-003", "S1"), ("g-dev-001", "S1")])
    stt = FakeSTT()

    def echo(audio, filename, mime, *, settings=None):
        """g-dev-001 comes back as the prompt's own words; the rest as the Golden sentence."""
        return "외상 현금 계좌이체" if filename == "S1_g-dev-001.m4a" else stt(audio, filename, mime, settings=settings)

    summary = _run(paths, stt=echo, settings=_prompt_settings(prompt_file), repeat=1)

    info = summary["run"]["stt_prompt"]
    assert info["on"] and info["chars"] == len(prompt) and len(info["sha256"]) == 64  # file content, stripped
    assert summary["stt"]["prompt_leak"] == {"count": 1, "ids": ["g-dev-001@S1"]}
    report = (paths["results_dir"] / "voice-test.report.md").read_text(encoding="utf-8")
    assert f"켬 (sha256 `{info['sha256'][:12]}`" in report and "섞인 인식 결과" in report
    assert "외상, 현금" not in report  # the word list itself is never written

    off = _run(_setup(tmp_path / "off", ENTRIES[:1]), repeat=1)
    assert off["run"]["stt_prompt"] == {"on": False, "chars": 0, "sha256": None}
    assert off["stt"]["prompt_leak"] is None


@pytest.mark.parametrize("content", [None, "  \n"])
def test_missing_or_blank_stt_prompt_file_stops_before_any_call(tmp_path: Path, content):
    prompt_file = tmp_path / "stt_prompt_ko.txt"
    if content is not None:
        prompt_file.write_text(content, encoding="utf-8")
    paths = _setup(tmp_path, ENTRIES[:1])
    stt = FakeSTT()
    with pytest.raises(voice.VoiceE2EError, match="STT_PROMPT_FILE"):
        _run(paths, stt=stt, settings=_prompt_settings(prompt_file), repeat=1)
    assert stt.calls == [] and not paths["out_dir"].exists()


def test_text_baseline_is_weighted_by_recordings_and_stt_failure_counts_as_wrong(tmp_path: Path):
    paths = _setup(tmp_path, [("g-dev-003", "S1"), ("g-dev-003", "S2")])
    summary = _run(paths, stt=FakeSTT(fail=("S2_g-dev-003.m4a",)))

    voice_tx = summary["e2e"]["all_included"]["voice"]["transaction_exact"]
    text_tx = summary["e2e"]["all_included"]["text"]["transaction_exact"]
    assert (voice_tx["correct"], voice_tx["total"]) == (3, 6)  # S1 perfect ×3, S2 STT failure ×3
    assert text_tx["total"] == 6
    failed = {"golden_id": "g-dev-003", "speaker": "S2", "type": "FakeAPIError", "status": 503}
    assert summary["stt"]["failed"] == [failed]
    by_id = {(r["golden_id"], r["speaker"]): r for r in summary["records"]}
    assert by_id[("g-dev-003", "S2")]["voice_correct"] == "0/3" and not by_id[("g-dev-003", "S2")]["stt_ok"]
    assert summary["cost"]["llm_usd"] > 0 and summary["cost"]["stt_usd_upper"] == 2 * 0.006


def test_stt_quality_counts_cer_without_spaces_and_key_info_after_normalization():
    reference = GOLDEN["g-dev-003"]["utterance_text"]
    same = voice.stt_quality(reference, reference.replace(" ", "") + ".")
    assert same["cer"]["edits"] == 0

    digits = voice.stt_quality(reference, "대박마트에 계란 2판 45,000원 달아놔")
    assert digits["cer_normalized"]["edits"] <= digits["cer"]["edits"]
    assert digits["key_info"]["amount"] == {"preserved": 1, "total": 1}
    assert digits["key_info"]["credit"] == {"preserved": 1, "total": 1}

    lost = voice.stt_quality(reference, "대박마트에 계란 두판 사만원")
    assert lost["key_info"]["amount"]["preserved"] == 0 and lost["key_info"]["credit"]["preserved"] == 0


def test_audio_seconds_from_wav_and_mp4_headers(tmp_path: Path):
    wav = tmp_path / "a.wav"
    with wave.open(str(wav), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * 16000)
    assert voice.audio_seconds(wav.read_bytes(), ".wav") == 2.0

    mvhd = b"mvhd" + b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, 1000, 3500)
    assert voice.audio_seconds(b"\x00\x00\x00\x6c" + mvhd + b"\x00" * 80, ".m4a") == 3.5
    assert voice.audio_seconds(b"\x1aE\xdf\xa3 webm", ".webm") is None
    assert voice.audio_seconds(b"garbage", ".wav") is None
