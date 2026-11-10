"""Unpaid · FIFO · payments (SPEC §5.3, DL-22 5-1·5-4) and ledger list by KST month."""

import uuid
from datetime import UTC, date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import LedgerEntry, PaymentRecord
from app.services.fifo import allocate, kst_date, owed_age

client = TestClient(app)


def _headers(user_id: str = "user-b") -> dict[str, str]:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": user_id}).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())}


def _entry(
    db: Session,
    amount: int,
    created: datetime,
    *,
    user_id: str = "user-b",
    counterparty: str = "김씨",
    status: str = "confirmed",
    credit: bool = True,
    deleted: bool = False,
) -> LedgerEntry:
    entry = LedgerEntry(
        entry_id=str(uuid.uuid4()),
        user_id=user_id,
        status=status,
        counterparty=counterparty,
        items=[{"name": "배추", "qty": "1", "unit": "박스", "unit_price": None, "amount": amount}],
        total_amount=amount,
        credit=credit,
        created_at=created,
        deleted_at=datetime.now(timezone.utc) if deleted else None,
    )
    db.add(entry)
    db.commit()
    return entry


def _pay(amount: int, counterparty: str = "김씨", user_id: str = "user-b", headers=None):
    return client.post(
        "/api/v1/payments",
        json={"counterparty": counterparty, "amount": amount},
        headers=headers or _headers(user_id),
    )


def _utc(y: int, m: int, d: int, h: int = 3) -> datetime:
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def _days_since(created: datetime) -> int:
    return (kst_date(datetime.now(UTC)) - kst_date(created)).days


def test_owed_age_skips_fully_paid_and_counts_partly_paid() -> None:
    today = date(2026, 10, 19)
    entries = [
        LedgerEntry(total_amount=10000, created_at=_utc(2026, 9, 10)),  # fully paid
        LedgerEntry(total_amount=20000, created_at=_utc(2026, 9, 17)),  # partly paid → oldest still owed
        LedgerEntry(total_amount=5000, created_at=_utc(2026, 10, 7)),
    ]
    assert owed_age(allocate(entries, 15000), today) == (32, 2)
    assert owed_age(allocate(entries, 35000), today) == (None, 0)
    assert owed_age([], today) == (None, 0)


def test_owed_age_uses_kst_calendar_day() -> None:
    late_evening_kst = datetime(2026, 10, 18, 14, 59, tzinfo=UTC)  # 10/18 23:59 KST
    entry = LedgerEntry(total_amount=1000, created_at=late_evening_kst)
    assert owed_age(allocate([entry], 0), date(2026, 10, 19)) == (1, 1)


def test_dashboard_and_detail_carry_oldest_days(db: Session) -> None:
    oldest = _entry(db, 10000, _utc(2026, 9, 1))
    _entry(db, 20000, _utc(2026, 9, 20))
    _entry(db, 5000, _utc(2026, 9, 5), counterparty="박씨")
    _entry(db, 3000, _utc(2026, 8, 1), counterparty="박씨", credit=False)  # cash: never 경과일
    _pay(10000)  # clears 김씨's oldest

    rows = {r["counterparty"]: r for r in client.get("/api/v1/dashboard/unpaid", headers=_headers()).json()["counterparties"]}
    assert rows["김씨"]["owed_count"] == 1
    assert rows["김씨"]["oldest_days"] == _days_since(_utc(2026, 9, 20))
    assert rows["박씨"] == {**rows["박씨"], "oldest_days": _days_since(_utc(2026, 9, 5)), "owed_count": 1}

    detail = client.get("/api/v1/ledger/counterparty/김씨", headers=_headers()).json()
    assert (detail["oldest_days"], detail["owed_count"]) == (_days_since(_utc(2026, 9, 20)), 1)
    assert oldest.entry_id in {e["entry_id"] for e in detail["entries"] if e["remaining"] == 0}


def test_unknown_counterparty_detail_has_no_age(db: Session) -> None:
    detail = client.get("/api/v1/ledger/counterparty/없는가게", headers=_headers()).json()
    assert (detail["oldest_days"], detail["owed_count"]) == (None, 0)


def test_allocate_is_oldest_first() -> None:
    entries = [
        LedgerEntry(total_amount=10000), LedgerEntry(total_amount=20000), LedgerEntry(total_amount=5000)
    ]
    result = allocate(entries, 25000)
    assert [(a.allocated, a.remaining) for a in result] == [(10000, 0), (15000, 5000), (0, 5000)]


def test_payment_reduces_unpaid_fifo(db: Session) -> None:
    old = _entry(db, 10000, _utc(2026, 9, 1))
    new = _entry(db, 20000, _utc(2026, 9, 2))

    res = _pay(15000)
    assert res.status_code == 200
    assert res.json() == {"counterparty": "김씨", "credit_total": 30000, "paid_total": 15000, "unpaid": 15000,
                          "oldest_days": _days_since(new.created_at), "owed_count": 1}  # old one fully paid

    detail = client.get("/api/v1/ledger/counterparty/김씨", headers=_headers()).json()
    by_id = {e["entry_id"]: e for e in detail["entries"]}
    assert by_id[old.entry_id]["remaining"] == 0
    assert by_id[new.entry_id]["allocated"] == 5000
    assert by_id[new.entry_id]["remaining"] == 15000
    assert detail["unpaid"] == 15000
    assert [p["amount"] for p in detail["payments"]] == [15000]


def test_same_created_at_orders_by_pk(db: Session) -> None:
    same = _utc(2026, 9, 1)
    first = _entry(db, 10000, same)
    second = _entry(db, 10000, same)
    _pay(10000)
    detail = client.get("/api/v1/ledger/counterparty/김씨", headers=_headers()).json()
    by_id = {e["entry_id"]: e for e in detail["entries"]}
    assert by_id[first.entry_id]["remaining"] == 0
    assert by_id[second.entry_id]["remaining"] == 10000


def test_late_confirmed_older_entry_is_paid_first_on_reread(db: Session) -> None:
    """Allocation is derived, so an older entry confirmed later takes the payment on the next read."""
    newer = _entry(db, 10000, _utc(2026, 9, 5))
    older = _entry(db, 10000, _utc(2026, 9, 1), status="candidate")
    _pay(10000)

    older.status = "confirmed"
    db.commit()
    detail = client.get("/api/v1/ledger/counterparty/김씨", headers=_headers()).json()
    by_id = {e["entry_id"]: e for e in detail["entries"]}
    assert by_id[older.entry_id]["remaining"] == 0
    assert by_id[newer.entry_id]["remaining"] == 10000


def test_overpayment_is_400_and_not_stored(db: Session) -> None:
    _entry(db, 10000, _utc(2026, 9, 1))
    res = _pay(10001)
    assert res.status_code == 400
    assert res.json()["error_code"] == "OVERPAYMENT"
    assert res.json()["problem_fields"] == ["amount"]
    assert db.query(PaymentRecord).count() == 0


def test_candidate_rejected_cash_and_deleted_are_excluded(db: Session) -> None:
    _entry(db, 10000, _utc(2026, 9, 1))
    _entry(db, 50000, _utc(2026, 9, 1), status="candidate")
    _entry(db, 50000, _utc(2026, 9, 1), status="rejected")
    _entry(db, 50000, _utc(2026, 9, 1), credit=False)
    _entry(db, 50000, _utc(2026, 9, 1), deleted=True)

    unpaid = client.get("/api/v1/dashboard/unpaid", headers=_headers()).json()
    assert unpaid == {
        "total_unpaid": 10000,
        "counterparties": [{"counterparty": "김씨", "credit_total": 10000, "paid_total": 0, "unpaid": 10000,
                            "oldest_days": _days_since(_utc(2026, 9, 1)), "owed_count": 1}],  # excluded rows never age it
        "has_confirmed_entries": True,
    }
    assert _pay(10001).json()["error_code"] == "OVERPAYMENT"


@pytest.mark.parametrize("amount", [0, -1000, 100_000_001])
def test_payment_amount_out_of_range_is_400(db: Session, amount: int) -> None:
    _entry(db, 10000, _utc(2026, 9, 1))
    res = _pay(amount)
    assert res.status_code == 400
    assert res.json()["error_code"] == "AMOUNT_OUT_OF_RANGE"


def test_payment_retry_with_same_key_records_once(db: Session) -> None:
    _entry(db, 10000, _utc(2026, 9, 1))
    headers = _headers()
    first = _pay(4000, headers=headers)
    second = _pay(4000, headers=headers)
    assert first.json() == second.json()
    assert db.query(PaymentRecord).count() == 1


def test_payment_requires_idempotency_key(db: Session) -> None:
    _entry(db, 10000, _utc(2026, 9, 1))
    headers = _headers()
    del headers["X-Idempotency-Key"]
    assert _pay(1000, headers=headers).status_code == 400


def test_fully_paid_counterparty_leaves_dashboard(db: Session) -> None:
    _entry(db, 10000, _utc(2026, 9, 1))
    _entry(db, 7000, _utc(2026, 9, 1), counterparty="박사장")
    _pay(10000)
    body = client.get("/api/v1/dashboard/unpaid", headers=_headers()).json()
    assert [c["counterparty"] for c in body["counterparties"]] == ["박사장"]
    assert body["total_unpaid"] == 7000


def test_other_users_data_is_invisible(db: Session) -> None:
    """A sees none of B's balance and cannot pay against it (overpayment, B untouched)."""
    _entry(db, 10000, _utc(2026, 9, 1), user_id="user-b")

    assert client.get("/api/v1/dashboard/unpaid", headers=_headers("user-a")).json()["total_unpaid"] == 0
    detail = client.get("/api/v1/ledger/counterparty/김씨", headers=_headers("user-a")).json()
    assert detail["entries"] == [] and detail["unpaid"] == 0
    assert _pay(5000, user_id="user-a").json()["error_code"] == "OVERPAYMENT"
    assert db.query(PaymentRecord).count() == 0


def test_ledger_list_uses_kst_day_boundary(db: Session) -> None:
    """KST 23:59 on 9/30 is September; KST 00:00 on 10/1 is October (DL-22 5-1)."""
    sept = _entry(db, 10000, datetime(2026, 9, 30, 14, 59, tzinfo=timezone.utc))  # KST 23:59
    octo = _entry(db, 20000, datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc))  # KST 10/1 00:00

    s = client.get("/api/v1/ledger?month=2026-09", headers=_headers()).json()
    o = client.get("/api/v1/ledger?month=2026-10", headers=_headers()).json()
    assert [e["entry_id"] for e in s["entries"]] == [sept.entry_id]
    assert [e["entry_id"] for e in o["entries"]] == [octo.entry_id]
    assert s["entries"][0]["kst_date"] == "2026-09-30"
    assert o["daily_credit"] == [{"date": "2026-10-01", "credit_amount": 20000}]


def test_ledger_list_calendar_keeps_issued_amount_after_payment(db: Session) -> None:
    """Calendar shows credit issued that day — payments do not reduce it (SPEC §5.3)."""
    _entry(db, 10000, _utc(2026, 9, 10))
    _entry(db, 5000, _utc(2026, 9, 10), credit=False)
    _entry(db, 3000, _utc(2026, 9, 10), status="candidate")
    _pay(10000)
    body = client.get("/api/v1/ledger?month=2026-09", headers=_headers()).json()
    assert body["daily_credit"] == [{"date": "2026-09-10", "credit_amount": 10000}]
    assert len(body["entries"]) == 2  # confirmed only (credit + cash)


def test_ledger_list_bad_month_is_422() -> None:
    assert client.get("/api/v1/ledger?month=2026-13", headers=_headers()).status_code == 422


def test_ledger_list_defaults_to_current_kst_month() -> None:
    body = client.get("/api/v1/ledger", headers=_headers()).json()
    assert len(body["month"]) == 7 and body["entries"] == []


def test_confirm_via_api_shows_up_in_unpaid() -> None:
    """End-to-end: parse → confirm → unpaid → pay → cleared."""
    parsed = client.post(
        "/api/v1/ledger/parse",
        json={"utterance_text": "김씨에게 배추 3박스 외상 30000원"},
        headers=_headers(),
    ).json()
    before = client.get("/api/v1/dashboard/unpaid", headers=_headers()).json()
    assert before["total_unpaid"] == 0  # candidate is not counted

    client.post(f"/api/v1/ledger/{parsed['entry_id']}/feedback", json={"action": "confirm"}, headers=_headers())
    assert client.get("/api/v1/dashboard/unpaid", headers=_headers()).json()["total_unpaid"] == 30000

    assert _pay(30000).json()["unpaid"] == 0
    assert client.get("/api/v1/dashboard/unpaid", headers=_headers()).json()["total_unpaid"] == 0


def test_unpaid_endpoints_require_auth() -> None:
    assert client.get("/api/v1/dashboard/unpaid").status_code == 401
    assert client.get("/api/v1/ledger").status_code == 401
    assert client.post("/api/v1/payments", json={"counterparty": "김씨", "amount": 1}).status_code == 401


def test_counterparty_name_with_slash(db: Session) -> None:
    """Names are free text (no alias merging) — a "/" must still reach the detail route."""
    _entry(db, 10000, _utc(2026, 9, 1), counterparty="김사장/2호점")
    for path in ("김사장/2호점", "김사장%2F2호점"):
        res = client.get(f"/api/v1/ledger/counterparty/{path}", headers=_headers())
        assert res.status_code == 200
        assert res.json()["counterparty"] == "김사장/2호점"
        assert res.json()["unpaid"] == 10000


def test_has_confirmed_entries_false_for_a_new_owner(db: Session) -> None:
    """PM 10/21: the home's 처음 사용 screen — nothing confirmed yet (a candidate or a deleted row doesn't count)."""
    _entry(db, 10000, _utc(2026, 9, 1), status="candidate")
    _entry(db, 10000, _utc(2026, 9, 1), deleted=True)
    body = client.get("/api/v1/dashboard/unpaid", headers=_headers()).json()
    assert body["has_confirmed_entries"] is False
    assert (body["total_unpaid"], body["counterparties"]) == (0, [])


def test_has_confirmed_entries_true_with_only_cash_from_an_earlier_month(db: Session) -> None:
    """A cash-only owner who recorded last month is not new — one month's ledger cannot tell (외상 없음, not 처음 사용)."""
    _entry(db, 10000, _utc(2026, 8, 1), credit=False)
    body = client.get("/api/v1/dashboard/unpaid", headers=_headers()).json()
    assert body["has_confirmed_entries"] is True
    assert body["total_unpaid"] == 0


def test_has_confirmed_entries_is_per_owner(db: Session) -> None:
    _entry(db, 10000, _utc(2026, 9, 1), user_id="user-a")
    assert client.get("/api/v1/dashboard/unpaid", headers=_headers("user-b")).json()["has_confirmed_entries"] is False
