"""LedgerEntry model — candidate until user confirms (never auto-confirmed)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONType


class LedgerEntry(Base):
    """Trade candidate/confirmed row. Soft-deleted via deleted_at."""

    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # DL-23: 정수 PK는 내부 FIFO 정렬용, 외부 공개 ID는 entry_id(UUID 문자열)만
    entry_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate")
    # candidate | confirmed | rejected

    counterparty: Mapped[str | None] = mapped_column(String(200), nullable=True)
    items: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    total_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # DL-01

    utterance_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # 보관 범위 D08 (DL-14)
    parser_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # llm | rule | manual
    ai_model_used: Mapped[str | None] = mapped_column(String(64), nullable=True)  # DB 스키마
    is_fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    processing_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    # PM #23 — LLM post-process trace (pp-x.y + rule names). NULL when post-process did not run
    postprocess_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    postprocess_applied: Mapped[list | None] = mapped_column(JSONType, nullable=True)

    tax_status: Mapped[str | None] = mapped_column(String(32), nullable=True)  # cache of TaxInvoiceEvent
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
