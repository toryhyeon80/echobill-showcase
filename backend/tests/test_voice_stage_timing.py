"""PM 10/20 — /parse-audio stage times (STT · parse) in Server-Timing and one log line; never the transcript."""

import logging
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.stt import STTError

client = TestClient(app)
HEARD = "김씨비밀상회에게 배추 3박스 외상 30000원"
_STAGES = re.compile(r"^app;dur=\d+\.\d, stt;dur=\d+, parse;dur=\d+$")


def _post(key: str | None = None):
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "user-timing"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "X-Idempotency-Key": key or str(uuid.uuid4())}
    return client.post("/api/v1/ledger/parse-audio", files={"file": ("a.webm", b"x" * 1000, "audio/webm")},
                       headers=headers)


def test_success_sends_stt_and_parse_stages_and_logs_numbers_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("app.api.ledger.transcribe", lambda *a, **k: HEARD)
    with caplog.at_level(logging.DEBUG):
        res = _post()
    assert res.status_code == 200
    assert _STAGES.match(res.headers["server-timing"])
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("voice_timing")]
    assert len(lines) == 1
    assert re.fullmatch(r"voice_timing status=200 stt_ms=\d+ parse_ms=\d+ audio_bytes=1000 mime=audio/webm", lines[0])
    assert "김씨비밀상회" not in caplog.text


def test_stt_failure_has_stt_stage_only(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def fail(*_a, **_k):
        raise STTError("down")

    monkeypatch.setattr("app.api.ledger.transcribe", fail)
    with caplog.at_level(logging.WARNING):
        res = _post()
    assert res.status_code == 503
    assert re.fullmatch(r"app;dur=\d+\.\d, stt;dur=\d+", res.headers["server-timing"])
    assert any(r.getMessage().startswith("voice_timing status=503 stt_ms=") for r in caplog.records)


def test_replayed_key_has_app_time_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.ledger.transcribe", lambda *a, **k: HEARD)
    key = str(uuid.uuid4())
    assert _post(key).status_code == 200
    again = _post(key)
    assert again.status_code == 200
    assert re.fullmatch(r"app;dur=\d+\.\d", again.headers["server-timing"])


def test_other_routes_keep_app_only() -> None:
    assert re.fullmatch(r"app;dur=\d+\.\d", client.get("/health").headers["server-timing"])
