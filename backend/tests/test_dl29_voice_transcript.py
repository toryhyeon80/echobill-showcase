"""DL-29 — /parse-audio 400 after STT carries the transcript; nowhere else, never in logs."""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.stt import STTError

client = TestClient(app)
HEARD = "김씨비밀상회에게 배추 외상"  # no amount → MISSING_REQUIRED_FIELD (400)


def _headers() -> dict[str, str]:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "user-b"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())}


def _audio(content_type: str = "audio/webm", data: bytes = b"x" * 1000):
    return client.post("/api/v1/ledger/parse-audio", files={"file": ("a.webm", data, content_type)}, headers=_headers())


def test_voice_400_returns_what_was_heard(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr("app.api.ledger.transcribe", lambda *a, **k: HEARD)
    with caplog.at_level(logging.DEBUG):
        res = _audio()
    assert res.status_code == 400
    body = res.json()
    assert body["error_code"] == "MISSING_REQUIRED_FIELD"
    assert body["utterance_text"] == HEARD
    assert "김씨비밀상회" not in caplog.text  # never logged (CLAUDE.md §3②)


def test_voice_success_has_no_error_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.ledger.transcribe", lambda *a, **k: "김씨에게 배추 3박스 외상 30000원")
    res = _audio()
    assert res.status_code == 200
    assert res.json()["utterance_text"] == "김씨에게 배추 3박스 외상 30000원"  # the candidate's own field
    assert "error_code" not in res.json()


def test_audio_invalid_has_no_transcript() -> None:
    res = _audio(content_type="text/plain")
    assert res.status_code == 400
    assert res.json()["error_code"] == "AUDIO_INVALID"
    assert res.json()["utterance_text"] is None


def test_stt_failure_504_has_no_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_a, **_k):
        raise STTError("down")

    monkeypatch.setattr("app.api.ledger.transcribe", fail)
    res = _audio()
    assert res.status_code == 503
    assert res.json()["utterance_text"] is None


def test_text_parse_400_has_no_transcript() -> None:
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": HEARD}, headers=_headers())
    assert res.status_code == 400
    assert res.json()["utterance_text"] is None


def test_replay_of_voice_400_keeps_the_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same key → the stored first response, transcript included (24h idempotency record)."""
    monkeypatch.setattr("app.api.ledger.transcribe", lambda *a, **k: HEARD)
    headers = _headers()
    files = {"file": ("a.webm", b"x" * 1000, "audio/webm")}
    first = client.post("/api/v1/ledger/parse-audio", files=files, headers=headers)
    again = client.post("/api/v1/ledger/parse-audio", files={"file": ("a.webm", b"x" * 1000, "audio/webm")}, headers=headers)
    assert first.json() == again.json()
    assert again.json()["utterance_text"] == HEARD
