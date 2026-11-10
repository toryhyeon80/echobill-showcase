"""Ledger persistence — candidate rows only (confirmed comes from the feedback API)."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import IdempotencyRecord
from app.models.ledger import LedgerEntry
from app.schemas.ledger import CandidateFields
from app.services.parse_mock import ParserResult

logger = logging.getLogger(__name__)


def _mask(user_id: str) -> str:
    """Keep the last 4 chars only — user IDs are Kakao member numbers."""
    return f"***{user_id[-4:]}" if len(user_id) > 4 else "***"


def get_owned_entry(
    db: Session, user_id: str, entry_id: str, *, for_update: bool = False
) -> LedgerEntry | None:
    """
    Fetch an entry by public ID, scoped to the owner from the start (DL-21 4-4).

    None covers missing, another user's and soft-deleted rows alike — callers answer 404
    without telling them apart. Every read/update/feedback of one entry goes through here.
    `for_update` row-locks it on PostgreSQL for state changes (ignored by SQLite).
    """
    query = select(LedgerEntry).where(
        LedgerEntry.entry_id == entry_id,
        LedgerEntry.user_id == user_id,
        LedgerEntry.deleted_at.is_(None),
    )
    if for_update:
        query = query.with_for_update()
    entry = db.scalar(query)
    if entry is None:
        owner = db.scalar(select(LedgerEntry.user_id).where(LedgerEntry.entry_id == entry_id))
        if owner is not None and owner != user_id:
            # 서버 로그에만 소유권 불일치 기록 (마스킹) — 응답은 404로 동일
            logger.warning(
                "ledger ownership mismatch: entry=%s requester=%s owner=%s",
                entry_id, _mask(user_id), _mask(owner),
            )
    return entry


def create_candidate(
    db: Session,
    user_id: str,
    fields: CandidateFields,
    result: ParserResult,
    *,
    consume_repair_key: str | None = None,
    repair_link: IdempotencyRecord | None = None,
) -> LedgerEntry:
    """Insert a validated candidate (DL-22 5-2) and return the row with its public entry_id.

    `consume_repair_key`: clear that repair_of trace in the SAME commit (PM #71) — the entry and
    the "link used" mark succeed or fail together, so a retry can't create the entry twice.
    `repair_link`: the repair_of request's record — it gets this entry_id in the same commit (PM 10/15),
    so the next repair with the same repair_of finds this candidate instead of making another.
    """
    entry = LedgerEntry(
        entry_id=str(uuid.uuid4()),
        user_id=user_id,
        status="candidate",
        counterparty=fields.counterparty,
        items=[item.model_dump(mode="json") for item in fields.items],
        total_amount=fields.total_amount,
        credit=fields.credit,
        utterance_text=result.utterance_text,
        parser_type=result.parser_type,
        is_fallback_used=result.is_fallback_used,
        processing_time_ms=result.processing_time_ms,
        ai_model_used=result.ai_model_used,
        postprocess_version=result.postprocess_version,
        postprocess_applied=list(result.postprocess_applied) if result.postprocess_version else None,
    )
    try:
        db.add(entry)
        if consume_repair_key:
            _clear_repair_trace(db, user_id, consume_repair_key)
        if repair_link is not None:
            repair_link.repair_entry_id = entry.entry_id
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(entry)
    return entry


REPAIRABLE_SCOPES = ("parse", "parse-audio")


def repair_record(db: Session, user_id: str, key: str | None, *, for_update: bool = False) -> IdempotencyRecord | None:
    """
    The caller's own parse/parse-audio record named by repair_of, within the idempotency window.

    Scoped to user_id from the start, like get_owned_entry: another user's key, an unknown key or an
    expired record all look the same — None, no error, nothing revealed. `for_update` row-locks it on
    PostgreSQL so two repair submits for one failed request run one after the other (ignored by SQLite).
    """
    if not key:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=get_settings().idempotency_ttl_hours)
    query = select(IdempotencyRecord).where(
        IdempotencyRecord.user_id == user_id,
        IdempotencyRecord.key == key,
        IdempotencyRecord.scope.in_(REPAIRABLE_SCOPES),
        IdempotencyRecord.created_at >= cutoff,
    )
    if for_update:
        query = query.with_for_update()
    return db.scalar(query)


def repair_trace(record: IdempotencyRecord | None) -> dict | None:
    """Post-process trace of the caller's own parse 400 (PM #23 repair_of); None without one."""
    trace = record.postprocess_trace if record else None
    if not trace or not trace.get("rules"):
        return None
    return {"version": trace.get("version"), "rules": list(trace["rules"])}


def _clear_repair_trace(db: Session, user_id: str, key: str) -> None:
    """Mark a repair_of link as used (no commit — the caller commits it with the entry)."""
    record = db.scalar(
        select(IdempotencyRecord).where(IdempotencyRecord.user_id == user_id, IdempotencyRecord.key == key)
    )
    if record is not None and record.postprocess_trace:
        record.postprocess_trace = None
