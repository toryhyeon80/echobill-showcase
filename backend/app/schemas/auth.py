"""Auth-related Pydantic models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DevTokenRequest(BaseModel):
    """Local/dev stub: mint JWT without real Kakao verification (D07 later)."""

    user_id: str = Field(..., min_length=1, max_length=64, examples=["dev-user-1"])
    display_name: str | None = Field(default=None, max_length=100)


class TokenResponse(BaseModel):
    """JWT access token payload for clients."""

    access_token: str
    token_type: str = "bearer"
    expires_in_hours: int
    user_id: str


class KakaoLoginRequest(BaseModel):
    """Authorization code the FE callback page received from Kakao (DL-21 4-2)."""

    code: str = Field(..., min_length=1, max_length=512)


class LoginResponse(BaseModel):
    """Kakao login result. The JWT itself travels only in the HttpOnly cookie (DL-12)."""

    signup_required: Literal[False] = False
    user_id: str
    expires_in_hours: int
    # DL-31: fields whose voice answer is on (VOICE_ANSWER_FIELDS) — the FE hides 🎤 for the others
    voice_answer_fields: list[str] = []
    # 마이페이지 AI 학습 동의 토글 (PM 10/8). null = no merchant row (dev-token user)
    ai_consent: bool | None = None


class SignupRequiredResponse(BaseModel):
    """First Kakao login: no merchant yet — show the terms screen, then POST /auth/signup (PM 10/8)."""

    signup_required: Literal[True] = True
    expires_in_minutes: int  # the signup cookie's lifetime; after that start over with Kakao


class SignupRequest(BaseModel):
    """Terms screen answers. Required ones are checked in the API so a missing one is a 400, not a 422."""

    terms_version: str | None = Field(default=None, max_length=32, examples=["2026-10-08"])
    privacy_version: str | None = Field(default=None, max_length=32, examples=["2026-10-08"])
    age_over_14: bool | None = None
    ai_consent: bool = False


class ConsentUpdate(BaseModel):
    """마이페이지 AI 학습 동의 토글."""

    ai_consent: bool


class ConsentResponse(BaseModel):
    ai_consent: bool
    ai_consent_at: datetime | None
