"""STT vendor switch — CLOVA Speech short-sentence adapter and recording conversion (no network)."""

import subprocess
import uuid

import httpx
import imageio_ffmpeg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.main import app
from app.services import stt as stt_module
from app.services.audio_convert import WAV_HEADER_BYTES, AudioConvertError, to_wav
from app.services.stt import STTError, clova_boostings, transcribe

INVOKE = "https://clovaspeech-gw.ncloud.com/external/v1/1234/abcd"


def _settings(**overrides) -> Settings:
    return Settings(**{
        "app_env": "development", "_env_file": None, "stt_provider": "clova",
        "clova_speech_invoke_url": INVOKE, "clova_speech_secret_key": "clova-test-key", **overrides,
    })


class FakeHttp:
    """Stands in for httpx.Client — records the request, answers with a canned response."""

    def __init__(self, status: int = 200, json_body: object = None, error: Exception | None = None) -> None:
        self.status, self.json_body, self.error, self.request = status, json_body, error, None

    def post(self, url, *, params, headers, content):
        self.request = {"url": url, "params": params, "headers": headers, "content": content}
        if self.error:
            raise self.error
        return httpx.Response(self.status, json=self.json_body if self.json_body is not None else {"text": ""})

    def close(self) -> None:
        pass


def _sample(fmt: str) -> bytes:
    """2 s tone in a browser format, made by the bundled ffmpeg (mp4 keeps its index at the end like iPhone)."""
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    codec = ["-c:a", "libopus", "-f", "webm"] if fmt == "webm" else ["-c:a", "aac", "-f", "ipod"]
    if fmt == "webm":
        return subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=2",
                               "-ac", "1", *codec, "pipe:1"], capture_output=True, check=True).stdout
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "a.m4a"
        subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=2",
                        "-ac", "1", "-c:a", "aac", str(out)], check=True)
        return out.read_bytes()


# --- request shape ------------------------------------------------------------------------------

def test_default_provider_is_openai() -> None:
    assert Settings(_env_file=None, app_env="development").stt_provider == "openai"


def test_clova_request_shape_for_a_format_it_reads() -> None:
    http = FakeHttp(json_body={"text": "  가나상회 배추 두 박스  ", "quota": 15})
    text = transcribe(b"ID3-mp3-bytes", "a.mp3", "audio/mpeg", settings=_settings(), client=http)
    assert text == "가나상회 배추 두 박스"
    assert http.request["url"] == INVOKE + "/recog/v1/stt"
    assert http.request["params"] == {"lang": "Kor"}  # no names → no boostings key
    assert http.request["headers"] == {"Content-Type": "application/octet-stream", "X-CLOVASPEECH-API-KEY": "clova-test-key"}
    assert http.request["content"] == b"ID3-mp3-bytes"  # sent as-is, not converted


def test_invoke_url_that_already_has_the_path_is_kept() -> None:
    http = FakeHttp(json_body={"text": "배추"})
    transcribe(b"x", "a.wav", "audio/wav", settings=_settings(clova_speech_invoke_url=INVOKE + "/recog/v1/stt/"),
               client=http)
    assert http.request["url"] == INVOKE + "/recog/v1/stt"


def test_names_go_as_boostings_not_the_openai_prompt() -> None:
    http = FakeHttp(json_body={"text": "가나상회"})
    transcribe(b"x", "a.wav", "audio/wav", counterparty_hints=["가나상회", "김씨", "다라 식당"], settings=_settings(),
               client=http)
    assert http.request["params"]["boostings"] == "가나상회\t다라 식당"  # 2-char name dropped (not boosted by API)


def test_boostings_stay_under_512_chars() -> None:
    names = ["가" * 40 + chr(0xAC00 + i) for i in range(30)]
    joined = clova_boostings(names)
    assert 0 < len(joined) <= 512
    assert all(len(n) >= 3 for n in joined.split("\t"))


def test_boostings_send_hangul_names_only() -> None:
    """병철 #168 리뷰: CLOVA boostings are Korean only — "GS25"·"ABC상사" would get the whole request rejected."""
    assert clova_boostings(["하늘마트", "GS25", "ABC상사", "다라 식당", "가나상회2"]) == "하늘마트\t다라 식당"


def test_one_overlong_name_does_not_drop_the_rest() -> None:
    assert clova_boostings(["가" * 600, "하늘마트"]) == "하늘마트"


def test_webm_is_converted_to_wav_before_sending() -> None:
    http = FakeHttp(json_body={"text": "배추"})
    transcribe(_sample("webm"), "a.webm", "audio/webm", settings=_settings(), client=http)
    assert http.request["content"][:4] == b"RIFF"


# --- conversion ---------------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["webm", "mp4"])
def test_browser_recordings_convert_to_16k_mono_wav(fmt: str) -> None:
    wav = to_wav(_sample(fmt), timeout=10)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert len(wav) > 16000  # ~2 s of 16 kHz 16-bit mono, not an empty header


def test_iphone_style_mp4_is_not_silently_empty() -> None:
    """Index at the end of the file: a pipe-only conversion returns a bare header with exit 0 — we must not."""
    sample = _sample("mp4")
    assert sample.rfind(b"moov") > sample.find(b"mdat")
    assert len(to_wav(sample, timeout=10)) > WAV_HEADER_BYTES


def test_garbage_audio_is_a_convert_error() -> None:
    with pytest.raises(AudioConvertError):
        to_wav(b"not audio at all", timeout=10)


# --- failures → STTError (503) ------------------------------------------------------------------

def test_missing_clova_config_is_an_error() -> None:
    with pytest.raises(STTError, match="not configured"):
        transcribe(b"x", "a.wav", "audio/wav", settings=_settings(clova_speech_secret_key=""), client=FakeHttp())
    with pytest.raises(STTError, match="not configured"):
        transcribe(b"x", "a.wav", "audio/wav", settings=_settings(clova_speech_invoke_url=" "), client=FakeHttp())


def test_clova_needs_no_openai_key() -> None:
    http = FakeHttp(json_body={"text": "배추"})
    assert transcribe(b"x", "a.wav", "audio/wav", settings=_settings(openai_api_key=None), client=http) == "배추"


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_vendor_error_status_is_stt_error(status: int) -> None:
    with pytest.raises(STTError):
        transcribe(b"x", "a.wav", "audio/wav", settings=_settings(), client=FakeHttp(status=status))


def test_network_error_is_stt_error() -> None:
    error = httpx.ConnectError("down", request=httpx.Request("POST", INVOKE))
    with pytest.raises(STTError):
        transcribe(b"x", "a.wav", "audio/wav", settings=_settings(), client=FakeHttp(error=error))


def test_unconvertible_recording_is_stt_error() -> None:
    with pytest.raises(STTError):
        transcribe(b"not audio", "a.webm", "audio/webm", settings=_settings(), client=FakeHttp())


def test_empty_or_odd_text_is_stt_error() -> None:
    for body in ({"text": "   "}, {"text": None}, {"quota": 15}, ["text"]):
        with pytest.raises(STTError):
            transcribe(b"x", "a.wav", "audio/wav", settings=_settings(), client=FakeHttp(json_body=body))


def test_slow_clova_is_cut_at_the_deadline() -> None:
    import time

    class Slow(FakeHttp):
        def post(self, *a, **k):
            time.sleep(2.0)
            return super().post(*a, **k)

    started = time.perf_counter()
    with pytest.raises(STTError, match="exceeded"):
        transcribe(b"x", "a.wav", "audio/wav", settings=_settings(stt_timeout_seconds=0.2), client=Slow())
    assert time.perf_counter() - started < 1.0


def test_secret_key_never_reaches_logs(caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(STTError):
        transcribe(b"x", "a.wav", "audio/wav", settings=_settings(), client=FakeHttp(status=401))
    assert "clova-test-key" not in caplog.text


# --- /parse-audio wiring ------------------------------------------------------------------------

def test_parse_audio_answers_504_when_clova_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "stt_provider", "clova")
    monkeypatch.setattr(get_settings(), "clova_speech_invoke_url", INVOKE)
    monkeypatch.setattr(get_settings(), "clova_speech_secret_key", SecretStr("clova-test-key"))
    monkeypatch.setattr(stt_module.httpx, "Client", lambda **_: FakeHttp(status=500))

    client = TestClient(app)
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "user-b"}).json()["access_token"]
    res = client.post(
        "/api/v1/ledger/parse-audio",
        files={"file": ("a.wav", b"RIFF" + b"x" * 1000, "audio/wav")},
        headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())},
    )
    assert res.status_code == 503
    assert res.json()["error_code"] == "EXTERNAL_API_TIMEOUT"
