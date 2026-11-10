"""이미 있는 외상 옮기기 (PM 10/23): save, idempotency, range, ownership, 받을 돈·FIFO, ledger list, tax refused."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models import LedgerEntry, PaymentRecord
from app.services.fifo import allocate, credit_entries, kst_date, owed_age
from app.services.moved_credit import MOVED, create_moved_credit, moved_date

client = TestClient(app)


def _headers(user_id: str = "user-m", key: str | None = None) -> dict[str, str]:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": user_id}).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Idempotency-Key": key or str(uuid.uuid4())}


def _move(counterparty="한솥밥식당", amount=120000, when="last_month", user_id="user-m", key=None):
    return client.post("/api/v1/ledger/moved-credit", json={"counterparty": counterparty, "amount": amount,
                                                             "when": when}, headers=_headers(user_id, key))


@pytest.mark.parametrize("when, expected", [
    ("this_week", date(2026, 10, 20)),
    ("this_month", date(2026, 10, 1)),
    ("last_month", date(2026, 9, 15)),
    ("earlier", date(2026, 8, 24)),
])
def test_when_becomes_an_approximate_day(when: str, expected: date) -> None:
    assert moved_date(when, date(2026, 10, 23)) == expected


def test_last_month_in_january_is_december() -> None:
    assert moved_date("last_month", date(2027, 1, 5)) == date(2026, 12, 15)


def test_when_keeps_its_order_on_every_day_of_the_year() -> None:
    """병철 #214: on the 1st-3rd "이번 달" was later than "이번 주", and "그 전" fell in last month on the 31st."""
    for offset in range(366):
        today = date(2026, 1, 1) + timedelta(days=offset)
        week, month, last, earlier = (moved_date(w, today) for w in ("this_week", "this_month", "last_month", "earlier"))
        last_month_first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        assert week >= month >= last > earlier and earlier < last_month_first and week < today, today


def test_early_month_and_month_end_examples() -> None:
    assert moved_date("this_month", date(2026, 10, 2)) == date(2026, 9, 29)  # not 10/1, which is after 이번 주
    assert moved_date("earlier", date(2026, 10, 31)) == date(2026, 8, 31)  # not 9/1, which is last month


def test_moved_credit_goes_before_an_older_credit_already_in_the_app(db: Session) -> None:
    """병철 #214: "이번 주" moved after an older app record must still be paid first (FIFO)."""
    older = LedgerEntry(entry_id=str(uuid.uuid4()), user_id="user-m", status="confirmed", counterparty="한솥밥식당",
                        items=[], total_amount=30000, credit=True, created_at=datetime(2026, 9, 1, tzinfo=UTC))
    db.add(older)
    db.commit()
    row = create_moved_credit(db, "user-m", "한솥밥식당", 50000, "this_week", now=datetime(2026, 10, 23, tzinfo=UTC))
    assert row.created_at.replace(tzinfo=UTC) < datetime(2026, 9, 1, tzinfo=UTC)
    other = create_moved_credit(db, "user-m", "다른식당", 10000, "this_week", now=datetime(2026, 10, 23, tzinfo=UTC))
    assert kst_date(other.created_at) == date(2026, 10, 20)  # another counterparty keeps its approximate day


def test_moved_credit_skips_an_older_credit_already_paid(db: Session) -> None:
    """병철 #233: going before a fully paid 9/1 credit handed its payment to the moved row — 9/1 showed unpaid again
    and "이번 주" showed 52 days. Only credit still owed counts."""
    db.add(LedgerEntry(entry_id=str(uuid.uuid4()), user_id="user-m", status="confirmed", counterparty="한솥밥식당",
                       items=[], total_amount=30000, credit=True, created_at=datetime(2026, 9, 1, tzinfo=UTC)))
    db.add(PaymentRecord(user_id="user-m", counterparty="한솥밥식당", amount=30000))
    db.commit()
    row = create_moved_credit(db, "user-m", "한솥밥식당", 50000, "this_week", now=datetime(2026, 10, 23, tzinfo=UTC))
    assert kst_date(row.created_at) == date(2026, 10, 20)
    allocations = allocate(credit_entries(db, "user-m", "한솥밥식당"), 30000)
    assert [a.remaining for a in allocations] == [0, 50000]  # 9/1 stays paid
    assert owed_age(allocations, date(2026, 10, 23)) == (3, 1)


def test_ops_report_does_not_count_it_as_parsed_traffic(db: Session) -> None:
    from app.services.ops_metrics import compute
    row = create_moved_credit(db, "user-m", "한솥밥식당", 10000, "this_week")
    day = kst_date(row.created_at)
    assert compute(db, "test", day, day).candidates == 0


def test_saved_as_confirmed_credit_without_items(db: Session) -> None:
    res = _move()
    assert res.status_code == 200
    body = res.json()
    assert (body["moved"], body["status"], body["credit"], body["items"], body["total_amount"]) == (
        True, "confirmed", True, [], 120000)
    row = db.scalars(select(LedgerEntry)).one()
    assert (row.parser_type, row.utterance_text, row.counterparty) == (MOVED, None, "한솥밥식당")


def test_counts_in_unpaid_and_ends_first_use(db: Session) -> None:
    _move(amount=50000)
    dash = client.get("/api/v1/dashboard/unpaid", headers=_headers()).json()
    assert dash["total_unpaid"] == 50000 and dash["has_confirmed_entries"] is True
    assert dash["counterparties"][0]["counterparty"] == "한솥밥식당"
    # last month's 15th — 17~46 days back depending on today, so compare with the exact day (">= 30" failed 11/1~11/13)
    today = kst_date(datetime.now(UTC))
    assert dash["counterparties"][0]["oldest_days"] == (today - moved_date("last_month", today)).days


def test_payment_reduces_the_moved_credit_first(db: Session) -> None:
    newer = LedgerEntry(entry_id=str(uuid.uuid4()), user_id="user-m", status="confirmed", counterparty="한솥밥식당",
                        items=[], total_amount=30000, credit=True, created_at=datetime.now(UTC))
    db.add(newer)
    db.commit()
    _move(amount=50000, when="earlier")
    client.post("/api/v1/payments", json={"counterparty": "한솥밥식당", "amount": 50000}, headers=_headers())
    detail = client.get("/api/v1/ledger/counterparty/한솥밥식당", headers=_headers()).json()
    moved = [e for e in detail["entries"] if e["moved"]]
    assert moved[0]["remaining"] == 0 and detail["unpaid"] == 30000


def test_ledger_list_marks_it_in_its_month(db: Session) -> None:
    row = create_moved_credit(db, "user-m", "한솥밥식당", 10000, "last_month", now=datetime(2026, 10, 23, tzinfo=UTC))
    month = kst_date(row.created_at).strftime("%Y-%m")
    assert month == "2026-09"
    body = client.get(f"/api/v1/ledger?month={month}", headers=_headers()).json()
    assert [(e["moved"], e["items"]) for e in body["entries"]] == [(True, [])]
    assert body["daily_credit"] == []  # its day is approximate — no credit on the calendar (PM 10/26, #214)


def test_same_key_saves_once(db: Session) -> None:
    key = str(uuid.uuid4())
    assert _move(key=key).status_code == 200
    assert _move(key=key).status_code == 200
    assert len(db.scalars(select(LedgerEntry)).all()) == 1


def test_key_is_required() -> None:
    res = client.post("/api/v1/ledger/moved-credit", json={"counterparty": "가", "amount": 1000, "when": "earlier"},
                      headers={"Authorization": _headers()["Authorization"]})
    assert res.status_code == 400 and res.json()["error_code"] == "IDEMPOTENCY_KEY_REQUIRED"


@pytest.mark.parametrize("amount", [0, 100_000_001])
def test_amount_out_of_range_is_400_and_not_saved(amount: int, db: Session) -> None:
    res = _move(amount=amount)
    assert res.status_code == 400 and res.json()["error_code"] == "AMOUNT_OUT_OF_RANGE"
    assert db.scalars(select(LedgerEntry)).first() is None


def test_blank_counterparty_is_400(db: Session) -> None:
    res = _move(counterparty="   ")
    assert res.status_code == 400 and res.json()["problem_fields"] == ["counterparty"]


def test_unknown_when_is_422() -> None:
    assert _move(when="yesterday").status_code == 422


def test_other_owner_does_not_see_it(db: Session) -> None:
    _move(user_id="user-m")
    assert client.get("/api/v1/dashboard/unpaid", headers=_headers("user-x")).json()["total_unpaid"] == 0


def test_requires_login() -> None:
    res = client.post("/api/v1/ledger/moved-credit", json={"counterparty": "가", "amount": 1000, "when": "earlier"})
    assert res.status_code == 401


def test_tax_mock_refuses_a_moved_credit(db: Session) -> None:
    entry_id = _move().json()["entry_id"]
    client.post("/api/v1/tax/business", json={"business_number": "123-45-67891", "business_name": "김사장상회"},
                headers=_headers())
    res = client.post("/api/v1/tax/issue", json={"entry_id": entry_id}, headers=_headers())
    assert res.status_code == 409 and res.json()["error_code"] == "ENTRY_NOT_TAXABLE"
