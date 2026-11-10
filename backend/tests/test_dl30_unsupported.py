"""DL-30 — purchases, returns and mixed partial payments → 400 UNSUPPORTED_TRANSACTION, nothing stored."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import LedgerEntry
from app.services.parse_mock import ParserResult
from app.services.validation import validate_candidate

client = TestClient(app)

GOOD = {
    "counterparty": "김씨",
    "items": [{"name": "배추", "qty": 3, "unit": "박스", "unit_price": None, "amount": 30000}],
    "total_amount": 30000,
    "credit": True,
}


@pytest.mark.parametrize("kind", ["purchase", "return", "partial_payment"])
def test_unsupported_types_fail_without_partial(kind: str) -> None:
    failure = validate_candidate(GOOD, transaction_type=kind)
    assert failure is not None
    assert failure.error_code == "UNSUPPORTED_TRANSACTION"
    assert failure.problem_fields == []
    assert failure.partial_candidate is None


@pytest.mark.parametrize("kind", ["sale", None])
def test_sale_or_unjudged_is_validated_as_before(kind) -> None:
    assert validate_candidate(GOOD, transaction_type=kind) is None


def test_default_keeps_old_callers_unchanged() -> None:
    """eval/validation_bridge and stored results call without the argument."""
    assert validate_candidate(GOOD) is None


def test_unsupported_wins_over_other_errors() -> None:
    """No candidate is made, so multi-counterparty / missing checks are moot."""
    failure = validate_candidate({"items": []}, counterparty_count=2, transaction_type="purchase")
    assert failure.error_code == "UNSUPPORTED_TRANSACTION"


def _parse(monkeypatch: pytest.MonkeyPatch, kind: str | None):
    monkeypatch.setattr(
        "app.api.ledger.structure_utterance",
        lambda text, *a, **k: ParserResult(fields=dict(GOOD), utterance_text=text, transaction_type=kind),
    )
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "user-b"}).json()["access_token"]
    return client.post(
        "/api/v1/ledger/parse",
        json={"utterance_text": "도매상에서 배추 3박스 30000원에 샀어"},
        headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())},
    )


def test_api_returns_400_and_stores_nothing(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    res = _parse(monkeypatch, "purchase")
    assert res.status_code == 400
    body = res.json()
    assert body["error_code"] == "UNSUPPORTED_TRANSACTION"
    assert body["problem_fields"] == []
    assert body["partial_candidate"] is None
    assert db.query(LedgerEntry).count() == 0


def test_api_sale_is_a_normal_candidate(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    res = _parse(monkeypatch, "sale")
    assert res.status_code == 200
    assert "transaction_type" not in res.json()  # internal only, not a DL-01 field
    assert db.query(LedgerEntry).count() == 1


def test_manual_repair_path_is_never_unsupported(db: Session) -> None:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "user-b"}).json()["access_token"]
    res = client.post(
        "/api/v1/ledger/parse",
        json={"utterance_text": "도매상에서 샀어", "candidate": GOOD},
        headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())},
    )
    assert res.status_code == 200
