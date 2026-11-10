"""Per-owner STT hints — the owner's own recent counterparty names (PM 10/19, 음성 인식 비교 ② 거래처 힌트)."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import LedgerEntry

# whisper-1 keeps only the last 224 prompt tokens and the vocabulary file already uses ~145
# (configs/README.md) — a short name line keeps most of that file instead of pushing it out
MAX_HINT_CHARS = 120


def recent_counterparty_names(db: Session, user_id: str, limit: int) -> list[str]:
    """
    Return up to `limit` counterparty names from this user's confirmed, not-deleted entries,
    most recently used first.

    Only the owner's own confirmed names — never candidates (could be misheard) or another
    user's data. Whitespace is collapsed (no newlines reach the prompt) and the joined list
    stops before MAX_HINT_CHARS.
    """
    if limit <= 0:
        return []
    last_used = func.max(LedgerEntry.created_at)
    rows = db.execute(
        select(LedgerEntry.counterparty)
        .where(
            LedgerEntry.user_id == user_id,
            LedgerEntry.status == "confirmed",
            LedgerEntry.deleted_at.is_(None),
            LedgerEntry.counterparty.is_not(None),
        )
        .group_by(LedgerEntry.counterparty)
        .order_by(last_used.desc(), func.max(LedgerEntry.id).desc())
        .limit(limit)
    ).scalars()

    names: list[str] = []
    used = 0
    for raw in rows:
        name = " ".join(raw.split())
        if not name or name in names:
            continue
        cost = len(name) + (2 if names else 0)  # ", " separator
        if used + cost > MAX_HINT_CHARS:
            continue  # a long name skips itself, not every shorter name after it (병철 #167 리뷰)
        names.append(name)
        used += cost
    return names
