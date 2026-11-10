"""Sentences that are not a sale (PM 10/26 내부 테스트 ②) → 400 NOT_A_SALE + `redirect` to the screen that handles it.

Before, the model's guess became a sale candidate and a 되묻기 answer saved a wrong transaction. PM sentences are
here only (not in Seed·Golden).
"""

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
from app.services.parse_pipeline import rule_parse

client = TestClient(app)
_NORMALIZED = Settings(_env_file=None, app_env="development")


def _parse(text: str):
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "non-sale"}).json()["access_token"]
    return client.post("/api/v1/ledger/parse", json={"utterance_text": text},
                       headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())})


@pytest.mark.parametrize(("text", "redirect"), [
    # PM 10/26 문장
    ("김씨네 지난달 외상값 오만 원 입금 들어왔어", "payment"),
    ("아까 가나상회 배추 세 박스 아니고 네 박스였어", "edit"),
    ("가나상회 외상 얼마 남았어?", "inquiry"),
    ("이번 주 김씨네 외상 합쳐서 이십만 원", "moved_credit"),
    # 같은 뜻 다른 말
    ("김씨네 외상 다 갚았어", "payment"),
    ("가나상회 밀린 돈 삼만 원 송금 받았어", "payment"),
    # PM 내부 테스트 2-17: "값" 없는 외상 + 금액 + 입금/들어왔 · 받아냄
    ("외상 오만 원 입금 들어왔어", "payment"),
    ("김씨네 외상 오만 원 입금 들어왔어", "payment"),
    ("가나상회 외상 오만 원 들어왔어", "payment"),
    ("가나상회 외상 50,000원 입금됐어", "payment"),
    ("가나상회 외상 오만 원 입금", "payment"),
    ("미수금 삼만 원 현금으로 받아냄", "payment"),
    ("미수금 삼만 원 받아냈어", "payment"),
    ("외상값 삼만 원 받아 냈어", "payment"),
    # PM 11/2 새 문장: 받았 · 입금함 · 금액과 입금 말 사이의 낱말
    ("외상 오만 원 받았어", "payment"),
    ("외상 3만원 입금함", "payment"),
    ("김씨네 외상 오만 원 계좌로 들어왔어", "payment"),
    ("김씨네 외상 오만 원 현금으로 받았어", "payment"),
    ("김씨네 외상 오만 원 통장으로 입금됐어", "payment"),
    ("김씨네 외상 오만 원 방금 계좌로 들어왔어", "payment"),
    ("김씨네 외상 오만 원 오늘 들어옴", "payment"),
    ("김씨네 외상 오만 원 받음", "payment"),
    ("김씨네 외상 오만 원 받아냈어", "payment"),
    ("아까 적은 거 잘못 적었어", "edit"),
    ("아까 거 고쳐 줘", "edit"),
    ("아까 적은 명함 다섯 통 아니고 여섯 통이야", "edit"),  # "였어" 없이 (PM 10/26 새 문장)
    ("아까 적은 명함 다섯 통 아니고 여섯 통이에요.", "edit"),  # 끝말 + 문장부호 (수경 #228 사후 리뷰)
    ("아까 적은 거 이만 원 아니고 삼만 원이야", "edit"),  # 기록을 가리키는 말 (승현 #235 리뷰)
    ("아까 넣은 거 이만 원 아니고 삼만 원이야", "edit"),  # "넣은"은 빠져도 때 말 + 금액 둘 → 고치기
    ("가나상회 남은 외상 알려줘", "inquiry"),
    ("가나상회 잔액 얼마야", "inquiry"),
    ("가나상회 그동안 밀린 외상 삼십만 원", "moved_credit"),
    ("지난달 다라식당 외상 전부 해서 오십만 원", "moved_credit"),
])
def test_not_a_sale_is_recognized(text: str, redirect: str) -> None:
    assert rule_parse(text, _NORMALIZED).transaction_type == redirect


@pytest.mark.parametrize("text", [
    "가나상회 배추 두 박스 이만 원 입금 받았어",  # 계좌로 받은 판매
    # PM 내부 테스트 2-17 수정의 경계: 입금이 끝나지 않았거나 외상 + 금액 + 입금 순서가 아님
    "가나상회 배추 삼만 원 외상 입금은 내일",
    "가나상회 외상 오만 원 내일 입금해 준대",
    "가나상회 외상 오만 원 입금 안 들어왔어",
    "가나상회 외상 오만 원 입금은 내일",
    "외상 오만 원 입금해 줄게",
    # PM 11/2: 아직 안 받은 말은 넓힌 뒤에도 판매 (안·이따·내일·줄게)
    "외상 오만 원 안 받았어",
    "외상 오만 원 못 받았어",
    "외상 오만 원 이따 입금해",
    "김씨네 외상 오만 원 내일 들어올 거야",
    "외상 오만 원 받을게",
    "외상 오만 원 계좌로 안 들어왔어",
    "외상 오만 원 계좌로 입금해 줄게",
    "외상 오만 원 내일 계좌로 입금함",
    "외상 오만 원 현금으로 줄게",
    "외상 오만 원 입금 안 함",
    "외상 오만 원어치 받았어",  # 물건을 받은 말
    "외상 오만 원 배추 받았어",
    "김씨네 배추 오만 원 외상 받았어",  # 금액 뒤 외상 = 판매
    "가나상회 배추 두 박스 외상 오만 원 입금 들어왔어",  # 수량을 말하면 판매
    "가나상회 배추 삼만 원 받았어",  # 빚 낱말 없음
    "아까 가나상회 배추 세 박스 삼만 원 외상",  # 아까 한 판매
    "가나상회 배추 세 박스 아니 네 박스 사만 원 외상",  # 지금 말하며 바로잡기 (#120)
    "청솔정육에 소불고기 5킬로 8만원이라고 했는데 9만원으로 고쳐줘 외상",  # Seed — 지금 판매의 정정
    "얼마 전에 가나상회 배추 두 박스 이만 원 외상",  # 얼마 전 = 때
    "그 사람한테 그냥 얼마어치 좀 달아놔",  # Seed — 얼마어치 = 판매
    "가나상회 배추 무 합쳐서 삼만 원 외상",  # 기간 없이 합친 말 = 한 번의 판매
    "가나상회 지난달 배추 두 박스 이만 원 외상",
    "외상값 2만원 받았고 오늘 배추 만원은 현금",
    "어제 소나무카페 빨대 세 박스 아니 네 박스 사만 원 외상",  # 지금 말하며 바로잡기
    "아까 가나상회 배추 세 박스 삼만 원 외상이야",  # 아니고 없음  # 입금 + 새 판매 (금액 둘) — 기존 동작 그대로
    # 수경 #228 사후 리뷰: 어제 판매를 말하며 바로 고쳐 말하기 — 끝말이 낱말 가운데·외상에 붙음
    "어제 가나상회 배추 아니고 야채 두 박스 이만 원 외상",  # 야채의 야
    "어제 김씨네 사과 아니고 배 세 박스 삼만 원 외상이에요",  # 외상이에요
    "어제 김씨네 사과 아니고 배 세 박스 삼만 원 현금이야",
    "어제 김씨네 사과 아니고 배 세 박스 삼만 원 외상입니다.",
    # 승현 #235 리뷰: 때 말만 있고 수량 + 금액 하나인 완전한 판매 → 끝말과 상관없이 판매
    "어제 김씨네 사과 아니고 배 세 박스 삼만 원 카드야",
    "어제 가나상회 배추 아니고 무 두 박스 이만 원 외상이었어",
    "어제 가나상회에 넣은 배추 아니고 무 두 박스 이만 원 외상이었어",  # 넣은 = 납품 (승현 #237 리뷰 문장)
    "어제 가나상회에 넣은 배추 아니고 무 두 박스 이만 원 카드야",
])
def test_sales_stay_sales(text: str) -> None:
    assert rule_parse(text, _NORMALIZED).transaction_type is None


@pytest.mark.parametrize(("text", "redirect", "screen"), [
    ("김씨네 지난달 외상값 오만 원 입금 들어왔어", "payment", "입금 등록"),
    ("외상 오만 원 입금 들어왔어", "payment", "입금 등록"),  # PM 내부 테스트 2-17
    ("미수금 삼만 원 현금으로 받아냄", "payment", "입금 등록"),
    ("김씨네 외상 오만 원 계좌로 들어왔어", "payment", "입금 등록"),  # PM 11/2
    ("아까 가나상회 배추 세 박스 아니고 네 박스였어", "edit", "장부 탭"),
    ("가나상회 외상 얼마 남았어?", "inquiry", "받을 돈"),
    ("이번 주 김씨네 외상 합쳐서 이십만 원", "moved_credit", "외상 옮기기"),
])
def test_parse_returns_redirect_and_stores_nothing(text: str, redirect: str, screen: str, db: Session) -> None:
    res = _parse(text)
    body = res.json()
    assert (res.status_code, body["error_code"], body["redirect"]) == (400, "NOT_A_SALE", redirect)
    assert screen in body["message"]
    assert body["partial_candidate"] is None and body["problem_fields"] == []  # nothing to fill in
    assert db.scalars(select(LedgerEntry)).first() is None


def test_other_errors_carry_no_redirect() -> None:
    assert _parse("가나상회 배추 삼만 원 외상").json()["redirect"] is None


def _llm_sale_guess(utterance: str, *, settings: Settings) -> LLMParseResult:
    """What the model made of "외상값 … 입금": a 김씨네 credit sale with the item missing (asked → saved wrong)."""
    candidate = LedgerExtraction(counterparty="김씨네", items=[], total_amount=50000, credit=True)
    return LLMParseResult(candidate=candidate, model="gpt-4o-mini", processing_time_ms=900)


def test_llm_path_uses_the_rule_judgment(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """LLM on: the model's sale guess is not asked about — the Rule's NOT_A_SALE comes first (like DL-30)."""
    settings = Settings(_env_file=None, app_env="development", openai_api_key="test-key", llm_parse_enabled=True)
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_sale_guess)
    body = _parse("김씨네 지난달 외상값 오만 원 입금 들어왔어").json()
    assert (body["error_code"], body["redirect"]) == ("NOT_A_SALE", "payment")
    assert db.scalars(select(LedgerEntry)).first() is None
