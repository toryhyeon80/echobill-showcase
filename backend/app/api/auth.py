"""Auth — Kakao Authorization Code → JWT cookie (D07 / DL-21), signup with terms consent (PM 10/8),
plus dev-only token mint.

Flow: [카카오로 시작하기] → /kakao. A signed-up member gets the login cookie at once. A first-timer
gets `signup_required` + a 10-minute signup cookie (no merchant row yet) → terms screen →
/signup creates the merchant with its consent record and sets the login cookie.
"""

import logging
from collections.abc import Generator
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.dependencies import CurrentUserId, create_access_token, create_signup_token, read_signup_token
from app.models import Merchant
from app.schemas.auth import (
    DevTokenRequest,
    KakaoLoginRequest,
    LoginResponse,
    SignupRequest,
    SignupRequiredResponse,
    TokenResponse,
)
from app.schemas.errors import ErrorResponse
from app.services.idempotency import run_idempotent
from app.services.kakao import KakaoAuthError, KakaoUnavailableError, exchange_code_for_member_id

logger = logging.getLogger(__name__)

router = APIRouter()


def _login_body(user_id: str, merchant: Merchant | None, settings: Settings) -> LoginResponse:
    return LoginResponse(user_id=user_id, expires_in_hours=settings.jwt_access_ttl_hours,
                         voice_answer_fields=sorted(settings.voice_answer_enabled),
                         ai_consent=merchant.ai_consent if merchant is not None else None)


def _set_login_cookie(response: Response, user_id: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=create_access_token(user_id, settings),
        max_age=settings.jwt_access_ttl_hours * 3600,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
        # no Domain (docs/deploy.md §2) — api.echobill.kr only
    )


def _clear_cookie(response: Response, name: str, path: str, settings: Settings) -> None:
    """Attributes must match set_cookie or browsers keep the old cookie."""
    response.delete_cookie(key=name, path=path, httponly=True, secure=settings.auth_cookie_secure, samesite="lax")


SIGNUP_COOKIE_PATH = "/api/v1/auth/signup"  # the signup cookie is sent to the signup API only


@router.get("/me", response_model=LoginResponse, responses={401: {"model": ErrorResponse}})
def current_session(
    user_id: CurrentUserId,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> LoginResponse:
    """Restore the login on app start from the HttpOnly cookie — the JWT is never exposed to JS (DL-12)."""
    return _login_body(user_id, db.get(Merchant, user_id), settings)


def get_kakao_http_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[httpx.Client, None, None]:
    """httpx client with the Kakao timeout. Tests override this with a MockTransport."""
    with httpx.Client(timeout=settings.kakao_timeout_seconds) as client:
        yield client


def _error(status_code: int, error_code: str, message: str) -> JSONResponse:
    """Shared DL-03 error body."""
    body = ErrorResponse(error_code=error_code, message=message)
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _signed_up(merchant: Merchant | None) -> bool:
    """A member is signed up once the terms are agreed. Rows made by the old first-login (before
    PM 10/8) have no consent record, so they go through the terms screen once too."""
    return merchant is not None and merchant.terms_agreed_at is not None


def _create_signed_up_merchant(db: Session, member_id: str, body: SignupRequest) -> Merchant:
    """Kakao member number is the merchant PK (기획서 §4.5) — created only with its consent record."""
    now = datetime.now(timezone.utc)
    merchant = db.get(Merchant, member_id)
    if _signed_up(merchant):
        return merchant  # already signed up (another tab / replayed request) — keep the first consent
    if merchant is None:
        merchant = Merchant(user_id=member_id, kakao_provider_id=member_id)
        db.add(merchant)
    merchant.terms_version = body.terms_version
    merchant.privacy_version = body.privacy_version
    merchant.terms_agreed_at = now
    merchant.ai_consent = body.ai_consent
    merchant.ai_consent_at = now
    try:
        db.commit()
    except IntegrityError:
        # concurrent signup with another idempotency key inserted it first
        db.rollback()
        existing = db.get(Merchant, member_id)
        if existing is None:
            raise
        return existing
    except Exception:
        db.rollback()
        raise
    return merchant


@router.post(
    "/kakao",
    response_model=LoginResponse | SignupRequiredResponse,
    responses={
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def kakao_login(
    body: KakaoLoginRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
    client: Annotated[httpx.Client, Depends(get_kakao_http_client)],
) -> LoginResponse | SignupRequiredResponse | JSONResponse:
    """
    Exchange a Kakao authorization code server-side. Signed-up member → login JWT as an HttpOnly
    cookie. First-timer → `signup_required: true` + a 10-minute signup cookie, no merchant row.

    401 = Kakao rejected the code (retry login) · 503 = Kakao unreachable · 500 = keys missing.
    """
    if not settings.kakao_rest_api_key or not settings.kakao_client_secret.get_secret_value():
        logger.error("Kakao login called but KAKAO_REST_API_KEY / KAKAO_CLIENT_SECRET not set")
        return _error(500, "KAKAO_NOT_CONFIGURED", "로그인을 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.")

    try:
        member_id = exchange_code_for_member_id(client, body.code, settings)
    except KakaoAuthError:
        return _error(401, "KAKAO_AUTH_FAILED", "카카오 로그인에 실패했습니다. 다시 로그인해 주세요.")
    except KakaoUnavailableError:
        return _error(503, "EXTERNAL_API_TIMEOUT", "카카오 응답이 늦습니다. 잠시 후 다시 시도해 주세요.")

    merchant = db.get(Merchant, member_id)
    if not _signed_up(merchant):
        response.set_cookie(
            key=settings.signup_cookie_name,
            value=create_signup_token(member_id, settings),
            max_age=settings.signup_token_ttl_minutes * 60,
            httponly=True,
            secure=settings.auth_cookie_secure,
            samesite="lax",
            path=SIGNUP_COOKIE_PATH,
        )
        # someone else's login on this device must not stay active behind the terms screen
        _clear_cookie(response, settings.auth_cookie_name, "/", settings)
        return SignupRequiredResponse(expires_in_minutes=settings.signup_token_ttl_minutes)

    _set_login_cookie(response, member_id, settings)
    _clear_cookie(response, settings.signup_cookie_name, SIGNUP_COOKIE_PATH, settings)
    return _login_body(member_id, merchant, settings)


@router.post(
    "/signup",
    response_model=LoginResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def signup(
    body: SignupRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
    x_idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """
    Terms agreed → create the merchant with its consent record and set the login cookie (PM 10/8).

    401 SIGNUP_EXPIRED = no or expired signup cookie (start over with Kakao) ·
    400 CONSENT_REQUIRED = 약관·개인정보 처리방침·만 14세 이상 missing (problem_fields) ·
    400 IDEMPOTENCY_KEY_REQUIRED · 409 same key, different body.
    """
    member_id = read_signup_token(request.cookies.get(settings.signup_cookie_name), settings)
    if member_id is None:
        return _error(401, "SIGNUP_EXPIRED", "가입 시간이 지났습니다. 카카오로 다시 시작해 주세요.")
    agreed = {"terms_version": bool((body.terms_version or "").strip()),
              "privacy_version": bool((body.privacy_version or "").strip()),
              "age_over_14": body.age_over_14 is True}
    missing = [name for name, ok in agreed.items() if not ok]
    if missing:
        error = ErrorResponse(error_code="CONSENT_REQUIRED", message="필수 항목에 동의해 주세요.",
                              problem_fields=missing)
        return JSONResponse(status_code=400, content=error.model_dump(mode="json"))

    def create() -> tuple[int, dict]:
        merchant = _create_signed_up_merchant(db, member_id, body)
        return 200, _login_body(member_id, merchant, settings).model_dump(mode="json")

    result = run_idempotent(db, member_id, x_idempotency_key, "signup", body.model_dump(mode="json"), create)
    if result.status_code == 200:  # a replay sets the cookie again too
        _set_login_cookie(result, member_id, settings)
        _clear_cookie(result, settings.signup_cookie_name, SIGNUP_COOKIE_PATH, settings)
    return result


@router.post("/logout", status_code=204)
def logout(settings: Annotated[Settings, Depends(get_settings)]) -> Response:
    """
    Clear the HttpOnly login cookie — JS cannot delete it (DL-12).

    No auth required: logging out twice or with an expired cookie is still a 204.
    Also drops a pending signup cookie (left the terms screen).
    """
    response = Response(status_code=204)
    _clear_cookie(response, settings.auth_cookie_name, "/", settings)
    _clear_cookie(response, settings.signup_cookie_name, SIGNUP_COOKIE_PATH, settings)
    return response


@router.post("/dev-token", response_model=TokenResponse)
def issue_dev_token(
    body: DevTokenRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    """
    Dev-only JWT mint (DL-21 4-5). Disabled outside APP_ENV=development — it is an auth bypass.
    Do not treat client-supplied user_id as authenticated identity outside stub.
    """
    if not settings.dev_token_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    token = create_access_token(body.user_id, settings)
    return TokenResponse(
        access_token=token,
        expires_in_hours=settings.jwt_access_ttl_hours,
        user_id=body.user_id,
    )
