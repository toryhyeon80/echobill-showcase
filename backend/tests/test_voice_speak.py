"""읽어 주기 TTS (PM 10/26) — POST /api/v1/voice/speak: mp3, length cap, login, vendor failure, cap, no text in logs."""

import logging
import threading
from types import SimpleNamespace

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.main import app
from app.models import IdempotencyRecord
from app.services import tts

client = TestClient(app)
URL = "/api/v1/voice/speak"
TEXT = "마바분식 대파 팔천 원 외상이요. 몇 단이었어요?"
MP3 = b"ID3fake-mp3"


def _auth(user_id: str = "user-s") -> dict[str, str]:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": user_id}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


class FakeSpeech:
    """Stands in for client.audio.speech — records the request, returns MP3 or raises."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    def create(self, **request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return SimpleNamespace(read=lambda: MP3)


@pytest.fixture
def instructions(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    path = tmp_path / "tts_instructions.txt"
    path.write_text("차분하고 또박또박한 말투\n", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "tts_instructions_file", str(path))
    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr("sk-test"))
    return "차분하고 또박또박한 말투"


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch, instructions: str):
    """OpenAI replaced — the API path builds its own client, so patch the constructor."""
    speech = FakeSpeech()
    monkeypatch.setattr(tts, "OpenAI", lambda **kw: SimpleNamespace(audio=SimpleNamespace(speech=speech)))
    return speech


def test_speak_returns_mp3_with_configured_voice(fake_openai: FakeSpeech, instructions: str, db: Session) -> None:
    r = client.post(URL, json={"text": f"  {TEXT}  "}, headers=_auth())
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.headers["cache-control"] == "no-store"
    assert r.content == MP3
    assert fake_openai.calls == [{"model": "gpt-4o-mini-tts", "voice": "marin", "input": TEXT,
                                  "response_format": "mp3", "instructions": instructions}]
    assert db.scalar(select(IdempotencyRecord.scope)) == "speak"  # counted toward the speak cap


def test_vendor_gets_digits_as_korean_words(fake_openai: FakeSpeech, instructions: str) -> None:
    """The screen shows digits; the voice reads them as people say them (PM 11/2)."""
    r = client.post(URL, json={"text": "합계가 12,000원이에요, 15,000원이에요?"}, headers=_auth())
    assert r.status_code == 200
    assert fake_openai.calls[0]["input"] == "합계가 만 이천 원이에요, 만 오천 원이에요?"


def test_text_over_limit_is_400_without_vendor_call(fake_openai: FakeSpeech) -> None:
    limit = get_settings().tts_max_chars
    assert client.post(URL, json={"text": "가" * limit}, headers=_auth()).status_code == 200
    r = client.post(URL, json={"text": "가" * (limit + 1)}, headers=_auth())
    assert r.status_code == 400
    assert r.json()["error_code"] == "TEXT_TOO_LONG"
    assert len(fake_openai.calls) == 1


def test_blank_text_is_400(fake_openai: FakeSpeech) -> None:
    r = client.post(URL, json={"text": "   "}, headers=_auth())
    assert (r.status_code, r.json()["error_code"]) == (400, "TEXT_EMPTY")
    assert fake_openai.calls == []


def test_login_required(fake_openai: FakeSpeech) -> None:
    assert client.post(URL, json={"text": TEXT}).status_code == 401
    assert fake_openai.calls == []


@pytest.mark.parametrize("error", [
    openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com")),
    openai.InternalServerError("boom", response=httpx.Response(500, request=httpx.Request("POST", "https://x")),
                               body=None),
])
def test_vendor_failure_is_504_logged_without_text(
    error: Exception, fake_openai: FakeSpeech, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    fake_openai.error = error
    with caplog.at_level(logging.WARNING):
        r = client.post(URL, json={"text": TEXT}, headers=_auth())
    assert (r.status_code, r.json()["error_code"]) == (503, "EXTERNAL_API_TIMEOUT")
    assert "TTS failed" in caplog.text
    assert "마바분식" not in caplog.text
    # still counted: a timed-out call keeps running (and billing), so failures must not dodge the cap
    assert db.scalar(select(IdempotencyRecord.scope)) == "speak"


def test_deadline_is_504(monkeypatch: pytest.MonkeyPatch, fake_openai: FakeSpeech) -> None:
    release = threading.Event()
    monkeypatch.setattr(get_settings(), "tts_timeout_seconds", 0.05)
    monkeypatch.setattr(fake_openai, "create", lambda **kw: release.wait(2) or SimpleNamespace(read=lambda: MP3))
    try:
        r = client.post(URL, json={"text": TEXT}, headers=_auth())
    finally:
        release.set()
    assert r.status_code == 503


def test_missing_instructions_file_is_504(monkeypatch: pytest.MonkeyPatch, fake_openai: FakeSpeech, tmp_path) -> None:
    monkeypatch.setattr(get_settings(), "tts_instructions_file", str(tmp_path / "none.txt"))
    assert client.post(URL, json={"text": TEXT}, headers=_auth()).status_code == 503
    assert fake_openai.calls == []


def test_missing_key_is_504(monkeypatch: pytest.MonkeyPatch, fake_openai: FakeSpeech) -> None:
    monkeypatch.setattr(get_settings(), "openai_api_key", None)
    assert client.post(URL, json={"text": TEXT}, headers=_auth()).status_code == 503


def test_rate_limited_per_user_with_retry_after(monkeypatch: pytest.MonkeyPatch, fake_openai: FakeSpeech) -> None:
    monkeypatch.setattr(get_settings(), "speak_limit_per_minute", 2)
    for _ in range(2):
        assert client.post(URL, json={"text": TEXT}, headers=_auth()).status_code == 200
    r = client.post(URL, json={"text": TEXT}, headers=_auth())
    assert (r.status_code, r.json()["error_code"]) == (429, "RATE_LIMITED")
    assert int(r.headers["Retry-After"]) >= 1
    assert len(fake_openai.calls) == 2
    # another owner has their own count
    assert client.post(URL, json={"text": TEXT}, headers=_auth("user-other")).status_code == 200


def test_default_instructions_file_is_in_repo() -> None:
    """The deploy reads it by default — without it every call is a 503 (PM 10/24 설정값, same tone as stt-retry.mp3)."""
    assert tts.DEFAULT_INSTRUCTIONS_FILE.read_text(encoding="utf-8").strip()
