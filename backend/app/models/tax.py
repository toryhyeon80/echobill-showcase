"""TaxInvoiceEvent — Popbill-shaped Mock only (issued = 모의 완료)."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TaxInvoiceEvent(Base):
    """Mock tax invoice lifecycle. Source of truth for nts_state."""

    __tablename__ = "tax_invoice_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ledger_entry_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    nts_state: Mapped[str] = mapped_column(String(32), nullable=False, default="requested")
    # requested | issued (mock complete) | failed
    fail_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
