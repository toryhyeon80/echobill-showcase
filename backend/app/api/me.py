"""My page — AI 개선 동의 토글 (DL-32). The signup consent itself is recorded by /auth/signup.

Turning it off only affects records made afterwards: each feedback row keeps the consent of its time.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import CurrentUserId
from app.models import Merchant
from app.schemas.auth import ConsentResponse, ConsentUpdate
from app.schemas.errors import ErrorResponse
from app.services.idempotency import run_idempotent

router = APIRouter()


@router.patch(
    "/consent",
    response_model=ConsentResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
               409: {"model": ErrorResponse}},
)
def update_consent(
    body: ConsentUpdate,
    user_id: CurrentUserId,
    db: Annotated[Session, Depends(get_db)],
    x_idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Turn AI 개선 동의 on/off. Same value again is a no-op (ai_consent_at keeps the last change).

    400 IDEMPOTENCY_KEY_REQUIRED · 404 MERCHANT_NOT_FOUND (dev-token user without signup) · 409 key reused.
    """

    def apply() -> tuple[int, dict]:
        merchant = db.get(Merchant, user_id)
        if merchant is None:
            error = ErrorResponse(error_code="MERCHANT_NOT_FOUND",
                                  message="회원 정보를 찾을 수 없습니다. 다시 로그인해 주세요.")
            return 404, error.model_dump(mode="json")
        if merchant.ai_consent != body.ai_consent:
            merchant.ai_consent = body.ai_consent
            merchant.ai_consent_at = datetime.now(timezone.utc)
            try:
                db.commit()
            except Exception:
                db.rollback()
                raise
        result = ConsentResponse(ai_consent=merchant.ai_consent, ai_consent_at=merchant.ai_consent_at)
        return 200, result.model_dump(mode="json")

    return run_idempotent(db, user_id, x_idempotency_key, "consent", body.model_dump(mode="json"), apply)
