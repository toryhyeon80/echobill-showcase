"""IdempotencyRecord — first response per (user, X-Idempotency-Key), kept 24h (DL-22 5-3·5-6)."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONType


class IdempotencyRecord(Base):
    """status_code NULL = request still in flight (reserved, no response yet)."""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_idempotency_user_key"),
        # per-user rate limit counts (PM #14) and the 24h purge scan
        Index("ix_idempotency_user_scope_created", "user_id", "scope", "created_at"),
        Index("ix_idempotency_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # endpoint name without IDs (parse · parse-audio · feedback · payments · tax) — rate limit counts by it
    scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # PM #23 repair_of: {"version": "pp-1.3", "rules": [...]} when a parse 400 came from post-process rules
    postprocess_trace: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # repair_of 1회 (PM 10/15): entry_id of the candidate the first repair submit for this (failed) request
    # made — a later repair with the same repair_of returns it (candidate), 409s (confirmed) or makes a new one
    repair_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Python-side UTC so the 24h window compares the same way on SQLite and PostgreSQL
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
