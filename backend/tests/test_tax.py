"""Mock tax invoice (SPEC §5.4, 기획서 §9.2) — business number, 3 states, no duplicates, ownership."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.main import app
from app.models import IdempotencyRecord, LedgerEntry, Merchant, TaxInvoiceEvent

client = TestClient(app)

VALID_NUMBER = "1234567891"  # passes the NTS checksum; not a real business


def _headers(user_id: str = "user-b", key: str | None = None) -> dict[str, str]:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": user_id}).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Idempotency-Key": key or str(uuid.uuid4())}


def _register(number: str = "123-45-67891", user_id: str = "user-b", name: str | None = "김사장상회"):
    return client.post(
        "/api/v1/tax/business",
        json={"business_number": number, "business_name": name},
        headers=_headers(user_id),
    )


def _issue(entry_id: str, user_id: str = "user-b", key: str | None = None):
    return client.post("/api/v1/tax/issue", json={"entry_id": entry_id}, headers=_headers(user_id, key))


def _entry(db: Session, status: str = "confirmed", user_id: str = "user-b", deleted: bool = False) -> str:
    entry = LedgerEntry(
        entry_id=str(uuid.uuid4()),
        user_id=user_id,
        status=status,
        counterparty="김씨",
        items=[{"name": "배추", "qty": "3", "unit": "박스", "unit_price": None, "amount": 30000}],
        total_amount=30000,
        credit=True,
        deleted_at=datetime.now(timezone.utc) if deleted else None,
    )
    db.add(entry)
    db.commit()
    return entry.entry_id


@pytest.fixture
def mock_mode(monkeypatch: pytest.MonkeyPatch):
    """Switch TAX_MOCK_MODE for one test (services read the cached settings)."""

    def set_mode(mode: str, delay: int = 10) -> None:
        monkeypatch.setattr(get_settings(), "tax_mock_mode", mode)
        monkeypatch.setattr(get_settings(), "tax_mock_delay_seconds", delay)

    return set_mode


# --- business number -------------------------------------------------------------------


def test_register_business_number_stores_encrypted_and_returns_masked(db: Session) -> None:
    res = _register()
    assert res.status_code == 200
    assert res.json() == {
        "registered": True,
        "business_number_masked": "123-45-*****",
        "business_name": "김사장상회",
    }
    stored = db.get(Merchant, "user-b").business_number_enc
    assert VALID_NUMBER not in stored  # encrypted, not plain
    assert VALID_NUMBER not in client.get("/api/v1/tax/business", headers=_headers()).text


def test_idempotency_table_holds_no_raw_number(db: Session) -> None:
    _register()
    record = db.query(IdempotencyRecord).one()
    assert VALID_NUMBER not in record.request_hash
    assert VALID_NUMBER not in str(record.response_body)


@pytest.mark.parametrize("number", ["1234567890", "12345", "abcdefghij", ""])
def test_invalid_business_number_is_400(number: str, db: Session) -> None:
    res = _register(number)
    assert res.status_code == 400
    assert res.json()["error_code"] == "INVALID_BUSINESS_NUMBER"
    assert res.json()["problem_fields"] == ["business_number"]
    assert db.get(Merchant, "user-b") is None


def test_business_info_not_registered() -> None:
    assert client.get("/api/v1/tax/business", headers=_headers()).json() == {
        "registered": False,
        "business_number_masked": None,
        "business_name": None,
    }


# --- issue ----------------------------------------------------------------------------


def test_issue_without_business_number_is_400(db: Session) -> None:
    res = _issue(_entry(db))
    assert res.status_code == 400
    assert res.json()["error_code"] == "BUSINESS_INFO_REQUIRED"
    assert db.query(TaxInvoiceEvent).count() == 0


def test_issue_candidate_is_409(db: Session) -> None:
    _register()
    res = _issue(_entry(db, status="candidate"))
    assert res.status_code == 409
    assert res.json()["error_code"] == "ENTRY_NOT_CONFIRMED"


def test_issue_confirmed_is_mock_issued(db: Session) -> None:
    _register()
    entry_id = _entry(db)
    res = _issue(entry_id)
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "issued"
    assert body["state_label"] == "모의 완료"
    assert body["is_mock"] is True
    assert "모의" in body["notice"] and "국세청 전송이 아닙니다" in body["notice"]
    db.expire_all()
    assert db.query(LedgerEntry).filter_by(entry_id=entry_id).one().tax_status == "issued"


def test_second_request_returns_same_invoice_without_duplicate(db: Session) -> None:
    """New key, same entry (e.g. button tapped again later) → existing invoice, no second event."""
    _register()
    entry_id = _entry(db)
    first = _issue(entry_id)
    second = _issue(entry_id)
    assert first.json() == second.json()
    assert db.query(TaxInvoiceEvent).count() == 1


def test_same_key_replays(db: Session) -> None:
    _register()
    entry_id = _entry(db)
    key = str(uuid.uuid4())
    assert _issue(entry_id, key=key).json() == _issue(entry_id, key=key).json()
    assert db.query(TaxInvoiceEvent).count() == 1


def test_failed_shows_reason_and_retry_creates_new_attempt(db: Session, mock_mode) -> None:
    _register()
    entry_id = _entry(db)
    mock_mode("fail")
    failed = _issue(entry_id).json()
    assert failed["state"] == "failed"
    assert failed["state_label"] == "실패"
    assert failed["fail_reason"]

    mock_mode("issue")
    retried = _issue(entry_id).json()
    assert retried["state"] == "issued"
    assert db.query(TaxInvoiceEvent).count() == 2


def test_delay_stays_requested_then_completes_on_recheck(db: Session, mock_mode) -> None:
    """기획서 §9.2 처리 지연 → 재조회. No 4th state: the screen's '처리 중' is just `requested`."""
    _register()
    entry_id = _entry(db)
    mock_mode("delay", delay=10)
    assert _issue(entry_id).json()["state"] == "requested"
    assert client.get(f"/api/v1/tax/{entry_id}", headers=_headers()).json()["state"] == "requested"

    event = db.query(TaxInvoiceEvent).one()
    event.created_at = datetime.now(timezone.utc) - timedelta(seconds=11)
    db.commit()
    assert client.get(f"/api/v1/tax/{entry_id}", headers=_headers()).json()["state"] == "issued"


def test_retry_while_requested_does_not_duplicate(db: Session, mock_mode) -> None:
    _register()
    entry_id = _entry(db)
    mock_mode("delay")
    _issue(entry_id)
    assert _issue(entry_id).json()["state"] == "requested"
    assert db.query(TaxInvoiceEvent).count() == 1


# --- status / list / ownership -----------------------------------------------------------


def test_status_none_before_request(db: Session) -> None:
    entry_id = _entry(db)
    body = client.get(f"/api/v1/tax/{entry_id}", headers=_headers()).json()
    assert body["state"] == "none"
    assert body["state_label"] == "미발행"


def test_list_shows_latest_per_entry(db: Session, mock_mode) -> None:
    _register()
    a, b = _entry(db), _entry(db)
    mock_mode("fail")
    _issue(a)
    mock_mode("issue")
    _issue(a)
    _issue(b)
    invoices = client.get("/api/v1/tax", headers=_headers()).json()["invoices"]
    assert sorted((i["entry_id"], i["state"]) for i in invoices) == sorted([(a, "issued"), (b, "issued")])


def test_other_users_entry_is_404_without_data(db: Session) -> None:
    _register(user_id="user-a")
    entry_id = _entry(db, user_id="user-b")
    res = _issue(entry_id, user_id="user-a")
    assert res.status_code == 404
    assert "김씨" not in res.text and entry_id not in res.text
    assert client.get(f"/api/v1/tax/{entry_id}", headers=_headers("user-a")).status_code == 404
    assert client.get("/api/v1/tax", headers=_headers("user-a")).json()["invoices"] == []
    assert db.query(TaxInvoiceEvent).count() == 0


def test_soft_deleted_entry_is_404(db: Session) -> None:
    _register()
    assert _issue(_entry(db, deleted=True)).status_code == 404


def test_tax_endpoints_require_auth() -> None:
    assert client.get("/api/v1/tax").status_code == 401
    assert client.post("/api/v1/tax/issue", json={"entry_id": "x"}).status_code == 401
    assert client.post("/api/v1/tax/business", json={"business_number": VALID_NUMBER}).status_code == 401


def test_production_requires_business_number_key() -> None:
    from app.config import Settings

    with pytest.raises(ValueError, match="BUSINESS_NUMBER_KEY"):
        Settings(_env_file=None, app_env="production", jwt_secret="p" * 32)


def test_entry_without_total_is_400_not_zero_invoice(db: Session) -> None:
    _register()
    entry_id = _entry(db)
    entry = db.query(LedgerEntry).filter_by(entry_id=entry_id).one()
    entry.total_amount = None
    db.commit()
    res = _issue(entry_id)
    assert res.status_code == 400
    assert res.json()["error_code"] == "MISSING_REQUIRED_FIELD"
    assert res.json()["problem_fields"] == ["total_amount"]
    assert db.query(TaxInvoiceEvent).count() == 0


def test_register_then_retry_with_new_key_issues(db: Session) -> None:
    """The documented flow: 400 BUSINESS_INFO_REQUIRED → register → new key → issued."""
    entry_id = _entry(db)
    assert _issue(entry_id).json()["error_code"] == "BUSINESS_INFO_REQUIRED"
    _register()
    assert _issue(entry_id).json()["state"] == "issued"


def test_invalid_fernet_key_fails_at_startup() -> None:
    from app.config import Settings

    with pytest.raises(ValueError, match="Fernet"):
        Settings(_env_file=None, app_env="development", business_number_key="just-a-password")
