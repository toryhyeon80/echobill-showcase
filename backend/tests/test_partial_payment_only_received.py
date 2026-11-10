"""회의 10/23 (seed2-135) — only part of a sale received, no credit word ("98400원어치 팔고 그중에 32000원만 받았어")
is a clear partial payment (DL-30 unsupported), so a "외상" answer can no longer save the whole amount as credit."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.services import parse_pipeline
from app.services.payment_phrases import (
    says_clear_partial_payment,
    says_partial_payment,
)

client = TestClient(app)

ONLY_PART = [
    "미소분식한테 98400원어치 팔고 그중에 32000원만 받았어",  # seed2-135
    "98400원어치 팔고 3000원만 받았어",  # same form without 그중에 (PM 확정 10/23)
    "9000원 중에 3000원 받았어",
    "배추 삼만 원어치 팔고 이만 원만 받았어",  # Hangul numerals + 원 (before normalization)
    "고운떡방에 두부 두 모 5600원 팔았는데 1000원만 받고 나머지는 외상",  # seed2-136, already caught
]
STILL_A_CREDIT_QUESTION = [
    "3000원만 받았어",  # one amount: the sale amount is unknown
    "삼만 받았어",  # 3만 원 received — 만 is the number, not "only"
    "가온유통한테 멸치볶음 세 팩 12300원, 돈은 나중에 한꺼번에 준대",  # seed2-105: 나중에
    "늘봄반찬 김 7톳 68600원, 돈은 나중에 한꺼번에 준대",  # seed2-109
    "배추 3000원 무 10000원만 받았어",  # the received amount is the larger one
    # #198 리뷰: a discount is paid in full at a lower price
    "가나상회 배추 3박스 30000원인데 단골이라 25000원만 받았어",
    "가나상회 배추 3만원인데 깎아서 2만5천원만 받았어",
    "배추 30000원 할인해서 25000원만 받았어",
    # #198 리뷰: 중에 not right after an amount
    "배추 30000원, 회의 중에 10000원 받았어",
]


@pytest.mark.parametrize("text", ONLY_PART)
def test_part_received_is_a_clear_partial_payment(text: str) -> None:
    assert says_clear_partial_payment(text) and says_partial_payment(text)


@pytest.mark.parametrize("text", STILL_A_CREDIT_QUESTION)
def test_lookalikes_are_not_a_clear_partial_payment(text: str) -> None:
    assert not says_clear_partial_payment(text)


@pytest.mark.xfail(strict=True, reason="지금은 못 잡음 — 받은 금액에 원이 없어 숫자 정리가 안 됨 (알려진 한계, 회의 10/23)")
def test_known_limit_amount_without_won() -> None:
    assert says_clear_partial_payment("배추 십만원어치 팔고 삼만 받았어")


def test_parse_api_asks_to_say_it_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule path: seed2-135 is now 400 UNSUPPORTED_TRANSACTION, not a credit question."""
    settings = Settings(app_env="development", llm_parse_enabled=False, _env_file=None)
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: settings)
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "partial-user"}).json()["access_token"]
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": ONLY_PART[0]},
                      headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())})
    assert res.status_code == 400
    assert res.json()["error_code"] == "UNSUPPORTED_TRANSACTION"
