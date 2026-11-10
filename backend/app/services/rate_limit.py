"""Per-user caps on the costly paths — /parse (LLM), /parse-audio (STT + LLM) (PM #14) and /voice/speak (TTS).

Counts the caller's idempotency records per scope (one per new request, 400s included, replays and
released 5xx not), so no extra table is needed. Text and voice have separate env caps
(PARSE_LIMIT_* / AUDIO_LIMIT_*), and so has TTS (SPEAK_LIMIT_*); 0 disables a window until D09 sets numbers.
"""

import math
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import IdempotencyRecord
from app.schemas.errors import ErrorResponse

# scope → settings prefix. Each scope has its own caps and its own count (voice never uses the text budget)
LIMIT_PREFIX = {"parse": "parse_limit", "parse-audio": "audio_limit", "voice-answer": "audio_limit",
                "speak": "speak_limit"}
# scopes that share one budget: every voice request costs an STT call (DL-31 answers use the voice cap)
COUNTED_SCOPES = {"parse": ("parse",), "parse-audio": ("parse-audio", "voice-answer"),
                  "voice-answer": ("parse-audio", "voice-answer"), "speak": ("speak",)}
_WINDOWS = (("minute", 60), ("day", 86_400))  # "day" = rolling 24h, not the KST calendar day


def check(db: Session, user_id: str, scope: str, settings: Settings) -> JSONResponse | None:
    """429 RATE_LIMITED with Retry-After when a window is over its limit, else None.

    Called after the caller's own reservation is saved, so `used` includes this request.
    """
    prefix = LIMIT_PREFIX.get(scope)
    if prefix is None:
        return None
    now = datetime.now(timezone.utc)
    for name, seconds in _WINDOWS:
        limit = getattr(settings, f"{prefix}_per_{name}")
        if limit <= 0:
            continue
        since = now - timedelta(seconds=seconds)
        used, oldest = db.execute(
            select(func.count(IdempotencyRecord.id), func.min(IdempotencyRecord.created_at)).where(
                IdempotencyRecord.user_id == user_id,
                IdempotencyRecord.scope.in_(COUNTED_SCOPES[scope]),
                IdempotencyRecord.created_at >= since,
            )
        ).one()
        if used > limit:
            oldest = oldest if oldest.tzinfo else oldest.replace(tzinfo=timezone.utc)
            retry_after = max(1, math.ceil((oldest + timedelta(seconds=seconds) - now).total_seconds()))
            body = ErrorResponse(
                error_code="RATE_LIMITED",
                message="요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
            ).model_dump(mode="json")
            return JSONResponse(status_code=429, content=body, headers={"Retry-After": str(retry_after)})
    return None


def count_use(db: Session, user_id: str, scope: str, settings: Settings) -> JSONResponse | None:
    """
    Count one call on a path with no idempotency key (nothing changes, e.g. /voice/speak) under the same caps.

    Saves a usage-only record (random key, no response) so the same count and 24h purge apply, then checks
    the caps; over a cap → the record is removed and the 429 returned. Unlike a released 5xx, a call that
    fails afterwards stays counted: a timed-out vendor call keeps running (and billing) in the background,
    so a slow vendor must not open a way around the cap.
    """
    record = IdempotencyRecord(user_id=user_id, key=f"{scope}:{uuid.uuid4()}", request_hash="-", scope=scope)
    db.add(record)
    db.commit()
    limited = check(db, user_id, scope, settings)
    if limited is not None:
        db.delete(record)
        db.commit()
    return limited
