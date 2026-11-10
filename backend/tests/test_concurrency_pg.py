"""PostgreSQL-only: row locks really serialize money paths (SPEC §5.3 Row Lock, PM 3순위 #8).

SQLite ignores SELECT … FOR UPDATE, so these run only with TEST_DATABASE_URL=postgresql://….
Deterministic: transaction A holds the lock while request B is started; B must wait for A.
"""

import os
import threading
import time
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import LedgerEntry, PaymentRecord
from app.services.fifo import credit_entries

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="row locks need PostgreSQL",
)

client = TestClient(app)


def _headers(user_id: str = "user-b") -> dict[str, str]:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": user_id}).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())}


def _credit_entry(TestingSession, amount: int) -> None:
    with TestingSession() as s:
        s.add(
            LedgerEntry(
                entry_id=str(uuid.uuid4()), user_id="user-b", status="confirmed", counterparty="김씨",
                items=[], total_amount=amount, credit=True, created_at=datetime.now(timezone.utc),
            )
        )
        s.commit()


def test_concurrent_payment_waits_for_lock_and_sees_first_payment(session_factory) -> None:
    """Balance 10,000: A pays 8,000 while holding the lock; B (8,000) must block, then get OVERPAYMENT."""
    TestingSession = session_factory
    _credit_entry(TestingSession, 10000)
    headers = _headers()
    result: dict = {}

    tx_a = TestingSession()
    try:
        credit_entries(tx_a, "user-b", "김씨", for_update=True)  # A takes the row lock
        tx_a.add(PaymentRecord(user_id="user-b", counterparty="김씨", amount=8000))

        def pay_b() -> None:
            res = client.post("/api/v1/payments", json={"counterparty": "김씨", "amount": 8000}, headers=headers)
            result["status"], result["body"] = res.status_code, res.json()

        b = threading.Thread(target=pay_b)
        b.start()
        time.sleep(1.0)
        assert b.is_alive(), "payment B did not wait for A's row lock"
        tx_a.commit()  # release
    finally:
        tx_a.close()

    b.join(timeout=10)
    assert result["status"] == 400
    assert result["body"]["error_code"] == "OVERPAYMENT"
    with TestingSession() as s:
        assert sum(p.amount for p in s.query(PaymentRecord).all()) == 8000


def test_cs_delete_waits_for_payment_lock_without_deadlock(session_factory) -> None:
    """CS script locks in the payments order: it waits for an in-flight payment, then decides on its result."""
    from app.services.cs_soft_delete import soft_delete

    TestingSession = session_factory
    with TestingSession() as s:
        old = LedgerEntry(entry_id=str(uuid.uuid4()), user_id="user-b", status="confirmed", counterparty="김씨",
                          items=[], total_amount=10000, credit=True,
                          created_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
        new = LedgerEntry(entry_id=str(uuid.uuid4()), user_id="user-b", status="confirmed", counterparty="김씨",
                          items=[], total_amount=10000, credit=True,
                          created_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
        s.add_all([old, new])
        s.commit()
        new_id = new.entry_id
    result: dict = {}

    tx_a = TestingSession()
    try:
        credit_entries(tx_a, "user-b", "김씨", for_update=True)  # payment in flight
        tx_a.add(PaymentRecord(user_id="user-b", counterparty="김씨", amount=10000))

        def cs_delete() -> None:
            with TestingSession() as db:
                result["r"] = soft_delete(db, user_id="user-b", entry_id=new_id, reason="CS", actor="최병철",
                                          approved_by="최승현", dry_run=False)

        t = threading.Thread(target=cs_delete)
        t.start()
        time.sleep(1.0)
        assert t.is_alive(), "CS script did not wait for the payment's lock"
        tx_a.commit()
    finally:
        tx_a.close()
    t.join(timeout=10)
    # payment covers the older entry only, so the newer one is still deletable
    assert result["r"].code == "OK"
