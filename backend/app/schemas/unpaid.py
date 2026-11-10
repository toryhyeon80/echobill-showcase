"""Ledger list · unpaid · payment schemas (SPEC §5.3, DL-22). Dates are KST calendar days."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.ledger import LedgerEntryResponse


class LedgerListEntry(LedgerEntryResponse):
    """Confirmed entry with its KST calendar day."""

    kst_date: date
    # 이미 있는 외상 옮기기 (PM 10/23): a counterparty total moved from elsewhere — no items, date is approximate
    moved: bool = False


class DailyCredit(BaseModel):
    """Calendar cell: confirmed credit issued that KST day (payments do not reduce it)."""

    date: date
    credit_amount: int


class LedgerListResponse(BaseModel):
    """GET /ledger?month=YYYY-MM — confirmed entries and the credit calendar for one KST month."""

    month: str
    entries: list[LedgerListEntry]
    daily_credit: list[DailyCredit]


class UnpaidSummary(BaseModel):
    """One counterparty's balance. unpaid = credit_total − paid_total."""

    counterparty: str
    credit_total: int
    paid_total: int
    unpaid: int
    # PM 10/19 — "32일 경과 · 외상 2건": KST days since the oldest entry still owed after FIFO,
    # and how many entries are still owed (dashboard, counterparty detail and the payment reply). None / 0 when nothing is owed.
    oldest_days: int | None = None
    owed_count: int = 0


class UnpaidListResponse(BaseModel):
    """GET /dashboard/unpaid — counterparties with a balance, largest first."""

    total_unpaid: int
    counterparties: list[UnpaidSummary]
    # PM 10/21: any confirmed, not deleted entry in any month (cash too). False = the home's "처음 사용"
    # screen — one month's ledger cannot tell a new owner from one who only recorded last month.
    has_confirmed_entries: bool


class AllocatedEntry(BaseModel):
    """Credit entry after FIFO (derived on read, DL-22 5-4)."""

    entry_id: str
    kst_date: date
    total_amount: int
    allocated: int
    remaining: int
    moved: bool = False  # 이미 있는 외상 옮기기 (PM 10/23)


class PaymentView(BaseModel):
    """A recorded deposit (integer PK is not exposed, DL-23)."""

    amount: int
    kst_date: date
    created_at: datetime


class CounterpartyDetailResponse(UnpaidSummary):
    """GET /ledger/counterparty/{name} — FIFO breakdown for one counterparty."""

    entries: list[AllocatedEntry]
    payments: list[PaymentView]


class PaymentRequest(BaseModel):
    """Deposit against a counterparty. Range and overpayment are checked in code → 400 (not 422)."""

    counterparty: str = Field(..., min_length=1, max_length=200)
    amount: int


class MovedCreditRequest(BaseModel):
    """이미 있는 외상 옮기기 — one counterparty's total. Amount range is checked in code → 400 (not 422)."""

    counterparty: str = Field(..., min_length=1, max_length=200)
    amount: int
    # 언제쯤 — stored as an approximate KST day (this_week 3일 전 · this_month 1일 · last_month 15일 · earlier 60일 전)
    when: Literal["this_week", "this_month", "last_month", "earlier"]
