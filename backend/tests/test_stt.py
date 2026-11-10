"""STT adapter — Whisper call shape and error mapping (no network)."""

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from openai import APIConnectionError
import httpx

from app.config import Settings
from app.services.stt import STTError, transcribe


def _settings(**overrides) -> Settings:
    return Settings(**{"app_env": "development", "openai_api_key": "test-key", "_env_file": None, **overrides})


class FakeTranscriptions:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result, self.error, self.request = result, error, None

    def create(self, **kwargs):
        self.request = kwargs
        if self.error:
            raise self.error
        return self.result


def _client(t: FakeTranscriptions):
    return SimpleNamespace(audio=SimpleNamespace(transcriptions=t))


def test_transcribe_sends_korean_whisper_request() -> None:
    t = FakeTranscriptions(SimpleNamespace(text="  김씨에게 배추 세 박스  "))
    text = transcribe(b"audio", "rec.webm", "audio/webm", settings=_settings(), client=_client(t))
    assert text == "김씨에게 배추 세 박스"
    assert t.request["model"] == "whisper-1"
    assert t.request["language"] == "ko"
    assert t.request["file"] == ("rec.webm", b"audio", "audio/webm")


REPO_STT_PROMPT = Path(__file__).resolve().parents[2] / "configs" / "prompts" / "stt_prompt_ko.txt"


def test_stt_prompt_file_is_off_by_default() -> None:
    assert Settings(_env_file=None, app_env="development").stt_prompt_file == ""


def test_default_request_has_no_prompt_argument() -> None:
    """STT_PROMPT_FILE unset → the exact request sent before this setting existed (no prompt key at all)."""
    t = FakeTranscriptions(SimpleNamespace(text="배추 세 박스"))
    transcribe(b"audio", "rec.webm", "audio/webm", settings=_settings(), client=_client(t))
    assert t.request == {"model": "whisper-1", "file": ("rec.webm", b"audio", "audio/webm"), "language": "ko"}


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_setting_sends_no_prompt(value: str) -> None:
    t = FakeTranscriptions(SimpleNamespace(text="배추 세 박스"))
    transcribe(b"audio", "rec.webm", "audio/webm", settings=_settings(stt_prompt_file=value), client=_client(t))
    assert "prompt" not in t.request


def test_whitespace_only_prompt_file_sends_no_prompt(tmp_path: Path) -> None:
    prompt_file = tmp_path / "blank.txt"
    prompt_file.write_text("  \n\t\n", encoding="utf-8")
    t = FakeTranscriptions(SimpleNamespace(text="배추 세 박스"))
    transcribe(b"audio", "rec.webm", "audio/webm", settings=_settings(stt_prompt_file=str(prompt_file)),
               client=_client(t))
    assert "prompt" not in t.request


def test_prompt_file_content_is_sent_as_prompt(tmp_path: Path) -> None:
    """File text (stripped) goes as `prompt`; model · language · file stay as before."""
    prompt_file = tmp_path / "hint.txt"
    prompt_file.write_text("\n외상, 박스, 만 원\n", encoding="utf-8")
    t = FakeTranscriptions(SimpleNamespace(text="배추 세 박스"))
    transcribe(b"audio", "rec.webm", "audio/webm", settings=_settings(stt_prompt_file=str(prompt_file)),
               client=_client(t))
    assert t.request["prompt"] == "외상, 박스, 만 원"
    assert t.request["model"] == "whisper-1"
    assert t.request["language"] == "ko"
    assert t.request["file"] == ("rec.webm", b"audio", "audio/webm")


def test_repo_prompt_file_is_sent_verbatim() -> None:
    t = FakeTranscriptions(SimpleNamespace(text="배추 세 박스"))
    transcribe(b"audio", "rec.webm", "audio/webm", settings=_settings(stt_prompt_file=str(REPO_STT_PROMPT)),
               client=_client(t))
    expected = REPO_STT_PROMPT.read_text(encoding="utf-8").strip()
    assert expected
    assert t.request["prompt"] == expected


def test_missing_prompt_file_transcribes_without_prompt_and_warns_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A set but missing file drops the hint (same request as off) — one warning with the path, not per request."""
    missing = str(tmp_path / "missing.txt")
    settings = _settings(stt_prompt_file=missing)
    with caplog.at_level(logging.WARNING, logger="app.services.stt"):
        for _ in range(3):
            t = FakeTranscriptions(SimpleNamespace(text="배추 세 박스"))
            assert transcribe(b"audio", "rec.webm", "audio/webm", settings=settings, client=_client(t)) == "배추 세 박스"
            assert t.request == {"model": "whisper-1", "file": ("rec.webm", b"audio", "audio/webm"), "language": "ko"}
    warnings = [r for r in caplog.records if "STT prompt file" in r.getMessage()]
    assert len(warnings) == 1
    assert missing in warnings[0].getMessage()


def test_transcribe_maps_provider_error_to_stt_error() -> None:
    error = APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))
    t = FakeTranscriptions(error=error)
    with pytest.raises(STTError):
        transcribe(b"audio", "rec.webm", "audio/webm", settings=_settings(), client=_client(t))


def test_empty_transcript_is_an_error() -> None:
    t = FakeTranscriptions(SimpleNamespace(text="   "))
    with pytest.raises(STTError):
        transcribe(b"audio", "rec.webm", "audio/webm", settings=_settings(), client=_client(t))


def test_missing_key_is_an_error() -> None:
    with pytest.raises(STTError):
        transcribe(b"audio", "rec.webm", "audio/webm", settings=_settings(openai_api_key=None))


class SlowTranscriptions(FakeTranscriptions):
    """Simulates Whisper replying after `delay` seconds (slow upload / slow server)."""

    def __init__(self, delay: float) -> None:
        super().__init__(result=SimpleNamespace(text="늦은 응답"))
        self.delay = delay

    def create(self, **kwargs):
        import time

        time.sleep(self.delay)
        return super().create(**kwargs)


def test_whole_call_deadline_cuts_slow_stt() -> None:
    """The deadline covers the whole call — per-phase SDK timeouts alone could run far past it."""
    import time

    started = time.perf_counter()
    with pytest.raises(STTError, match="exceeded"):
        transcribe(b"audio", "a.webm", "audio/webm", settings=_settings(stt_timeout_seconds=0.2),
                   client=_client(SlowTranscriptions(delay=2.0)))
    assert time.perf_counter() - started < 1.0


def test_fast_stt_within_deadline_returns_text() -> None:
    text = transcribe(b"audio", "a.webm", "audio/webm", settings=_settings(stt_timeout_seconds=2.0),
                      client=_client(SlowTranscriptions(delay=0.05)))
    assert text == "늦은 응답"


FE_PARSE_AUDIO_TIMEOUT = 25.0  # minimum for /parse-audio — FE #49 uses 30s (text /parse keeps 15s)
UPLOAD_ALLOWANCE = 8.0  # 1-minute ≈1.6MB recording on a slow LTE uplink


def test_server_budget_leaves_room_for_upload_in_fe_timeout() -> None:
    """Upload + STT + LLM must end (or fail to 503/Rule) before the FE gives up on /parse-audio."""
    s = Settings(_env_file=None, app_env="development")
    assert s.stt_timeout_seconds == 10.0
    assert UPLOAD_ALLOWANCE + s.stt_timeout_seconds + s.llm_timeout_seconds < FE_PARSE_AUDIO_TIMEOUT


def test_parse_audio_answers_504_within_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: slow Whisper → the server's 503 (텍스트로 입력) arrives at the deadline, not after."""
    import time
    import uuid

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr("test-key"))
    monkeypatch.setattr(get_settings(), "stt_timeout_seconds", 0.3)
    monkeypatch.setattr("app.services.stt.OpenAI", lambda **_: _client(SlowTranscriptions(delay=3.0)))

    client = TestClient(app)
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "user-b"}).json()["access_token"]
    started = time.perf_counter()
    res = client.post(
        "/api/v1/ledger/parse-audio",
        files={"file": ("a.webm", b"x" * 1000, "audio/webm")},
        headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())},
    )
    assert res.status_code == 503
    assert res.json()["error_code"] == "EXTERNAL_API_TIMEOUT"
    assert time.perf_counter() - started < 1.5


def test_stt_failure_answers_503_the_browser_can_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """DL-33: Cloudflare replaces an origin 502·504 with its own page (no CORS) → the FE saw "check your connection"."""

    def fail(*args: object, **kwargs: object) -> str:
        raise STTError("no text")

    monkeypatch.setattr("app.api.ledger.transcribe", fail)
    import uuid

    from fastapi.testclient import TestClient

    from app.main import app

    origin = "http://localhost:5173"
    client = TestClient(app)
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "user-b"}).json()["access_token"]
    res = client.post(
        "/api/v1/ledger/parse-audio",
        files={"file": ("a.webm", b"x" * 1000, "audio/webm")},
        headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4()), "Origin": origin},
    )
    assert (res.status_code, res.json()["error_code"]) == (503, "EXTERNAL_API_TIMEOUT")
    assert res.headers.get("access-control-allow-origin") == origin


def test_measure_script_makes_sample_without_a_recording(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """수경님(Windows)처럼 `say`가 없어도 --make-sample로 같은 샘플을 만든다 (TTS는 가짜 클라이언트)."""
    from app.config import get_settings
    from scripts import measure_stt

    requests: list[dict] = []

    class FakeSpeech:
        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(read=lambda: b"OggS-fake-opus")

    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr("test-key"))
    monkeypatch.setattr(measure_stt, "OpenAI", lambda **_: SimpleNamespace(audio=SimpleNamespace(speech=FakeSpeech())))
    out = tmp_path / "sample.ogg"
    assert measure_stt.main(["--make-sample", str(out)]) == 0
    assert out.read_bytes() == b"OggS-fake-opus"
    assert requests[0]["response_format"] == "opus"
    assert "배추" in requests[0]["input"]


def test_measure_script_requires_input() -> None:
    from scripts import measure_stt

    with pytest.raises(SystemExit):
        measure_stt.main([])
