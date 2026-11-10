"""confirm / correct / reject — the only path to `confirmed` (CLAUDE.md §4-1, DL-21, DL-22)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import FeedbackEvent, LedgerEntry

client = TestClient(app)

UTTERANCE = "김씨에게 배추 3박스 외상 30000원"
CORRECTED = {
    "counterparty": "김씨상회",
    "items": [{"name": "배추", "qty": 4, "unit": "박스", "unit_price": None, "amount": 40000}],
    "total_amount": 40000,
    "credit": True,
}


def _headers(user_id: str = "user-b", key: str | None = None) -> dict[str, str]:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": user_id}).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Idempotency-Key": key or str(uuid.uuid4())}


def _feedback(entry_id: str, body: dict, user_id: str = "user-b", key: str | None = None):
    return client.post(
        f"/api/v1/ledger/{entry_id}/feedback", json=body, headers=_headers(user_id, key)
    )


@pytest.fixture
def entry_id() -> str:
    """User B's stored candidate."""
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": UTTERANCE}, headers=_headers())
    assert res.status_code == 200
    return res.json()["entry_id"]


def _entry(db: Session, entry_id: str) -> LedgerEntry:
    db.expire_all()
    return db.query(LedgerEntry).filter_by(entry_id=entry_id).one()


def test_confirm_moves_candidate_to_confirmed(entry_id: str, db: Session) -> None:
    res = _feedback(entry_id, {"action": "confirm"})
    assert res.status_code == 200
    assert res.json()["status"] == "confirmed"
    assert _entry(db, entry_id).status == "confirmed"
    event = db.query(FeedbackEvent).one()
    assert event.action == "confirm"
    assert event.corrected_data is None
    assert event.ledger_entry_id == _entry(db, entry_id).id


def test_correct_updates_fields_and_stores_diff(entry_id: str, db: Session) -> None:
    res = _feedback(entry_id, {"action": "correct", "corrected": CORRECTED})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "confirmed"
    assert body["counterparty"] == "김씨상회"
    assert body["total_amount"] == 40000

    diff = db.query(FeedbackEvent).one().corrected_data
    assert diff["counterparty"] == {"before": "김씨", "after": "김씨상회"}
    assert diff["total_amount"] == {"before": 30000, "after": 40000}
    assert "credit" not in diff  # unchanged fields are not in the diff


def test_invalid_correction_is_400_and_changes_nothing(entry_id: str, db: Session) -> None:
    bad = {**CORRECTED, "total_amount": 99999}  # item sum 40000 ≠ total
    res = _feedback(entry_id, {"action": "correct", "corrected": bad})
    assert res.status_code == 400
    assert res.json()["error_code"] == "AMOUNT_MISMATCH"
    assert _entry(db, entry_id).status == "candidate"
    assert _entry(db, entry_id).total_amount == 30000
    assert db.query(FeedbackEvent).count() == 0


def test_correction_with_four_items_is_400_not_422(entry_id: str) -> None:
    item = CORRECTED["items"][0]
    bad = {**CORRECTED, "items": [item] * 4, "total_amount": 160000}
    res = _feedback(entry_id, {"action": "correct", "corrected": bad})
    assert res.status_code == 400
    assert res.json()["error_code"] == "EXCEED_MAX_ITEMS"


def test_reject_moves_candidate_to_rejected(entry_id: str, db: Session) -> None:
    res = _feedback(entry_id, {"action": "reject"})
    assert res.status_code == 200
    assert res.json()["status"] == "rejected"
    assert db.query(FeedbackEvent).one().action == "reject"


def test_second_decision_is_409(entry_id: str) -> None:
    assert _feedback(entry_id, {"action": "confirm"}).status_code == 200
    res = _feedback(entry_id, {"action": "reject"})
    assert res.status_code == 409
    assert res.json()["error_code"] == "ENTRY_NOT_CANDIDATE"


def test_retry_with_same_key_replays_200_not_409(entry_id: str, db: Session) -> None:
    """A network retry of the confirm must not turn into ENTRY_NOT_CANDIDATE."""
    key = str(uuid.uuid4())
    first = _feedback(entry_id, {"action": "confirm"}, key=key)
    second = _feedback(entry_id, {"action": "confirm"}, key=key)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert db.query(FeedbackEvent).count() == 1


def test_confirm_after_7_days_is_409_but_reject_works(entry_id: str, db: Session) -> None:
    entry = _entry(db, entry_id)
    entry.created_at = datetime.now(timezone.utc) - timedelta(days=7, minutes=1)
    db.commit()

    res = _feedback(entry_id, {"action": "confirm"})
    assert res.status_code == 409
    assert res.json()["error_code"] == "CANDIDATE_EXPIRED"
    assert _entry(db, entry_id).status == "candidate"

    assert _feedback(entry_id, {"action": "reject"}).status_code == 200


def test_other_user_feedback_is_404_and_b_unchanged(entry_id: str, db: Session) -> None:
    """DL-21 필수 테스트: A 토큰으로 B 장부 수정 → 404, 응답에 B 데이터 없음, B 장부 그대로."""
    res = _feedback(entry_id, {"action": "correct", "corrected": CORRECTED}, user_id="user-a")
    missing = _feedback(str(uuid.uuid4()), {"action": "confirm"}, user_id="user-a")

    assert res.status_code == 404
    assert res.json() == missing.json()
    for leaked in ("김씨", "30000", "user-b", entry_id):
        assert leaked not in res.text

    entry = _entry(db, entry_id)
    assert entry.status == "candidate"
    assert entry.counterparty == "김씨"
    assert db.query(FeedbackEvent).count() == 0


def test_soft_deleted_entry_feedback_is_404(entry_id: str, db: Session) -> None:
    entry = _entry(db, entry_id)
    entry.deleted_at = datetime.now(timezone.utc)
    db.commit()
    assert _feedback(entry_id, {"action": "confirm"}).status_code == 404


@pytest.mark.parametrize("body", [
    {"action": "correct"},
    {"action": "confirm", "corrected": CORRECTED},
    {"action": "approve"},
])
def test_malformed_body_is_422(entry_id: str, body: dict) -> None:
    assert _feedback(entry_id, body).status_code == 422


def test_feedback_requires_auth(entry_id: str) -> None:
    res = client.post(f"/api/v1/ledger/{entry_id}/feedback", json={"action": "confirm"})
    assert res.status_code == 401


def test_overlong_item_name_is_422_not_500(entry_id: str) -> None:
    """String caps match DB columns; they fail at request validation, never as a 500."""
    item = {**CORRECTED["items"][0], "name": "배" * 201}
    res = _feedback(entry_id, {"action": "correct", "corrected": {**CORRECTED, "items": [item]}})
    assert res.status_code == 422
