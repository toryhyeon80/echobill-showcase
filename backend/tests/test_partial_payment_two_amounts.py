"""PM 10/14 — a partial payment without 남은·나머지 ("3만원인데 2만원 받고 만원 외상", "현금 2만원 외상 만원")
is DL-30 unsupported on the Rule and the LLM path, and sentences that only look alike are left as before."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import app
from app.models import LedgerEntry
from app.services import parse_llm, parse_pipeline
from app.services.parse_llm import LedgerExtraction, LLMParseResult
from app.services.payment_phrases import (
    says_clear_partial_payment,
    says_partial_payment,
)

client = TestClient(app)

PARTIAL = [
    "3만원인데 2만원 받고 만원 외상",
    "2만원은 받았고 만원은 달아놔",
    "현금 2만원 외상 만원",
    "김씨네 배추 3만원 2만원 받았어 만원은 외상",
    "배추 3만원어치 만원 받고 2만원 외상으로",
    "이만원 받구 만원 외상 달아줘",
    "카드로 2만원 결제하고 만원은 외상",
    "2만원 현금으로 받고 만원은 미수",
    "총 5만원 3만원 받음 2만원 외상",
    "외상 만원 하고 2만원은 받았어",
    "배추 2만원 받았고 무 만원은 외상",
    "푸른마트 무 3박스 삼만 원 이만 원만 받고 만 원 외상",
    "카드 결제 2만원 외상 만원",  # "결제" after the payment word
    "현금으로 2만원 주고 외상 만원",
    "배추 2만원 현금 무 만원 외상",
    "외상 만원에 현금 2만원",
    # 판단 사항 (PM 10/14): old credit coming in with a new sale, and part of a credit received — re-said, not stored
    "외상 만원 받고 현금 2만원",
    "3만원 외상 중에 2만원 받았어",
    # "다음에·나중에·다음 달에 받기로·주기로" right after an amount is the credit side
    "2만원 받고 만원은 다음에 받기로",
    "배추 2만원 받았고 무 만원은 다음 달에 주기로",
    "3만원어치 2만원 받고 만원은 나중에 받기로",
]
NOT_PARTIAL = [
    "배추 2만원 무 만원 외상",
    "개당 2천원 총 2만원 외상",
    "김씨네 배추 3만원 외상",
    "2만원 아니 3만원 외상",
    "주문 받고 배추 2만원 무 만원 외상",
    "배추 2만원 무 만원 현금 받았어",
    "배추 3박스 박스당 만원 3만원 외상으로 달아줘",
    "배추 2만원 무 만원 받았어 외상 아니야",
    "외상값 2만원 받았고 오늘 배추 만원은 현금",
    "배추 2만원 무 만원 외상 안 하고 현금 받았어",
    "2만원 받고 만원 거슬러줬어",
    "2천원씩 10개 2만원 받았어 외상 없음",
    "카드 2만원 현금 만원",
    "주문 받았어 배추 2만원 무 만원 달아놔",
    "개당 2천원 10개 2만원 외상 받아줘",
    "외상 2만원 받고 오늘 배추 만원 외상",
    "2만원 아니 3만원 받았어 외상 아니고",
    "배추 만원 현금 아니고 외상 2만원",
    "현금 말고 외상으로 배추 2만원 무 만원",
    "배추 2만원 무 만원 카드로 외상 안 해",
    "개당 2천원 20개 4만원 현금",
    "외상으로 배추 2박스 박스당 만원 2만원",
    # goods given later, not money
    "2만원 받고 무 만원어치는 다음에 주기로",
    "배추 3만원 받았어 무는 다음에 주기로",
    "배추 2만원 무 만원 다음에 주기로 했어",
]


def _settings(**overrides) -> Settings:
    base = {"app_env": "development", "openai_api_key": "test-key", "llm_parse_enabled": True, "_env_file": None}
    return Settings(**{**base, **overrides})


def _llm_credit_30000(utterance: str, *, settings: Settings) -> LLMParseResult:
    """The LLM saves the whole 30000 as 외상 (the case this guards)."""
    items = [{"name": "배추", "qty": None, "unit": None, "unit_price": None, "amount": 30000}]
    candidate = LedgerExtraction(counterparty="김씨네", items=items, total_amount=30000, credit=True)
    return LLMParseResult(candidate=candidate, model="gpt-4o-mini", processing_time_ms=900)


@pytest.mark.parametrize("text", PARTIAL)
def test_partial_payment_is_clear(text: str) -> None:
    assert says_clear_partial_payment(text) is True
    assert says_partial_payment(text) is True  # every clear one is a partial payment too (#128)


@pytest.mark.parametrize("text", NOT_PARTIAL)
def test_look_alike_is_not_clear(text: str) -> None:
    assert says_clear_partial_payment(text) is False


@pytest.mark.parametrize("text", PARTIAL)
@pytest.mark.parametrize("llm", [False, True], ids=["rule", "llm"])
def test_pipeline_marks_partial_payment(monkeypatch: pytest.MonkeyPatch, text: str, llm: bool) -> None:
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_credit_30000)
    result = parse_pipeline.structure_utterance(text, _settings(llm_parse_enabled=llm))
    assert result.transaction_type == "partial_payment"


@pytest.mark.parametrize("text", NOT_PARTIAL)
@pytest.mark.parametrize("llm", [False, True], ids=["rule", "llm"])
def test_pipeline_leaves_look_alike(monkeypatch: pytest.MonkeyPatch, text: str, llm: bool) -> None:
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_credit_30000)
    result = parse_pipeline.structure_utterance(text, _settings(llm_parse_enabled=llm))
    assert result.transaction_type is None


@pytest.mark.parametrize("llm", [False, True], ids=["rule", "llm"])
def test_credit_coming_in_is_a_payment_not_a_partial_payment(monkeypatch: pytest.MonkeyPatch, llm: bool) -> None:
    """"값" 없이도 외상 + 금액 + 받았 = 입금 안내 (PM 11/2) — 부분 결제 안내가 아니라 입금 등록 화면으로."""
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_credit_30000)
    text = "외상 2만원 받았어"
    assert says_clear_partial_payment(text) is False
    assert parse_pipeline.structure_utterance(text, _settings(llm_parse_enabled=llm)).transaction_type == "payment"


@pytest.mark.parametrize("llm", [False, True], ids=["rule", "llm"])
def test_two_counterparties_get_the_multiple_counterparty_question(monkeypatch: pytest.MonkeyPatch, llm: bool) -> None:
    """Credit to one buyer, cash from another is not a partial payment — 다중 거래처 안내 (eval G4 sentence)."""
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_credit_30000)
    text = "솔바람한테 배추 2포기 1만원 외상이고 새벽상회한테 무 3개 9천원 현금"
    result = parse_pipeline.structure_utterance(text, _settings(llm_parse_enabled=llm))
    assert result.transaction_type is None
    assert result.counterparty_count == 2


@pytest.mark.parametrize("text", ["3만원인데 2만원 받고 만원 외상", "현금 2만원 외상 만원"])
def test_parse_api_llm_path_rejects_and_stores_nothing(monkeypatch: pytest.MonkeyPatch, db: Session, text: str) -> None:
    """Not 외상 30000 — the amount owed would be inflated."""
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: _settings())
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_credit_30000)
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "pp-user"}).json()["access_token"]
    res = client.post(
        "/api/v1/ledger/parse",
        json={"utterance_text": text},
        headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())},
    )
    assert res.status_code == 400
    assert res.json()["error_code"] == "UNSUPPORTED_TRANSACTION"
    assert db.scalars(select(LedgerEntry)).first() is None
