"""FeedbackEvent — confirm / correct / reject against a candidate."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONType


class FeedbackEvent(Base):
    """User decision on a parse candidate."""

    __tablename__ = "feedback_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ledger_entry_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # confirm | correct | reject
    corrected_data: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # DL-32: the user's AI 개선 동의 when this record was made — only true rows may be exported (pseudonymized)
    ai_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
