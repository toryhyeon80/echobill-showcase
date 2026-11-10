"""Ledger list · unpaid balance · FIFO payments (SPEC §5.3 · DL-22). Confirmed entries only."""

from datetime import UTC, date, datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.dependencies import CurrentUserId
from app.models import LedgerEntry, PaymentRecord
from app.schemas.errors import ErrorResponse
from app.schemas.unpaid import (
    AllocatedEntry,
    CounterpartyDetailResponse,
    DailyCredit,
    LedgerListEntry,
    LedgerListResponse,
    MovedCreditRequest,
    PaymentRequest,
    PaymentView,
    UnpaidListResponse,
    UnpaidSummary,
)
from app.services.fifo import (
    KST,
    allocate,
    credit_entries,
    credit_entries_by_counterparty,
    kst_date,
    owed_age,
    paid_total,
    unpaid_by_counterparty,
)
from app.services.idempotency import run_idempotent
from app.services.moved_credit import MOVED, create_moved_credit

ledger_router = APIRouter()
dashboard_router = APIRouter()
payments_router = APIRouter()

@ledger_router.get("", response_model=LedgerListResponse)
def list_ledger(
    user_id: CurrentUserId,
    db: Annotated[Session, Depends(get_db)],
    month: Annotated[
        str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="KST 월 YYYY-MM, 없으면 이번 달")
    ] = None,
) -> LedgerListResponse:
    """
    Confirmed entries registered in one KST month + daily credit issued (calendar).

    Filtered by KST date in Python so SQLite and PostgreSQL agree on day boundaries;
    one merchant's month is small.
    """
    month = month or datetime.now(timezone.utc).astimezone(KST).strftime("%Y-%m")

    rows = db.scalars(
        select(LedgerEntry)
        .where(
            LedgerEntry.user_id == user_id,
            LedgerEntry.status == "confirmed",
            LedgerEntry.deleted_at.is_(None),
        )
        .order_by(LedgerEntry.created_at, LedgerEntry.id)
    )
    entries: list[LedgerListEntry] = []
    daily: dict[date, int] = {}
    for row in rows:
        day = kst_date(row.created_at)
        if day.strftime("%Y-%m") != month:
            continue
        entries.append(
            LedgerListEntry(
                entry_id=row.entry_id,
                status=row.status,
                counterparty=row.counterparty,
                items=row.items or [],
                total_amount=row.total_amount,
                credit=row.credit,
                utterance_text=row.utterance_text,
                created_at=row.created_at,
                kst_date=day,
                moved=row.parser_type == MOVED,
            )
        )
        # a moved credit's day is approximate, not a sale on that day — kept in the list, not in the calendar (#214)
        if row.credit and row.parser_type != MOVED:
            daily[day] = daily.get(day, 0) + (row.total_amount or 0)
    return LedgerListResponse(
        month=month,
        entries=entries,
        daily_credit=[DailyCredit(date=d, credit_amount=a) for d, a in sorted(daily.items())],
    )


@ledger_router.get("/counterparty/{name:path}", response_model=CounterpartyDetailResponse)
def counterparty_detail(
    name: str,
    user_id: CurrentUserId,
    db: Annotated[Session, Depends(get_db)],
) -> CounterpartyDetailResponse:
    """FIFO breakdown for one counterparty (exact name). Unknown name → zero balance, empty lists."""
    entries = credit_entries(db, user_id, name)
    paid = paid_total(db, user_id, name)
    allocations = allocate(entries, paid)
    payments = db.scalars(
        select(PaymentRecord)
        .where(
            PaymentRecord.user_id == user_id,
            PaymentRecord.counterparty == name,
            PaymentRecord.status == "valid",
        )
        .order_by(PaymentRecord.created_at, PaymentRecord.id)
    )
    credit_total = sum(a.entry.total_amount or 0 for a in allocations)
    oldest_days, owed_count = owed_age(allocations, kst_date(datetime.now(UTC)))
    return CounterpartyDetailResponse(
        counterparty=name,
        credit_total=credit_total,
        paid_total=paid,
        unpaid=credit_total - paid,
        oldest_days=oldest_days,
        owed_count=owed_count,
        entries=[
            AllocatedEntry(
                entry_id=a.entry.entry_id,
                kst_date=kst_date(a.entry.created_at),
                total_amount=a.entry.total_amount or 0,
                allocated=a.allocated,
                remaining=a.remaining,
                moved=a.entry.parser_type == MOVED,
            )
            for a in allocations
        ],
        payments=[
            PaymentView(amount=p.amount, kst_date=kst_date(p.created_at), created_at=p.created_at)
            for p in payments
        ],
    )


@dashboard_router.get("/unpaid", response_model=UnpaidListResponse)
def unpaid_dashboard(
    user_id: CurrentUserId,
    db: Annotated[Session, Depends(get_db)],
) -> UnpaidListResponse:
    """Counterparties with a remaining balance, largest first, each with its 경과일 (oldest still-owed entry)."""
    today = kst_date(datetime.now(UTC))
    entries = credit_entries_by_counterparty(db, user_id)
    rows = []
    for name, credit, paid in unpaid_by_counterparty(db, user_id):
        if credit - paid <= 0:
            continue
        oldest_days, owed_count = owed_age(allocate(entries.get(name, []), paid), today)
        rows.append(UnpaidSummary(counterparty=name, credit_total=credit, paid_total=paid, unpaid=credit - paid,
                                  oldest_days=oldest_days, owed_count=owed_count))
    rows.sort(key=lambda r: (-r.unpaid, r.counterparty))
    return UnpaidListResponse(total_unpaid=sum(r.unpaid for r in rows), counterparties=rows,
                              has_confirmed_entries=_has_confirmed_entries(db, user_id))


def _has_confirmed_entries(db: Session, user_id: str) -> bool:
    """Whether the owner has confirmed anything yet (any month, cash or credit; candidates and deleted rows don't count)."""
    return db.scalar(
        select(LedgerEntry.id).where(
            LedgerEntry.user_id == user_id,
            LedgerEntry.status == "confirmed",
            LedgerEntry.deleted_at.is_(None),
        ).limit(1)
    ) is not None


def _record_payment(body: PaymentRequest, user_id: str, db: Session) -> tuple[int, dict[str, Any]]:
    """Validate against the locked balance and insert. Returns (status, body)."""
    settings = get_settings()
    if not (settings.amount_min_krw <= body.amount <= settings.amount_max_krw):
        return 400, ErrorResponse(
            error_code="AMOUNT_OUT_OF_RANGE",
            message="금액이 허용 범위를 벗어났습니다.",
            problem_fields=["amount"],
        ).model_dump(mode="json")

    # lock this counterparty's credit rows so two deposits cannot both pass the check (PostgreSQL)
    entries = credit_entries(db, user_id, body.counterparty, for_update=True)
    credit_total = sum(e.total_amount or 0 for e in entries)
    paid = paid_total(db, user_id, body.counterparty)
    unpaid = credit_total - paid
    if body.amount > unpaid:
        db.rollback()  # release the row lock
        return 400, ErrorResponse(
            error_code="OVERPAYMENT",
            message="미수 잔액보다 많이 입금할 수 없습니다.",
            problem_fields=["amount"],
        ).model_dump(mode="json")

    db.add(PaymentRecord(user_id=user_id, counterparty=body.counterparty, amount=body.amount))
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    paid += body.amount
    oldest_days, owed_count = owed_age(allocate(entries, paid), kst_date(datetime.now(UTC)))
    return 200, UnpaidSummary(
        counterparty=body.counterparty,
        credit_total=credit_total,
        paid_total=paid,
        unpaid=credit_total - paid,
        oldest_days=oldest_days,
        owed_count=owed_count,
    ).model_dump(mode="json")


@payments_router.post(
    "",
    response_model=UnpaidSummary,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def create_payment(
    body: PaymentRequest,
    user_id: CurrentUserId,
    db: Annotated[Session, Depends(get_db)],
    x_idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """
    Record a deposit for one counterparty; FIFO allocation is recomputed on read.

    400 OVERPAYMENT if it exceeds the unpaid balance · 400 AMOUNT_OUT_OF_RANGE ·
    same X-Idempotency-Key replays the first response (DL-22 5-3).
    """
    return run_idempotent(
        db, user_id, x_idempotency_key, "payments", body.model_dump(mode="json"),
        lambda: _record_payment(body, user_id, db),
    )


def _move_credit(body: MovedCreditRequest, user_id: str, db: Session) -> tuple[int, dict[str, Any]]:
    """Validate the amount and store one moved credit row. Returns (status, body)."""
    settings = get_settings()
    counterparty = body.counterparty.strip()
    if not counterparty:
        return 400, ErrorResponse(error_code="MISSING_REQUIRED_FIELD", message="거래처를 입력해 주세요.",
                                  problem_fields=["counterparty"]).model_dump(mode="json")
    if not (settings.amount_min_krw <= body.amount <= settings.amount_max_krw):
        return 400, ErrorResponse(error_code="AMOUNT_OUT_OF_RANGE", message="금액이 허용 범위를 벗어났습니다.",
                                  problem_fields=["amount"]).model_dump(mode="json")
    row = create_moved_credit(db, user_id, counterparty, body.amount, body.when)
    return 200, LedgerListEntry(
        entry_id=row.entry_id, status=row.status, counterparty=row.counterparty, items=[],
        total_amount=row.total_amount, credit=row.credit, utterance_text=None, created_at=row.created_at,
        kst_date=kst_date(row.created_at), moved=True,
    ).model_dump(mode="json")


@ledger_router.post(
    "/moved-credit",
    response_model=LedgerListEntry,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def move_existing_credit(
    body: MovedCreditRequest,
    user_id: CurrentUserId,
    db: Annotated[Session, Depends(get_db)],
    x_idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """
    이미 있는 외상 옮기기 (PM 10/23): one counterparty's total, owed from about `when`, becomes a confirmed credit row.

    Counts in 받을 돈 and FIFO (oldest first); no items, so the tax mock refuses it. 400 MISSING_REQUIRED_FIELD ·
    400 AMOUNT_OUT_OF_RANGE · same X-Idempotency-Key replays the first response (DL-22 5-3).
    """
    return run_idempotent(
        db, user_id, x_idempotency_key, "moved_credit", body.model_dump(mode="json"),
        lambda: _move_credit(body, user_id, db),
    )
