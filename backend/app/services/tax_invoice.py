"""Mock tax invoice requests — state lives in TaxInvoiceEvent.nts_state (M9), ledger.tax_status mirrors it."""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import LedgerEntry, Merchant, TaxInvoiceEvent
from app.services import business_info, tax_mock
from app.services.idempotency import as_utc
from app.services.ledger_store import get_owned_entry
from app.services.moved_credit import MOVED

ACTIVE_STATES = ("requested", "issued")


@dataclass(frozen=True)
class TaxRejected:
    status_code: int
    error_code: str
    message: str
    problem_fields: tuple[str, ...] = ()


NOT_FOUND = TaxRejected(404, "NOT_FOUND", "찾을 수 없습니다.")
NOT_CONFIRMED = TaxRejected(409, "ENTRY_NOT_CONFIRMED", "확정된 거래만 모의 발행할 수 있습니다.")
# PM 10/23: a moved credit total has no items or trade date to invoice
NOT_TAXABLE = TaxRejected(409, "ENTRY_NOT_TAXABLE", "옮겨 온 외상은 모의 발행할 수 없어요.")
MISSING_FIELDS_MESSAGE = "거래처와 합계 금액이 있어야 모의 발행할 수 있습니다."
BUSINESS_REQUIRED = TaxRejected(
    400, "BUSINESS_INFO_REQUIRED", "사업자번호를 먼저 등록해 주세요.", ("business_number",)
)


def latest_event(db: Session, user_id: str, entry: LedgerEntry) -> TaxInvoiceEvent | None:
    """Most recent mock request for this entry (a failed one may be retried → several rows)."""
    return db.scalar(
        select(TaxInvoiceEvent)
        .where(TaxInvoiceEvent.user_id == user_id, TaxInvoiceEvent.ledger_entry_id == entry.id)
        .order_by(TaxInvoiceEvent.id.desc())
        .limit(1)
    )


def _set_state(db: Session, event: TaxInvoiceEvent, entry: LedgerEntry, result: tax_mock.MockResult) -> None:
    event.nts_state = result.state
    event.fail_reason = result.fail_reason
    event.updated_at = datetime.now(timezone.utc)
    entry.tax_status = result.state  # cache only (M9)


def refresh(db: Session, event: TaxInvoiceEvent, entry: LedgerEntry, settings: Settings) -> None:
    """Re-check a `requested` invoice — the 재조회 path after a delay or timeout."""
    if event.nts_state != "requested":
        return
    result = tax_mock.poll(as_utc(event.created_at), settings)
    if result.state != event.nts_state:
        _set_state(db, event, entry, result)
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise


def request_issue(
    db: Session, user_id: str, entry_id: str, settings: Settings | None = None
) -> tuple[LedgerEntry, TaxInvoiceEvent] | TaxRejected:
    """
    Ask the Mock to issue one confirmed entry. An active (requested/issued) invoice is returned
    as-is instead of issuing again — a retry never duplicates (기획서 §9.3). Failed → new attempt.
    """
    settings = settings or get_settings()
    entry = get_owned_entry(db, user_id, entry_id, for_update=True)
    if entry is None:
        return NOT_FOUND
    if entry.status != "confirmed":
        return NOT_CONFIRMED
    if entry.parser_type == MOVED:
        return NOT_TAXABLE

    existing = latest_event(db, user_id, entry)
    if existing is not None and existing.nts_state in ACTIVE_STATES:
        refresh(db, existing, entry, settings)
        return entry, existing

    missing = tuple(f for f in ("counterparty", "total_amount") if not getattr(entry, f))
    if missing:  # confirm validation requires both; guard so a 0원/no-buyer invoice is never issued
        return TaxRejected(400, "MISSING_REQUIRED_FIELD", MISSING_FIELDS_MESSAGE, missing)

    merchant = db.get(Merchant, user_id)
    if merchant is None or not merchant.business_number_enc:
        return BUSINESS_REQUIRED

    invoice = tax_mock.MockInvoice(
        supplier_business_number=business_info.decrypt(merchant.business_number_enc, settings),
        supplier_name=merchant.business_name,
        buyer_name=entry.counterparty,
        total_amount=entry.total_amount,
    )
    now = datetime.now(timezone.utc)
    event = TaxInvoiceEvent(user_id=user_id, ledger_entry_id=entry.id, created_at=now, updated_at=now)
    _set_state(db, event, entry, tax_mock.submit(invoice, settings))
    db.add(event)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(event)
    return entry, event


def invoice_status(
    db: Session, user_id: str, entry_id: str, settings: Settings | None = None
) -> tuple[LedgerEntry, TaxInvoiceEvent | None] | TaxRejected:
    """State after re-entry; `requested` is re-polled on every read."""
    settings = settings or get_settings()
    entry = get_owned_entry(db, user_id, entry_id)
    if entry is None:
        return NOT_FOUND
    event = latest_event(db, user_id, entry)
    if event is not None:
        refresh(db, event, entry, settings)
    return entry, event


def list_invoices(
    db: Session, user_id: str, settings: Settings | None = None
) -> list[tuple[LedgerEntry, TaxInvoiceEvent]]:
    """Latest invoice per entry for the caller's live (not soft-deleted) entries, newest first."""
    settings = settings or get_settings()
    rows = db.execute(
        select(TaxInvoiceEvent, LedgerEntry)
        .join(LedgerEntry, LedgerEntry.id == TaxInvoiceEvent.ledger_entry_id)
        .where(
            TaxInvoiceEvent.user_id == user_id,
            LedgerEntry.user_id == user_id,
            LedgerEntry.deleted_at.is_(None),
        )
        .order_by(TaxInvoiceEvent.id.desc())
    ).all()
    seen: set[int] = set()
    result: list[tuple[LedgerEntry, TaxInvoiceEvent]] = []
    for event, entry in rows:
        if entry.id in seen:
            continue
        seen.add(entry.id)
        refresh(db, event, entry, settings)
        result.append((entry, event))
    return result


def save_business_info(db: Session, user_id: str, number: str, name: str | None, settings: Settings | None = None) -> Merchant:
    """Create or update the caller's merchant row with the encrypted number (dev-token users may have none)."""
    merchant = db.get(Merchant, user_id) or Merchant(user_id=user_id)
    merchant.business_number_enc = business_info.encrypt(number, settings)
    if name is not None:
        merchant.business_name = name.strip() or None
    db.add(merchant)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return merchant
