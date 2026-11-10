"""Unpaid balance · FIFO allocation — derived on every read, never stored (DL-22 5-4, SPEC §5.3).

Only confirmed, credit (외상), not soft-deleted entries count. Order: created_at ↑, then PK ↑.
Payments are per counterparty (exact name — no alias merging, 기획서 §4) and not 1:1 with entries.
"""

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import LedgerEntry, PaymentRecord
from app.services.idempotency import as_utc

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class Allocation:
    """One credit entry after FIFO: how much of it the payments covered."""

    entry: LedgerEntry
    allocated: int
    remaining: int


def kst_date(value: datetime) -> date:
    """Calendar day in KST (DL-22 5-1): KST 23:59 is that day, 00:00 the next."""
    return as_utc(value).astimezone(KST).date()


def _credit_filter(user_id: str):
    return (
        LedgerEntry.user_id == user_id,
        LedgerEntry.status == "confirmed",
        LedgerEntry.credit.is_(True),
        LedgerEntry.deleted_at.is_(None),
    )


def credit_entries(
    db: Session, user_id: str, counterparty: str, *, for_update: bool = False
) -> list[LedgerEntry]:
    """Confirmed credit entries of one counterparty in FIFO order."""
    query = (
        select(LedgerEntry)
        .where(*_credit_filter(user_id), LedgerEntry.counterparty == counterparty)
        .order_by(LedgerEntry.created_at, LedgerEntry.id)
    )
    if for_update:
        query = query.with_for_update()
    return list(db.scalars(query))


def credit_entries_by_counterparty(db: Session, user_id: str) -> dict[str, list[LedgerEntry]]:
    """All confirmed credit entries of a user, grouped by counterparty, each list in FIFO order (one query)."""
    grouped: dict[str, list[LedgerEntry]] = {}
    query = select(LedgerEntry).where(*_credit_filter(user_id)).order_by(LedgerEntry.created_at, LedgerEntry.id)
    for entry in db.scalars(query):
        if entry.counterparty:
            grouped.setdefault(entry.counterparty, []).append(entry)
    return grouped


def paid_total(db: Session, user_id: str, counterparty: str) -> int:
    """Sum of valid payments to one counterparty."""
    total = db.scalar(
        select(func.coalesce(func.sum(PaymentRecord.amount), 0)).where(
            PaymentRecord.user_id == user_id,
            PaymentRecord.counterparty == counterparty,
            PaymentRecord.status == "valid",
        )
    )
    return int(total or 0)


def owed_age(allocations: list[Allocation], today: date) -> tuple[int | None, int]:
    """(KST days since the oldest entry still owed, number of entries still owed) after FIFO.

    Payments clear the oldest entries first, so a partly paid entry still counts; (None, 0) when all paid.
    """
    owed = [a for a in allocations if a.remaining > 0]
    if not owed:
        return None, 0
    return max(0, (today - kst_date(owed[0].entry.created_at)).days), len(owed)


def allocate(entries: list[LedgerEntry], paid: int) -> list[Allocation]:
    """Spend `paid` over entries oldest first. Pure — the caller supplies FIFO order."""
    left = paid
    result: list[Allocation] = []
    for entry in entries:
        amount = entry.total_amount or 0
        used = min(amount, left)
        left -= used
        result.append(Allocation(entry=entry, allocated=used, remaining=amount - used))
    return result


def unpaid_by_counterparty(db: Session, user_id: str) -> list[tuple[str, int, int]]:
    """(counterparty, credit_total, paid_total) for every counterparty with credit or payments."""
    credit_rows = db.execute(
        select(LedgerEntry.counterparty, func.coalesce(func.sum(LedgerEntry.total_amount), 0))
        .where(*_credit_filter(user_id))
        .group_by(LedgerEntry.counterparty)
    ).all()
    paid_rows = db.execute(
        select(PaymentRecord.counterparty, func.coalesce(func.sum(PaymentRecord.amount), 0))
        .where(PaymentRecord.user_id == user_id, PaymentRecord.status == "valid")
        .group_by(PaymentRecord.counterparty)
    ).all()
    credit = {name: int(total) for name, total in credit_rows if name}
    paid = {name: int(total) for name, total in paid_rows}
    names = sorted(set(credit) | set(paid))
    return [(name, credit.get(name, 0), paid.get(name, 0)) for name in names]
