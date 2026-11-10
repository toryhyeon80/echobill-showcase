"""Cloudflare bypass guard (PM #11) — off until ORIGIN_SECRET is set; /health always open."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, create_app

SECRET = "s" * 64


def _client(secret: str = SECRET) -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, app_env="development", origin_secret=secret)))


def test_off_by_default_nothing_is_blocked() -> None:
    """Deploying the code alone must not block traffic (Cloudflare header may not be on yet)."""
    assert TestClient(app).get("/api/v1/dashboard/unpaid").status_code == 401  # reaches auth, not 403


@pytest.mark.parametrize("headers", [{}, {"X-Origin-Secret": "wrong"}, {"X-Origin-Secret": SECRET[:-1]}])
def test_missing_or_wrong_secret_is_403(headers: dict) -> None:
    res = _client().get("/api/v1/dashboard/unpaid", headers=headers)
    assert res.status_code == 403
    assert res.json()["error_code"] == "ORIGIN_FORBIDDEN"


def test_correct_secret_passes_through() -> None:
    res = _client().get("/api/v1/dashboard/unpaid", headers={"X-Origin-Secret": SECRET})
    assert res.status_code == 401  # passed the guard, then normal auth


def test_health_is_exempt_for_railway_healthcheck() -> None:
    assert _client().get("/health").status_code == 200


def test_rejected_before_body_is_read() -> None:
    """Outermost: even an oversized body gets 403, not 413 — nothing else runs."""
    res = _client().post("/api/v1/ledger/parse", content=b"x" * 100_000)
    assert res.status_code == 403


def test_cors_preflight_through_cloudflare_still_works() -> None:
    from app.config import get_settings

    origin = get_settings().cors_origin_list[0]
    res = _client().options(
        "/api/v1/ledger/parse",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST", "X-Origin-Secret": SECRET},
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == origin
