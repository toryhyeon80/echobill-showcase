"""Kakao login — Authorization Code → Kakao member number (D07 / DL-21 4-1·4-2).

The server talks to Kakao directly; a client-supplied ID is never trusted.
"""

import logging

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class KakaoAuthError(Exception):
    """Kakao rejected the code/token (bad, expired or reused code, redirect mismatch)."""


class KakaoUnavailableError(Exception):
    """Kakao unreachable or timed out — recoverable, not the user's fault."""


def _request(client: httpx.Client, method: str, url: str, **kwargs) -> dict:
    """Call Kakao and return JSON; map transport failures and 4xx to our errors."""
    try:
        res = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        logger.warning("Kakao request failed: %s %s (%s)", method, url, type(exc).__name__)
        raise KakaoUnavailableError from exc

    if res.status_code >= 500:
        logger.warning("Kakao %s %s returned %s", method, url, res.status_code)
        raise KakaoUnavailableError
    if res.status_code != 200:
        # error_code only (e.g. KOE320) — body may echo request params
        try:
            kakao_code = res.json().get("error_code")
        except ValueError:
            kakao_code = None
        logger.info("Kakao %s %s rejected: %s %s", method, url, res.status_code, kakao_code)
        raise KakaoAuthError
    try:
        return res.json()
    except ValueError as exc:
        raise KakaoUnavailableError from exc


def exchange_code_for_member_id(client: httpx.Client, code: str, settings: Settings) -> str:
    """
    Exchange an authorization code for the Kakao member number (회원번호).

    client_secret is always sent: the console has it enabled and Kakao fails the
    token request without it.
    """
    token = _request(
        client,
        "POST",
        f"{settings.kakao_auth_base_url}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": settings.kakao_rest_api_key,
            "client_secret": settings.kakao_client_secret.get_secret_value(),
            "redirect_uri": settings.kakao_redirect_uri,
            "code": code,
        },
    )
    access_token = token.get("access_token")
    if not access_token:
        raise KakaoAuthError

    me = _request(
        client,
        "GET",
        f"{settings.kakao_api_base_url}/v2/user/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    member_id = me.get("id")
    if member_id is None:
        raise KakaoAuthError
    return str(member_id)
