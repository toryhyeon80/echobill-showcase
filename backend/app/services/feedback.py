"""Candidate → confirmed / rejected. The only path that writes `confirmed` (CLAUDE.md §4-1)."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import FeedbackEvent, LedgerEntry, Merchant
from app.schemas.ledger import CandidateFields, FeedbackRequest
from app.services.idempotency import as_utc
from app.services.ledger_store import get_owned_entry
from app.services.validation import ValidationFailure, validate_candidate

FIELD_KEYS = ("counterparty", "items", "total_amount", "credit")


@dataclass(frozen=True)
class FeedbackRejected:
    """Non-400 refusal: 404 (not found / not yours) or 409 (expired / already decided)."""

    status_code: int
    error_code: str
    message: str


NOT_FOUND = FeedbackRejected(404, "NOT_FOUND", "찾을 수 없습니다.")
EXPIRED = FeedbackRejected(
    409, "CANDIDATE_EXPIRED", "7일이 지난 후보는 확정할 수 없습니다. 다시 입력해 주세요."
)
NOT_CANDIDATE = FeedbackRejected(409, "ENTRY_NOT_CANDIDATE", "이미 확정되었거나 거절된 거래입니다.")


def _fields_of(entry: LedgerEntry) -> dict[str, Any]:
    """Stored DL-01 fields in JSON form (qty as string, like the stored items)."""
    return CandidateFields.model_validate(
        {k: getattr(entry, k) if k != "items" else (entry.items or []) for k in FIELD_KEYS}
    ).model_dump(mode="json")


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """corrected_data = changed top-level fields only (SPEC §6 JSON Diff)."""
    return {
        k: {"before": before[k], "after": after[k]} for k in FIELD_KEYS if before[k] != after[k]
    }


def _is_expired(entry: LedgerEntry, settings: Settings) -> bool:
    """DL-22 5-5: confirm is blocked `candidate_confirm_days` after creation (reject is not)."""
    limit = as_utc(entry.created_at) + timedelta(days=settings.candidate_confirm_days)
    return datetime.now(timezone.utc) > limit


def apply_feedback(
    db: Session,
    user_id: str,
    entry_id: str,
    body: FeedbackRequest,
    settings: Settings | None = None,
) -> LedgerEntry | ValidationFailure | FeedbackRejected:
    """
    Apply one decision atomically: entry status/fields + FeedbackEvent in a single commit.

    Returns the updated entry, a DL-03 ValidationFailure (400, nothing changed),
    or a FeedbackRejected (404/409, nothing changed).
    """
    settings = settings or get_settings()
    entry = get_owned_entry(db, user_id, entry_id, for_update=True)
    if entry is None:
        return NOT_FOUND
    if entry.status != "candidate":
        return NOT_CANDIDATE
    if body.action != "reject" and _is_expired(entry, settings):
        return EXPIRED

    corrected_data: dict[str, Any] | None = None
    if body.action == "correct":
        raw = body.corrected.model_dump(mode="json")
        failure = validate_candidate(raw, settings=settings)
        if failure:
            return failure
        before = _fields_of(entry)
        after = CandidateFields.model_validate(raw).model_dump(mode="json")
        corrected_data = _diff(before, after)
        entry.counterparty = after["counterparty"]
        entry.items = after["items"]
        entry.total_amount = after["total_amount"]
        entry.credit = after["credit"]

    entry.status = "rejected" if body.action == "reject" else "confirmed"
    merchant = db.get(Merchant, user_id)  # no row (dev-token user) = not agreed
    db.add(
        FeedbackEvent(
            user_id=user_id,
            ledger_entry_id=entry.id,
            action=body.action,
            corrected_data=corrected_data,
            ai_consent=bool(merchant and merchant.ai_consent),
        )
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(entry)
    return entry
