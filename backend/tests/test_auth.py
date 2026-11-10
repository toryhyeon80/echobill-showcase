"""Auth tests — Kakao code exchange (mocked Kakao), cookie JWT, dev-token gating."""

import uuid
from collections.abc import Callable, Generator
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import get_kakao_http_client
from app.config import Settings, get_settings
from app.main import app
from app.models import Merchant

client = TestClient(app)

PROD_KEY = "QWw5Y3JJb3l3aHk0ZHRrN0tVaW1sTktxOWZpVnB0Z2g="  # any non-default Fernet key
PROD_SECRET = "p" * 32  # minimum accepted length outside development

KAKAO_SETTINGS = Settings(
    kakao_rest_api_key="test-rest-key",
    kakao_client_secret="test-client-secret",
    kakao_redirect_uri="http://localhost:5173/auth/kakao/callback",
    auth_cookie_secure=False,  # TestClient runs over http
)


def _use_kakao(handler: Callable[[httpx.Request], httpx.Response]) -> None:
    """Route Kakao HTTP calls to `handler` and enable Kakao settings."""

    def _client() -> Generator[httpx.Client, None, None]:
        with httpx.Client(transport=httpx.MockTransport(handler)) as c:
            yield c

    app.dependency_overrides[get_kakao_http_client] = _client
    app.dependency_overrides[get_settings] = lambda: KAKAO_SETTINGS


@pytest.fixture(autouse=True)
def _reset_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.pop(get_kakao_http_client, None)
    app.dependency_overrides.pop(get_settings, None)
    client.cookies.clear()


def _kakao_ok(member_id: int = 4242424242, seen: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "kakao-at", "token_type": "bearer"})
        if request.url.path == "/v2/user/me":
            assert request.headers["Authorization"] == "Bearer kakao-at"
            return httpx.Response(200, json={"id": member_id})
        return httpx.Response(404)
    return handler


CONSENT = {"terms_version": "2026-10-08", "privacy_version": "2026-10-08", "age_over_14": True, "ai_consent": False}


def _login(code: str = "c") -> httpx.Response:
    """Kakao → (first time) terms screen → signup: ends with the login cookie set (PM 10/8 flow)."""
    res = client.post("/api/v1/auth/kakao", json={"code": code})
    if res.json().get("signup_required"):
        res = client.post("/api/v1/auth/signup", json=CONSENT, headers={"X-Idempotency-Key": str(uuid.uuid4())})
    return res


def test_dev_token_issues_jwt() -> None:
    """POST /api/v1/auth/dev-token returns bearer token."""
    res = client.post("/api/v1/auth/dev-token", json={"user_id": "dev-user-1"})
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["user_id"] == "dev-user-1"
    assert body["access_token"]


def test_dev_token_disabled_outside_development() -> None:
    """DL-21 4-5: dev-token is an auth bypass — gone in production."""
    app.dependency_overrides[get_settings] = lambda: Settings(app_env="production", jwt_secret=PROD_SECRET, business_number_key=PROD_KEY)
    res = client.post("/api/v1/auth/dev-token", json={"user_id": "dev-user-1"})
    assert res.status_code == 404


def test_kakao_login_sends_client_secret_and_sets_cookie(db: Session) -> None:
    """Token request carries client_secret; JWT goes to an HttpOnly cookie, not the body."""
    seen: list[httpx.Request] = []
    _use_kakao(_kakao_ok(seen=seen))

    res = client.post("/api/v1/auth/kakao", json={"code": "auth-code-1"})

    assert res.status_code == 200
    assert res.json() == {"signup_required": True, "expires_in_minutes": 10}  # first time → terms screen
    assert db.get(Merchant, "4242424242") is None  # no member before consent (PM 10/8)
    res = _login("auth-code-2")  # signed up now: the next Kakao login sets the login cookie
    res = client.post("/api/v1/auth/kakao", json={"code": "auth-code-3"})
    body = res.json()
    assert body == {"signup_required": False, "user_id": "4242424242", "expires_in_hours": 24,
                    "voice_answer_fields": [], "ai_consent": False}
    assert "access_token" not in body

    form = parse_qs(seen[0].content.decode())
    assert form["client_secret"] == ["test-client-secret"]
    assert form["client_id"] == ["test-rest-key"]
    assert form["code"] == ["auth-code-1"]
    assert form["grant_type"] == ["authorization_code"]
    assert form["redirect_uri"] == ["http://localhost:5173/auth/kakao/callback"]

    set_cookie = res.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "domain=" not in set_cookie

    db.expire_all()
    merchant = db.get(Merchant, "4242424242")
    assert merchant is not None
    assert merchant.kakao_provider_id == "4242424242"


def test_cookie_authenticates_ledger_api() -> None:
    """After Kakao login the cookie alone (no Authorization header) passes auth."""
    _use_kakao(_kakao_ok())
    _login("auth-code-1")

    res = client.post(
        "/api/v1/ledger/parse",
        json={"utterance_text": "김씨에게 배추 3박스 외상 30000원"},
        headers={"X-Idempotency-Key": str(uuid.uuid4())},
    )
    assert res.status_code == 200


def test_kakao_login_twice_reuses_merchant(db: Session) -> None:
    """Re-login does not duplicate the merchant row."""
    _use_kakao(_kakao_ok())
    assert _login("c1").status_code == 200
    assert _login("c2").json()["signup_required"] is False  # signed up → straight login
    assert db.query(Merchant).count() == 1


def test_kakao_rejected_code_is_401() -> None:
    """Invalid / reused code (KOE320) → 401, no cookie."""
    _use_kakao(lambda _: httpx.Response(400, json={"error": "invalid_grant", "error_code": "KOE320"}))
    res = client.post("/api/v1/auth/kakao", json={"code": "bad"})
    assert res.status_code == 401
    assert res.json()["error_code"] == "KAKAO_AUTH_FAILED"
    assert "set-cookie" not in res.headers


def test_kakao_timeout_is_504() -> None:
    """Kakao unreachable → 503, not an input error."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    _use_kakao(handler)
    res = client.post("/api/v1/auth/kakao", json={"code": "c"})
    assert res.status_code == 503
    assert res.json()["error_code"] == "EXTERNAL_API_TIMEOUT"


def test_kakao_not_configured_is_500() -> None:
    """Missing key/secret is a server problem, never sent to Kakao."""
    _use_kakao(_kakao_ok())
    app.dependency_overrides[get_settings] = lambda: Settings(kakao_client_secret="")
    res = client.post("/api/v1/auth/kakao", json={"code": "c"})
    assert res.status_code == 500
    assert res.json()["error_code"] == "KAKAO_NOT_CONFIGURED"


def test_dev_token_off_when_app_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deploy that forgets APP_ENV must not expose the dev-token bypass."""
    monkeypatch.delenv("APP_ENV", raising=False)
    assert Settings(_env_file=None, jwt_secret=PROD_SECRET, business_number_key=PROD_KEY).dev_token_enabled is False


def test_production_rejects_default_jwt_secret() -> None:
    """The committed default secret is public — refuse to start with it outside development."""
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(_env_file=None, app_env="production", business_number_key=PROD_KEY)


def test_concurrent_first_login_returns_existing_merchant(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """If another signup inserted the merchant between our check and insert, reuse it — no 500."""
    from app.api.auth import _create_signed_up_merchant
    from app.schemas.auth import SignupRequest

    db.add(Merchant(user_id="555", kakao_provider_id="555"))
    db.commit()
    db.expunge_all()

    real_get = Session.get
    calls = {"n": 0}

    def get_missing_once(self, *args, **kwargs):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_get(self, *args, **kwargs)

    monkeypatch.setattr(Session, "get", get_missing_once)
    merchant = _create_signed_up_merchant(db, "555", SignupRequest(**CONSENT))
    assert merchant.user_id == "555"
    assert db.query(Merchant).count() == 1


def test_stale_bearer_falls_back_to_cookie() -> None:
    """An expired dev Bearer header must not block a valid Kakao login cookie."""
    _use_kakao(_kakao_ok())
    _login("c")
    res = client.post(
        "/api/v1/ledger/parse",
        json={"utterance_text": "김씨에게 배추 3박스 외상 30000원"},
        headers={"Authorization": "Bearer not-a-valid-jwt", "X-Idempotency-Key": str(uuid.uuid4())},
    )
    assert res.status_code == 200


def test_logout_clears_cookie() -> None:
    """After logout the cookie is gone and ledger APIs are 401 again."""
    _use_kakao(_kakao_ok())
    _login("c")

    res = client.post("/api/v1/auth/logout")
    assert res.status_code == 204
    set_cookie = res.headers["set-cookie"].lower()
    assert "max-age=0" in set_cookie
    assert "httponly" in set_cookie

    after = client.post(
        "/api/v1/ledger/parse",
        json={"utterance_text": "김씨에게 배추 3박스 외상 30000원"},
        headers={"X-Idempotency-Key": str(uuid.uuid4())},
    )
    assert after.status_code == 401


def test_logout_without_login_is_204() -> None:
    assert client.post("/api/v1/auth/logout").status_code == 204


def test_production_rejects_short_jwt_secret() -> None:
    """A guessable short secret lets anyone forge tokens — refuse to start (PM 2순위 #4)."""
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(_env_file=None, app_env="production", jwt_secret="x" * 31, business_number_key=PROD_KEY)
    assert Settings(_env_file=None, app_env="production", jwt_secret="x" * 32, business_number_key=PROD_KEY).app_env == "production"


def test_token_algorithm_is_fixed_hs256() -> None:
    """The algorithm comes from code, not env (PM 2순위 #5); other algs are rejected."""
    import jwt as pyjwt

    from app.dependencies import create_access_token, decode_token

    token = create_access_token("u1", KAKAO_SETTINGS)
    assert pyjwt.get_unverified_header(token)["alg"] == "HS256"
    forged = pyjwt.encode({"sub": "u1", "typ": "access"}, KAKAO_SETTINGS.jwt_secret.get_secret_value(), algorithm="HS512")
    with pytest.raises(Exception) as info:
        decode_token(forged, KAKAO_SETTINGS)
    assert getattr(info.value, "status_code", None) == 401


def test_expired_token_is_401() -> None:
    from datetime import datetime, timedelta, timezone

    import jwt as pyjwt

    expired = pyjwt.encode(
        {"sub": "u1", "typ": "access", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
        Settings().jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    assert client.get("/api/v1/dashboard/unpaid", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_api_docs_off_outside_development() -> None:
    """PM 2순위 #6: no /docs · /redoc · /openapi.json in production."""
    from app.main import create_app

    prod = TestClient(create_app(Settings(_env_file=None, app_env="production", jwt_secret=PROD_SECRET, business_number_key=PROD_KEY)))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert prod.get(path).status_code == 404
    assert prod.get("/health").status_code == 200

    dev = TestClient(create_app(Settings(_env_file=None, app_env="development")))
    assert dev.get("/openapi.json").status_code == 200


def test_me_requires_login() -> None:
    """No cookie, no dev header → 401 (FE shows the login screen)."""
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_restores_session_from_cookie() -> None:
    """App start: the HttpOnly cookie alone is enough to know who is logged in (DL-12)."""
    _use_kakao(_kakao_ok())
    _login("c")
    res = client.get("/api/v1/auth/me")
    assert res.status_code == 200
    assert res.json() == {"signup_required": False, "user_id": "4242424242", "expires_in_hours": 24,
                          "voice_answer_fields": [], "ai_consent": False}


def test_cookie_wins_over_valid_dev_header() -> None:
    """An old dev identity cannot shadow a freshly authenticated cookie identity."""
    from app.dependencies import create_access_token

    _use_kakao(_kakao_ok())
    _login("c")
    res = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {create_access_token('old-dev-user', KAKAO_SETTINGS)}"},
    )
    assert res.json()["user_id"] == "4242424242"


def test_bearer_header_is_ignored_in_production() -> None:
    """Production authenticates by cookie only; a valid Bearer token is not accepted there."""
    from app.dependencies import create_access_token

    prod = Settings(_env_file=None, app_env="production", jwt_secret=PROD_SECRET, business_number_key=PROD_KEY)
    app.dependency_overrides[get_settings] = lambda: prod
    token = create_access_token("someone", prod)
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
