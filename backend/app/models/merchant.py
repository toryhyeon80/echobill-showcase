"""Merchant (1인 사업자) model."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Merchant(Base):
    """Authenticated business owner; PK = JWT user_id."""

    __tablename__ = "merchants"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kakao_provider_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    business_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    business_number_enc: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 가입 동의 기록 (PM 10/8) — terms_agreed_at NULL = 아직 가입 전(약관 동의 화면으로)
    terms_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    privacy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    terms_agreed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    ai_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
