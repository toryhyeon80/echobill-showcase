"""Unhandled exception → 500 the browser can read: CORS headers + DL-03 body (CLAUDE.md §4 "500 ≠ network")."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import create_app
from app.middleware import ServerErrorResponseMiddleware
from app.models import IdempotencyRecord

ORIGIN = "http://localhost:5173"  # default CORS_ORIGINS


def _boom_app() -> FastAPI:
    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("bug")

    return app


def test_unhandled_exception_answers_500_with_cors_and_error_body() -> None:
    res = TestClient(_boom_app(), raise_server_exceptions=False).get("/boom", headers={"Origin": ORIGIN})
    assert res.status_code == 500
    assert res.headers.get("access-control-allow-origin") == ORIGIN
    assert res.headers.get("access-control-allow-credentials") == "true"
    body = res.json()
    assert body["error_code"] == "INTERNAL_SERVER_ERROR"
    assert "bug" not in res.text  # the exception text never reaches the client


def test_other_origin_gets_no_cors_header_on_500() -> None:
    res = TestClient(_boom_app(), raise_server_exceptions=False).get("/boom", headers={"Origin": "https://evil.example"})
    assert res.status_code == 500
    assert "access-control-allow-origin" not in res.headers


def test_exception_still_propagates_so_the_server_logs_the_traceback() -> None:
    with pytest.raises(RuntimeError, match="bug"):
        TestClient(_boom_app()).get("/boom", headers={"Origin": ORIGIN})


def test_failure_after_the_response_started_is_not_answered_twice() -> None:
    app = FastAPI()
    app.add_middleware(ServerErrorResponseMiddleware)

    @app.get("/stream")
    def stream() -> StreamingResponse:
        def chunks():
            yield b"part"
            raise RuntimeError("bug")

        return StreamingResponse(chunks())

    with pytest.raises(RuntimeError, match="bug"):
        TestClient(app).get("/stream")


def test_voice_route_crash_is_readable_and_the_key_can_be_retried(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    """The reported path: /parse-audio raising past STTError must not look like a lost connection."""

    def crash(*args: object, **kwargs: object) -> str:
        raise ValueError("bug")

    monkeypatch.setattr("app.api.ledger.transcribe", crash)
    client = TestClient(_boom_app(), raise_server_exceptions=False)
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "user-b"}).json()["access_token"]
    key = str(uuid.uuid4())
    res = client.post(
        "/api/v1/ledger/parse-audio",
        files={"file": ("a.webm", b"x" * 1000, "audio/webm")},
        headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": key, "Origin": ORIGIN},
    )
    assert res.status_code == 500
    assert res.headers.get("access-control-allow-origin") == ORIGIN
    assert res.json()["error_code"] == "INTERNAL_SERVER_ERROR"
    assert db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.key == key)) is None
