"""Signup flow (PM 10/8) — Kakao → terms screen → /auth/signup → login, plus the AI consent toggle."""

import uuid
from collections.abc import Generator
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import get_kakao_http_client
from app.config import get_settings
from app.dependencies import create_access_token, create_signup_token
from app.main import app
from app.models import Merchant
from tests.test_auth import CONSENT, KAKAO_SETTINGS, _kakao_ok, _use_kakao

client = TestClient(app)
MEMBER = "4242424242"


@pytest.fixture(autouse=True)
def _kakao() -> Generator[None, None, None]:
    _use_kakao(_kakao_ok())
    yield
    app.dependency_overrides.pop(get_kakao_http_client, None)
    app.dependency_overrides.pop(get_settings, None)
    client.cookies.clear()


def _signup(body: dict | None = None, key: str | None = "auto"):
    headers = {"X-Idempotency-Key": str(uuid.uuid4()) if key == "auto" else key} if key else {}
    return client.post("/api/v1/auth/signup", json=CONSENT if body is None else body, headers=headers)


def _start() -> None:
    assert client.post("/api/v1/auth/kakao", json={"code": "c"}).json()["signup_required"] is True


def _set_cookies(res) -> str:
    return "\n".join(res.headers.get_list("set-cookie")).lower()


# --- /auth/kakao: first time = signup pending, no member row ------------------------------

def test_first_kakao_login_sets_only_a_short_signup_cookie(db: Session) -> None:
    res = client.post("/api/v1/auth/kakao", json={"code": "c"})
    assert res.json() == {"signup_required": True, "expires_in_minutes": 10}
    cookies = _set_cookies(res)
    assert "echobill_signup_token=" in cookies
    assert "path=/api/v1/auth/signup" in cookies and "max-age=600" in cookies and "httponly" in cookies
    assert 'echobill_access_token="";' in cookies or "echobill_access_token=;" in cookies  # cleared
    assert db.get(Merchant, MEMBER) is None


def test_signup_token_is_not_a_login() -> None:
    """The signup token opens /auth/signup only — never the ledger or /auth/me."""
    token = create_signup_token(MEMBER, KAKAO_SETTINGS)
    client.cookies.set("echobill_access_token", token)
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.clear()
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_token_without_typ_is_401() -> None:
    """Pre-`typ` login tokens (before PR #92, 10/12) have all expired, so a token without `typ` is invalid."""
    no_typ = pyjwt.encode({"sub": "u-old", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                          KAKAO_SETTINGS.jwt_secret.get_secret_value(), algorithm="HS256")
    client.cookies.set("echobill_access_token", no_typ)
    res = client.get("/api/v1/auth/me")
    assert (res.status_code, res.json()["detail"]) == (401, "Invalid or expired token")
    client.cookies.clear()
    res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {no_typ}"})
    assert (res.status_code, res.json()["detail"]) == (401, "Invalid or expired token")


def test_access_token_cannot_be_used_to_sign_up() -> None:
    client.cookies.set("echobill_signup_token", create_access_token(MEMBER, KAKAO_SETTINGS),
                       path="/api/v1/auth/signup")
    assert _signup().status_code == 401


# --- /auth/signup ----------------------------------------------------------------------------

def test_signup_creates_member_with_consent_and_logs_in(db: Session) -> None:
    _start()
    res = _signup({**CONSENT, "ai_consent": True})
    assert res.status_code == 200
    assert res.json() == {"signup_required": False, "user_id": MEMBER, "expires_in_hours": 24,
                          "voice_answer_fields": [], "ai_consent": True}
    cookies = _set_cookies(res)
    assert "echobill_access_token=" in cookies and "httponly" in cookies
    merchant = db.get(Merchant, MEMBER)
    assert (merchant.terms_version, merchant.privacy_version, merchant.ai_consent) == ("2026-10-08", "2026-10-08", True)
    assert merchant.terms_agreed_at is not None and merchant.ai_consent_at is not None
    assert client.get("/api/v1/auth/me").json()["ai_consent"] is True
    # next time: straight login, no terms screen
    assert client.post("/api/v1/auth/kakao", json={"code": "c2"}).json()["signup_required"] is False


def test_signup_without_or_with_expired_signup_cookie_is_401() -> None:
    res = _signup()
    assert (res.status_code, res.json()["error_code"]) == (401, "SIGNUP_EXPIRED")
    expired = pyjwt.encode({"sub": MEMBER, "typ": "signup", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
                           KAKAO_SETTINGS.jwt_secret.get_secret_value(), algorithm="HS256")
    client.cookies.set("echobill_signup_token", expired, path="/api/v1/auth/signup")
    assert _signup().status_code == 401


@pytest.mark.parametrize("body, missing", [
    ({**CONSENT, "terms_version": None}, ["terms_version"]),
    ({**CONSENT, "privacy_version": "  "}, ["privacy_version"]),
    ({**CONSENT, "age_over_14": False}, ["age_over_14"]),
    ({"ai_consent": True}, ["terms_version", "privacy_version", "age_over_14"]),
])
def test_signup_without_required_consent_is_400(body: dict, missing: list[str], db: Session) -> None:
    _start()
    res = _signup(body)
    assert res.status_code == 400
    assert (res.json()["error_code"], res.json()["problem_fields"]) == ("CONSENT_REQUIRED", missing)
    assert db.get(Merchant, MEMBER) is None


def test_signup_ai_consent_defaults_to_off(db: Session) -> None:
    _start()
    body = {k: v for k, v in CONSENT.items() if k != "ai_consent"}
    assert _signup(body).json()["ai_consent"] is False


def test_signup_needs_idempotency_key() -> None:
    _start()
    assert _signup(key=None).json()["error_code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_signup_replay_sets_cookie_again_and_reused_key_is_409(db: Session) -> None:
    _start()
    key = str(uuid.uuid4())
    first = _signup(key=key)
    # the response was lost: the app retries with the same key (signup cookie still within 10 min)
    client.cookies.delete("echobill_access_token")
    client.cookies.set("echobill_signup_token", create_signup_token(MEMBER, KAKAO_SETTINGS),
                       path="/api/v1/auth/signup")
    again = _signup(key=key)
    assert again.status_code == 200 and again.json() == first.json()
    assert "echobill_access_token=" in _set_cookies(again)
    assert db.query(Merchant).count() == 1
    reused = _signup({**CONSENT, "ai_consent": True}, key=key)
    assert (reused.status_code, reused.json()["error_code"]) == (409, "IDEMPOTENCY_KEY_REUSED")


def test_second_signup_keeps_the_first_consent(db: Session) -> None:
    """Two tabs: the later signup logs in but does not overwrite the recorded consent."""
    _start()
    _signup({**CONSENT, "ai_consent": True})
    client.cookies.set("echobill_signup_token", create_signup_token(MEMBER, KAKAO_SETTINGS),
                       path="/api/v1/auth/signup")
    res = _signup({**CONSENT, "terms_version": "old", "ai_consent": False})
    assert res.status_code == 200 and res.json()["ai_consent"] is True
    assert db.get(Merchant, MEMBER).terms_version == "2026-10-08"


def test_member_from_old_first_login_agrees_once(db: Session) -> None:
    """Rows created by the old Kakao login (no consent record) see the terms screen once; data stays."""
    db.add(Merchant(user_id=MEMBER, kakao_provider_id=MEMBER, business_name="기존 가게"))
    db.commit()
    _start()
    assert _signup().status_code == 200
    db.expire_all()
    merchant = db.get(Merchant, MEMBER)
    assert merchant.business_name == "기존 가게" and merchant.terms_agreed_at is not None
    assert db.query(Merchant).count() == 1


def test_logout_drops_a_pending_signup_cookie() -> None:
    _start()
    res = client.post("/api/v1/auth/logout")
    assert "echobill_signup_token=" in _set_cookies(res)
    assert _signup().status_code == 401


# --- PATCH /me/consent -------------------------------------------------------------------------

def _consent(value: bool | None, token: str | None = None, key: str | None = "auto"):
    headers = {"X-Idempotency-Key": str(uuid.uuid4()) if key == "auto" else key} if key else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.patch("/api/v1/me/consent", json={} if value is None else {"ai_consent": value}, headers=headers)


def test_consent_toggle(db: Session) -> None:
    _start()
    _signup()
    on = _consent(True)
    assert on.status_code == 200 and on.json()["ai_consent"] is True
    changed_at = on.json()["ai_consent_at"]
    assert _consent(True).json()["ai_consent_at"] == changed_at  # no change → time kept
    assert client.get("/api/v1/auth/me").json()["ai_consent"] is True
    assert _consent(False).json()["ai_consent"] is False
    db.expire_all()
    assert db.get(Merchant, MEMBER).ai_consent is False


def test_consent_toggle_needs_key_login_and_a_member() -> None:
    assert _consent(True).status_code == 401
    token = create_access_token("dev-only-user", KAKAO_SETTINGS)
    res = _consent(True, token)
    assert (res.status_code, res.json()["error_code"]) == (404, "MERCHANT_NOT_FOUND")
    assert _consent(None, token).status_code == 422
    assert _consent(True, token, key=None).json()["error_code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_feedback_keeps_the_consent_of_its_time(db: Session) -> None:
    """DL-32: turning consent off later does not change records already made (and vice versa)."""
    from app.models import FeedbackEvent

    _start()
    _signup({**CONSENT, "ai_consent": True})

    def confirm_one() -> None:
        parsed = client.post("/api/v1/ledger/parse", json={"utterance_text": "김씨에게 배추 3박스 외상 30000원"},
                             headers={"X-Idempotency-Key": str(uuid.uuid4())}).json()
        res = client.post(f"/api/v1/ledger/{parsed['entry_id']}/feedback", json={"action": "confirm"},
                          headers={"X-Idempotency-Key": str(uuid.uuid4())})
        assert res.status_code == 200, res.text

    confirm_one()
    _consent(False)
    confirm_one()
    assert [e.ai_consent for e in db.query(FeedbackEvent).order_by(FeedbackEvent.id)] == [True, False]


def test_openapi_documents_signup_flow() -> None:
    paths = app.openapi()["paths"]
    assert {"400", "401", "409"} <= paths["/api/v1/auth/signup"]["post"]["responses"].keys()
    assert {"400", "401", "404", "409"} <= paths["/api/v1/me/consent"]["patch"]["responses"].keys()
