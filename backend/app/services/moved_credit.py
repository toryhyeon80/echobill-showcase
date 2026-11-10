"""이미 있는 외상 옮기기 (PM 10/18 화면, 10/23 설계) — an owner moves credit kept elsewhere into 받을 돈.

One confirmed credit row per counterparty total: no items, no utterance, `parser_type = "moved"`. The owner typed and
submitted it, so it is not a parsed candidate waiting for confirmation (CLAUDE.md §4-1). It counts in 받을 돈 and FIFO
like any credit; the tax mock refuses it (no items to invoice). "언제쯤" becomes an approximate date so the moved credit
stands before new records in FIFO and 경과일 is roughly right.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from sqlalchemy.orm import Session

from app.models import LedgerEntry
from app.services.fifo import KST, allocate, credit_entries, paid_total
from app.services.idempotency import as_utc

MOVED = "moved"
MovedWhen = Literal["this_week", "this_month", "last_month", "earlier"]


def moved_date(when: MovedWhen, today: date) -> date:
    """KST day stored for "언제쯤" (PM 10/23): 이번 주 = 3일 전 · 이번 달 = 1일 · 지난달 = 15일 · 그 전 = 60일 전.

    Kept in order 이번 주 ≥ 이번 달 ≥ 지난달 ≥ 그 전 on every day (병철 #214): on the 1st-3rd "이번 달" is not later
    than "이번 주", and "그 전" stays before last month even at the end of a 31-day month.
    """
    this_week = today - timedelta(days=3)
    first = today.replace(day=1)
    last_month_first = (first - timedelta(days=1)).replace(day=1)
    if when == "this_week":
        return this_week
    if when == "this_month":
        return min(first, this_week)
    if when == "last_month":
        return last_month_first.replace(day=15)
    return min(today - timedelta(days=60), last_month_first - timedelta(days=1))


def create_moved_credit(db: Session, user_id: str, counterparty: str, amount: int, when: MovedWhen,
                        now: datetime | None = None) -> LedgerEntry:
    """Insert one moved credit row dated by `when` (09:00 KST) and return it.

    It must be paid first (FIFO), so it is dated just before that counterparty's oldest credit still owed in the app
    when that one is older (병철 #214). Fully paid credit is skipped: going before it would hand its payments to the
    moved row and show it unpaid again (병철 #233). 09:00 is always in the past: the day is at least 3 days ago.
    """
    today = (now or datetime.now(UTC)).astimezone(KST).date()
    created = datetime.combine(moved_date(when, today), time(9, 0), tzinfo=KST).astimezone(UTC)
    owed = [a.entry for a in allocate(credit_entries(db, user_id, counterparty), paid_total(db, user_id, counterparty))
            if a.remaining > 0]
    if owed and as_utc(owed[0].created_at) <= created:
        created = as_utc(owed[0].created_at) - timedelta(minutes=1)
    entry = LedgerEntry(
        entry_id=str(uuid.uuid4()),
        user_id=user_id,
        status="confirmed",
        counterparty=counterparty,
        items=[],
        total_amount=amount,
        credit=True,
        parser_type=MOVED,
        created_at=created,
    )
    db.add(entry)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(entry)
    return entry
