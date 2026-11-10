"""DL-22 5-3·5-6 — X-Idempotency-Key replay, 409 on a different body, 24h expiry."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import IdempotencyRecord, LedgerEntry
from app.services.idempotency import _request_hash

client = TestClient(app)

UTTERANCE = "김씨에게 배추 3박스 외상 30000원"


def _token(user_id: str = "dev-user-1") -> str:
    return client.post("/api/v1/auth/dev-token", json={"user_id": user_id}).json()["access_token"]


def _parse(key: str | None, text: str = UTTERANCE, user_id: str = "dev-user-1"):
    headers = {"Authorization": f"Bearer {_token(user_id)}"}
    if key is not None:
        headers["X-Idempotency-Key"] = key
    return client.post("/api/v1/ledger/parse", json={"utterance_text": text}, headers=headers)


def test_same_key_same_body_replays_first_response(db: Session) -> None:
    """A retried request returns the same entry and writes nothing new."""
    key = str(uuid.uuid4())
    first = _parse(key)
    second = _parse(key)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert db.query(LedgerEntry).count() == 1


def test_same_key_different_body_is_409(db: Session) -> None:
    key = str(uuid.uuid4())
    assert _parse(key).status_code == 200
    res = _parse(key, text="박사장에게 무 7박스 외상 70000원")
    assert res.status_code == 409
    assert res.json()["error_code"] == "IDEMPOTENCY_KEY_REUSED"
    assert db.query(LedgerEntry).count() == 1


def test_missing_key_is_400_and_stores_nothing(db: Session) -> None:
    res = _parse(None)
    assert res.status_code == 400
    assert res.json()["error_code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert db.query(LedgerEntry).count() == 0


def test_oversized_key_is_400() -> None:
    assert _parse("k" * 129).status_code == 400


def test_keys_are_scoped_per_user(db: Session) -> None:
    """User B reusing A's key gets their own result, never A's cached response."""
    key = str(uuid.uuid4())
    a = _parse(key, user_id="user-a")
    b = _parse(key, user_id="user-b")
    assert a.status_code == b.status_code == 200
    assert a.json()["entry_id"] != b.json()["entry_id"]
    assert db.query(LedgerEntry).count() == 2


def test_400_response_is_replayed_too(db: Session) -> None:
    """DL-03 400 is a final answer — the same key gets the same 400, still nothing stored."""
    key = str(uuid.uuid4())
    text = "김씨에게 배추 3박스 외상"  # no amount → MISSING_REQUIRED_FIELD
    first = _parse(key, text=text)
    second = _parse(key, text=text)
    assert first.status_code == second.status_code == 400
    assert first.json() == second.json()
    assert db.query(LedgerEntry).count() == 0


def test_expired_key_runs_again(db: Session) -> None:
    """After 24h the key is forgotten, so the same key creates a new entry."""
    key = str(uuid.uuid4())
    assert _parse(key).status_code == 200
    record = db.query(IdempotencyRecord).one()
    record.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
    db.commit()

    res = _parse(key)
    assert res.status_code == 200
    assert db.query(LedgerEntry).count() == 2


def test_in_flight_key_is_409(db: Session) -> None:
    """A reserved key without a stored response (request still running) is not re-run."""
    key = str(uuid.uuid4())
    same_request = _request_hash("parse", {"utterance_text": UTTERANCE, "candidate": None})
    db.add(IdempotencyRecord(user_id="dev-user-1", key=key, request_hash=same_request))
    db.commit()
    res = _parse(key)
    assert res.status_code == 409
    assert res.json()["error_code"] == "IDEMPOTENCY_IN_PROGRESS"
    assert db.query(LedgerEntry).count() == 0


def test_abandoned_reservation_runs_again(db: Session) -> None:
    """A reservation left by a crashed worker must not block the key for 24h."""
    key = str(uuid.uuid4())
    db.add(
        IdempotencyRecord(
            user_id="dev-user-1", key=key, request_hash="x" * 64,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=61),
        )
    )
    db.commit()
    res = _parse(key)
    assert res.status_code == 200
    assert db.query(LedgerEntry).count() == 1
