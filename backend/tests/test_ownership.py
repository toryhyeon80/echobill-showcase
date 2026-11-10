"""DL-21 4-4 ownership — another user's entry is 404 with no trace of its data."""

import logging
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import LedgerEntry

client = TestClient(app)

B_UTTERANCE = "박사장에게 무 7박스 외상 70000원"


def _auth(user_id: str) -> dict[str, str]:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": user_id}).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())}


@pytest.fixture
def b_entry_id() -> str:
    """User B's stored candidate."""
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": B_UTTERANCE}, headers=_auth("user-b"))
    assert res.status_code == 200
    return res.json()["entry_id"]


def test_owner_can_read_entry(b_entry_id: str) -> None:
    res = client.get(f"/api/v1/ledger/{b_entry_id}", headers=_auth("user-b"))
    assert res.status_code == 200
    assert res.json()["entry_id"] == b_entry_id
    assert res.json()["status"] == "candidate"


def test_other_user_read_is_404_without_data(b_entry_id: str, caplog: pytest.LogCaptureFixture) -> None:
    """A's token on B's entry → 404, same body as a missing entry, and nothing of B leaks."""
    with caplog.at_level(logging.WARNING):
        res = client.get(f"/api/v1/ledger/{b_entry_id}", headers=_auth("user-a"))
    missing = client.get("/api/v1/ledger/00000000-0000-0000-0000-000000000000", headers=_auth("user-a"))

    assert res.status_code == 404
    assert res.json() == missing.json()
    for leaked in ("박사장", "70000", "user-b", b_entry_id):
        assert leaked not in res.text

    # 서버 로그엔 불일치 기록, 사용자 ID는 마스킹
    mismatch = [r.getMessage() for r in caplog.records if "ownership mismatch" in r.getMessage()]
    assert len(mismatch) == 1
    assert "user-a" not in mismatch[0] and "user-b" not in mismatch[0]


def test_soft_deleted_entry_is_404(b_entry_id: str, db: Session) -> None:
    """Soft-deleted rows are 404 even for their owner."""
    entry = db.query(LedgerEntry).filter_by(entry_id=b_entry_id).one()
    entry.deleted_at = datetime.now(timezone.utc)
    db.commit()

    res = client.get(f"/api/v1/ledger/{b_entry_id}", headers=_auth("user-b"))
    assert res.status_code == 404


def test_read_requires_auth(b_entry_id: str) -> None:
    assert client.get(f"/api/v1/ledger/{b_entry_id}").status_code == 401
