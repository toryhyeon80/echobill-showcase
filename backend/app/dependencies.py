"""JWT helpers and FastAPI auth dependencies — HttpOnly cookie (DL-12) or dev Bearer."""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt

from app.config import Settings, get_settings

# Fixed in code, not env: a configurable algorithm invites alg-confusion mistakes
JWT_ALGORITHM = "HS256"

security = HTTPBearer(auto_error=False)


# token kinds: a signup token must never pass as a login. A token without `typ` is rejected (pre-10/12 tokens all expired).
ACCESS = "access"
SIGNUP = "signup"


def create_access_token(user_id: str, settings: Settings | None = None) -> str:
    """Issue HS256 access JWT with user_id claim."""
    settings = settings or get_settings()
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_access_ttl_hours)
    payload = {"sub": user_id, "exp": expire, "typ": ACCESS}
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=JWT_ALGORITHM)


def create_signup_token(member_id: str, settings: Settings | None = None) -> str:
    """Short-lived JWT for a Kakao member who has not agreed to the terms yet — signup API only."""
    settings = settings or get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.signup_token_ttl_minutes)
    payload = {"sub": member_id, "exp": expire, "typ": SIGNUP}
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=JWT_ALGORITHM)


def read_signup_token(token: str | None, settings: Settings | None = None) -> str | None:
    """Kakao member id from a valid, unexpired signup token; None otherwise (→ 401)."""
    if not token:
        return None
    settings = settings or get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    return str(sub) if sub and payload.get("typ") == SIGNUP else None


def decode_token(token: str, settings: Settings | None = None) -> dict[str, Any]:
    """Decode and validate JWT; raise HTTP 401 on failure."""
    settings = settings or get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


def get_current_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """
    Require a JWT and return user_id (sub).

    The Kakao login cookie (DL-12) is tried first, so an old dev-token header can never
    shadow a fresh login. The Bearer header is the dev-token FE path and is accepted only
    in development — production authenticates by HttpOnly cookie alone. 401 if none is valid.
    """
    candidates = [
        request.cookies.get(settings.auth_cookie_name),
        credentials.credentials if credentials and settings.dev_token_enabled else None,
    ]
    tokens = [t for t in candidates if t]
    if not tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    for token in tokens:
        try:
            payload = decode_token(token, settings)
        except HTTPException:
            continue
        user_id = payload.get("sub")
        if user_id and payload.get("typ") == ACCESS:  # a signup token is not a login
            return str(user_id)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
    )


CurrentUserId = Annotated[str, Depends(get_current_user_id)]
