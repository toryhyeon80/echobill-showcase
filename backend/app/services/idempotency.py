"""X-Idempotency-Key handling for state-changing POSTs (DL-22 5-3·5-6).

Same key + same request → the first response again (no second write).
Same key + different request → 409 IDEMPOTENCY_KEY_REUSED. Records expire after 24h.
Keys are scoped per user, so two users can never see each other's cached responses.
"""

import hashlib
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import IdempotencyRecord
from app.schemas.errors import ErrorResponse
from app.services import rate_limit

logger = logging.getLogger(__name__)

KEY_MAX_LEN = 128

# (status, body) or (status, body, trace) — trace (PM #23: post-process rule names only, no text)
# is kept on the record so a later repair submit can link back to it via repair_of
Handler = Callable[[], tuple[int, dict[str, Any]] | tuple[int, dict[str, Any], dict[str, Any] | None]]


def error_json(status_code: int, error_code: str, message: str) -> JSONResponse:
    """DL-03 error body without candidate fields."""
    body = ErrorResponse(error_code=error_code, message=message)
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes (stored as UTC); PostgreSQL returns aware ones."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _request_hash(scope: str, payload: dict[str, Any]) -> str:
    """Scope is part of the hash — reusing a key on another endpoint is a different request."""
    canonical = json.dumps({"scope": scope, "payload": payload}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reused() -> JSONResponse:
    return error_json(409, "IDEMPOTENCY_KEY_REUSED", "이미 처리된 요청입니다. 다시 입력해 주세요.")


def _in_progress() -> JSONResponse:
    """Not a re-entry case — the first request may still save; retry the same key later."""
    return error_json(409, "IDEMPOTENCY_IN_PROGRESS", "처리 중입니다. 잠시 후 다시 시도해 주세요.")


def _is_stale(record: IdempotencyRecord, settings: Settings) -> bool:
    """Completed records live 24h; a reservation with no response is abandoned after 60s."""
    age = datetime.now(timezone.utc) - as_utc(record.created_at)
    if record.status_code is None:
        return age > timedelta(seconds=settings.idempotency_pending_seconds)
    return age > timedelta(hours=settings.idempotency_ttl_hours)


def run_idempotent(
    db: Session,
    user_id: str,
    key: str | None,
    scope: str,
    payload: dict[str, Any],
    handler: Handler,
    settings: Settings | None = None,
) -> JSONResponse:
    """
    Run `handler` at most once per (user, key) and replay its response.

    The key is reserved before the handler runs, so a concurrent duplicate gets 409
    instead of a second write. 5xx responses and exceptions release the key so the
    client can retry. `handler` returns (status_code, JSON body).
    """
    settings = settings or get_settings()
    if not key or len(key) > KEY_MAX_LEN:
        return error_json(400, "IDEMPOTENCY_KEY_REQUIRED", "요청을 처리할 수 없습니다. 다시 시도해 주세요.")

    _maybe_purge(db, settings)
    request_hash = _request_hash(scope, payload)
    record = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user_id, IdempotencyRecord.key == key
        )
    )
    if record is not None and _is_stale(record, settings):
        db.delete(record)
        db.commit()
        record = None
    if record is not None:
        if record.request_hash != request_hash:
            logger.info("idempotency key reused with a different request: scope=%s", scope)
            return _reused()
        if record.status_code is None:
            # same request still running (e.g. client retried after a network timeout)
            return _in_progress()
        return JSONResponse(status_code=record.status_code, content=record.response_body)

    base_scope = scope.split(":", 1)[0]
    record = IdempotencyRecord(user_id=user_id, key=key, request_hash=request_hash, scope=base_scope)
    try:
        db.add(record)
        db.commit()
    except IntegrityError:
        db.rollback()  # a concurrent request with the same key reserved it first
        return _in_progress()

    # Cap only new requests (a replay above returned the stored answer for free). Counted after
    # our own reservation is committed, so parallel requests see each other and cannot all slip
    # under the limit — under a burst they err on the side of refusing.
    limited = rate_limit.check(db, user_id, base_scope, settings)
    if limited is not None:
        _release(db, record)
        return limited

    try:
        status_code, body, *extra = handler()
    except Exception:
        db.rollback()
        _release(db, record)
        raise

    if status_code >= 500:
        _release(db, record)
    else:
        record.status_code = status_code
        record.response_body = body
        record.postprocess_trace = extra[0] if extra else None
        db.commit()
    return JSONResponse(status_code=status_code, content=body)


_last_purge = 0.0  # monotonic seconds, per process


def purge_expired(db: Session, settings: Settings) -> int:
    """
    Delete records past the retention window. Kept ≥ 24h even if the replay TTL is shorter,
    because the daily rate limit counts them. Returns rows removed.
    """
    keep_hours = max(settings.idempotency_ttl_hours, 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=keep_hours)
    try:
        removed = db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.created_at < cutoff)).rowcount
        db.commit()
    except Exception:
        db.rollback()
        raise
    return removed or 0


def _maybe_purge(db: Session, settings: Settings) -> None:
    """Purge at most once per interval per process — no cron needed on Railway."""
    global _last_purge
    now = time.monotonic()
    if now - _last_purge < settings.idempotency_purge_interval_seconds:
        return
    _last_purge = now
    try:
        removed = purge_expired(db, settings)
    except Exception as exc:  # housekeeping must never fail the user's request
        logger.warning("idempotency purge skipped: %s", type(exc).__name__)
        return
    if removed:
        logger.info("purged %d expired idempotency records", removed)


def _release(db: Session, record: IdempotencyRecord) -> None:
    """Drop the reservation so a retry with the same key runs again."""
    try:
        db.delete(record)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("failed to release idempotency key reservation")
        raise
