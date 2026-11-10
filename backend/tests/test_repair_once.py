"""repair_of 1회 (PM 10/15 내부 테스트: 장부 두 번 들어감) — one failed request makes at most one repaired candidate.

The FE kept "이어서 입력하기" after the repaired candidate was confirmed and sent the same repair_of again
with a new idempotency key. Before this, only a repair_of with a post-process trace was marked used, so
every other repair made a new candidate.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import IdempotencyRecord, LedgerEntry
from app.services.parse_mock import ParserResult

client = TestClient(app)
FIELDS = {
    "counterparty": "김씨",
    "items": [{"name": "배추", "qty": 3, "unit": "박스", "unit_price": None, "amount": 30000}],
    "total_amount": 30000,
    "credit": True,
}


def _headers(user_id: str = "user-b", key: str | None = None) -> dict[str, str]:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": user_id}).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Idempotency-Key": key or str(uuid.uuid4())}


def _missing_400(monkeypatch: pytest.MonkeyPatch, user_id: str = "user-b") -> str:
    """A plain 400 (no post-process rule behind it) — the case that used to repeat. Returns its key."""
    monkeypatch.setattr(
        "app.api.ledger.structure_utterance",
        lambda text, *a, **k: ParserResult(fields={**FIELDS, "credit": None}, utterance_text=text, parser_type="rule"),
    )
    key = str(uuid.uuid4())
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": "김씨 배추 3박스 3만원"},
                      headers=_headers(user_id, key))
    assert res.status_code == 400 and res.json()["error_code"] == "MISSING_REQUIRED_FIELD"
    return key


def _repair(repair_of: str, credit: bool = True, user_id: str = "user-b"):
    body = {"utterance_text": "김씨 배추 3박스 3만원", "candidate": {**FIELDS, "credit": credit}, "repair_of": repair_of}
    return client.post("/api/v1/ledger/parse", json=body, headers=_headers(user_id))  # a new key every time


def _decide(entry_id: str, action: str, user_id: str = "user-b"):
    return client.post(f"/api/v1/ledger/{entry_id}/feedback", json={"action": action}, headers=_headers(user_id))


def test_same_repair_of_twice_makes_one_candidate(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    key = _missing_400(monkeypatch)
    first, second = _repair(key), _repair(key)
    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()  # the same candidate back, not a new one
    assert db.query(LedgerEntry).count() == 1
    assert db.query(IdempotencyRecord).filter_by(key=key).one().repair_entry_id == first.json()["entry_id"]


def test_repair_after_confirm_is_409_and_saves_nothing(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    key = _missing_400(monkeypatch)
    entry_id = _repair(key).json()["entry_id"]
    assert _decide(entry_id, "confirm").status_code == 200
    res = _repair(key)
    assert res.status_code == 409
    assert res.json()["error_code"] == "REPAIR_ALREADY_CONFIRMED"
    assert db.query(LedgerEntry).count() == 1


def test_repair_after_reject_makes_a_new_candidate(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    key = _missing_400(monkeypatch)
    old = _repair(key).json()["entry_id"]
    assert _decide(old, "reject").status_code == 200
    res = _repair(key, credit=False)
    assert res.status_code == 200 and res.json()["entry_id"] != old and res.json()["credit"] is False
    assert db.query(IdempotencyRecord).filter_by(key=key).one().repair_entry_id == res.json()["entry_id"]
    assert _repair(key).json()["entry_id"] == res.json()["entry_id"]  # and that one is the link now


def test_soft_deleted_candidate_allows_a_new_one(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    from datetime import UTC, datetime

    key = _missing_400(monkeypatch)
    old = _repair(key).json()["entry_id"]
    db.query(LedgerEntry).filter_by(entry_id=old).one().deleted_at = datetime.now(UTC)  # CS 삭제
    db.commit()
    res = _repair(key)
    assert res.status_code == 200 and res.json()["entry_id"] != old


def test_failed_repair_leaves_no_link(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """A repair whose own values are wrong (400) made nothing — the next repair still makes the candidate."""
    key = _missing_400(monkeypatch)
    bad = {"utterance_text": "x", "candidate": {**FIELDS, "total_amount": 99999}, "repair_of": key}
    assert client.post("/api/v1/ledger/parse", json=bad, headers=_headers()).status_code == 400
    assert db.query(IdempotencyRecord).filter_by(key=key).one().repair_entry_id is None
    assert _repair(key).status_code == 200 and db.query(LedgerEntry).count() == 1


def test_another_users_repair_of_is_not_shared(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """user-b cannot reach user-a's link: no replay of a's candidate, no 409 — b's repair saves normally."""
    key = _missing_400(monkeypatch, user_id="user-a")
    a_entry = _repair(key, user_id="user-a").json()["entry_id"]
    _decide(a_entry, "confirm", user_id="user-a")
    res = _repair(key, user_id="user-b")
    assert res.status_code == 200 and res.json()["entry_id"] != a_entry
