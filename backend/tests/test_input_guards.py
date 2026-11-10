"""Request size limit (PM #13) and manual-path sanitization (PM #9)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.main import app
from app.models import LedgerEntry

client = TestClient(app)


def _headers() -> dict[str, str]:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "user-b"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())}


# --- #13 body size ------------------------------------------------------------------------


def test_oversized_json_is_413_before_auth(db: Session) -> None:
    """Rejected on Content-Length alone — no token needed, nothing parsed or stored."""
    body = b'{"utterance_text": "' + b"a" * (64 * 1024) + b'"}'
    res = client.post("/api/v1/ledger/parse", content=body, headers={"content-type": "application/json"})
    assert res.status_code == 413
    assert res.json()["error_code"] == "PAYLOAD_TOO_LARGE"
    assert db.query(LedgerEntry).count() == 0


def test_413_carries_cors_headers_for_the_fe() -> None:
    """Otherwise the browser reports a network error instead of 413 (CLAUDE.md §4 오류 UX)."""
    origin = get_settings().cors_origin_list[0]
    body = b"x" * (64 * 1024 + 1)
    res = client.post("/api/v1/ledger/parse", content=body, headers={"origin": origin})
    assert res.status_code == 413
    assert res.headers.get("access-control-allow-origin") == origin


def test_chunked_body_without_length_is_cut_off() -> None:
    def chunks():
        for _ in range(70):
            yield b"x" * 1024

    res = client.post("/api/v1/ledger/parse", content=chunks(), headers={"content-type": "application/json"})
    assert res.status_code == 413


def test_audio_path_has_larger_limit() -> None:
    """6MB for /parse-audio: a 1MB upload passes the size gate (then fails auth → 401, not 413)."""
    res = client.post("/api/v1/ledger/parse-audio", files={"file": ("a.webm", b"x" * 1_000_000, "audio/webm")})
    assert res.status_code == 401
    big = client.post(
        "/api/v1/ledger/parse-audio",
        content=b"x" * (6 * 1024 * 1024 + 1),
        headers={"content-type": "multipart/form-data; boundary=x"},
    )
    assert big.status_code == 413


def test_normal_request_unaffected() -> None:
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": "김씨에게 배추 3박스 외상 30000원"},
                      headers=_headers())
    assert res.status_code == 200


# --- #9 manual path sanitization ------------------------------------------------------------

CANDIDATE = {
    "counterparty": "김씨",
    "items": [{"name": "배추", "qty": 3, "unit": "박스", "unit_price": 10000, "amount": 30000}],
    "total_amount": 30000,
    "credit": True,
}


def _manual(candidate: dict):
    return client.post("/api/v1/ledger/parse", json={"utterance_text": "김씨 배추", "candidate": candidate},
                       headers=_headers())


def test_whitespace_is_trimmed_before_validation(db: Session) -> None:
    item = {**CANDIDATE["items"][0], "name": "  배추 ", "unit": " 박스"}
    res = _manual({**CANDIDATE, "counterparty": "  김씨  ", "items": [item]})
    assert res.status_code == 200
    body = res.json()
    assert body["counterparty"] == "김씨"
    assert (body["items"][0]["name"], body["items"][0]["unit"]) == ("배추", "박스")
    assert db.query(LedgerEntry).one().counterparty == "김씨"


def test_blank_counterparty_becomes_missing_400() -> None:
    res = _manual({**CANDIDATE, "counterparty": "   "})
    assert res.status_code == 400
    assert res.json()["error_code"] == "MISSING_REQUIRED_FIELD"
    assert "counterparty" in res.json()["problem_fields"]


@pytest.mark.parametrize("value", ["30000", True])
def test_money_must_be_a_json_number(value) -> None:
    """No numeric strings, no booleans sneaking in as 1 — FE never sends these → format 422."""
    assert _manual({**CANDIDATE, "total_amount": value}).status_code == 422
    item = {**CANDIDATE["items"][0], "amount": value}
    assert _manual({**CANDIDATE, "items": [item]}).status_code == 422


def test_fractional_won_is_400_with_field(db: Session) -> None:
    """A user can type 30000.5 in the form → DL-03 400 so the modal highlights the field."""
    item = {**CANDIDATE["items"][0], "amount": 30000.5}
    res = _manual({**CANDIDATE, "items": [item]})
    assert res.status_code == 400
    assert res.json()["error_code"] == "AMOUNT_OUT_OF_RANGE"
    assert res.json()["problem_fields"] == ["items[0].amount"]
    assert db.query(LedgerEntry).count() == 0


def test_whole_float_amount_is_accepted() -> None:
    item = {**CANDIDATE["items"][0], "amount": 30000.0}
    assert _manual({**CANDIDATE, "items": [item], "total_amount": 30000.0}).status_code == 200


def test_truncated_chunked_body_never_reaches_the_route(db: Session) -> None:
    """Valid JSON + padding over the limit: 413 and nothing stored (route must not run on a partial body)."""
    head = b'{"utterance_text": "\uae40\uc528\uc5d0\uac8c \ubc30\ucd94 3\ubc15\uc2a4 \uc678\uc0c1 30000\uc6d0"}'

    def chunks():
        yield head
        for _ in range(70):
            yield b" " * 1024

    res = client.post("/api/v1/ledger/parse", content=chunks(), headers={**_headers(), "content-type": "application/json"})
    assert res.status_code == 413
    assert db.query(LedgerEntry).count() == 0


def test_audio_limit_follows_stt_max_bytes() -> None:
    from app.config import Settings

    s = Settings(_env_file=None, stt_max_bytes=10_000_000)
    assert s.max_audio_body_bytes == 10_000_000 + 1024 * 1024
