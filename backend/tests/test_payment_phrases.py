"""Shared payment-phrase checks (payment_phrases.py) — PM examples, and the parser vs voice-answer credit decision."""

import pytest

from app.services.payment_phrases import (
    has_paid_phrase,
    says_clear_partial_payment,
    says_not_paid_now,
    says_not_paid_yet,
    says_not_received,
    says_partial_payment,
)
from app.services.postprocess import postprocess_candidate, says_paid_phrase
from app.services.voice_answer import interpret


# --- shared checks: PM examples (pp-1.4 PM 10/8 · #82 리뷰 10/12) -------------------------------------------

@pytest.mark.parametrize("text, paid_phrase, not_paid_yet, not_received", [
    ("현금 아직 못 받았어", False, True, True),
    ("다음 주에 받음", False, True, False),
    ("월말에 받기로", False, True, False),
    ("내일 준대", False, True, False),
    ("돈 받음", True, False, False),
    ("3만원 받았어", True, False, False),
    ("삼만 원 받았어", True, False, False),
    ("용돈 줬어", False, False, False),  # 돈 only as its own word
    ("배추 3 받았어", False, False, False),  # an amount is a numeral + 원
    ("이체했대", False, False, False),  # a payment word, not a paid phrase — the caller decides
    ("돈 줬어", True, False, False),
    ("결제 완료", True, False, False),
    ("결제 완료 안 됨", False, False, False),
])
def test_pm_examples(text: str, paid_phrase: bool, not_paid_yet: bool, not_received: bool) -> None:
    assert has_paid_phrase(text) is paid_phrase
    assert says_not_paid_yet(text) is not_paid_yet
    assert says_not_received(text) is not_received


@pytest.mark.parametrize("text, partial", [
    ("9000원 중에 3000원 받았어", True),
    ("3000원 받았고 나머지는 외상", True),
    ("3000원만 받았어", True),
    ("3000원 받았어", False),
])
def test_partial_payment(text: str, partial: bool) -> None:
    assert says_partial_payment(text) is partial


# #115: "받고 … 나머지/남은" (normalized text, as the LLM gets it)
@pytest.mark.parametrize("text", [
    "솔바람 양파 3박스 20000원 받고 남은 거 외상",
    "솔바람 양파 3박스 20000원 받고 남은 돈 외상",
    "솔바람 양파 3박스 20000원 받고 남은 건 미수",
    "솔바람 양파 3박스 20000원 받고 나머지는 외상",
    "솔바람 양파 3박스 20000원 받고 나머진 외상",
    "솔바람 양파 3박스 20000원 받았고 나머진 외상",
    "솔바람 양파 3박스 20000원 받구 나머지 달아놔",
    "솔바람 양파 3박스 현금 20000원 받고 남은 금액은 외상으로",
    "솔바람 양파 3박스 20000원 받고 나머지는 다음 달에",
    "솔바람 양파 3박스 받고 나서 나머지 품목은 외상",  # some items paid, the rest on credit → asked
    # PR #128 리뷰: an amount and up to two words between 남은 and the credit word
    "솔바람 양파 3박스 20000원 받고 남은 10000원 외상 합계 30000원",
    "솔바람 양파 3박스 20000원 받고 남은 건 다음에 외상 합계 30000원",
    "솔바람 양파 3박스 20000원 받고 남은 10000원은 다음 달에 외상",
    "솔바람 양파 3박스 2만원 받고 남은 1만원 외상",  # not normalized (normalization off)
    "솔바람 양파 3박스 20000원 받고 남은 양파는 내일 외상",  # a word in between, still credit → asked (safe side)
])
def test_partial_payment_received_then_remainder(text: str) -> None:
    assert says_partial_payment(text) is True


@pytest.mark.parametrize("text", [
    "솔바람 양파 3박스 30000원 현금 받고 끝",
    "솔바람 양파 3박스 30000원 카드로 다 받고 외상 없음",
    "솔바람 양파 3박스 20000원 받고 남은 물건은 내일",  # 남은 = the goods, no credit word
    "솔바람 양파 3박스 30000원 받고 남은 양파는 내일 배달",
    "솔바람 양파 3박스 30000원 현금으로 받고 남은 건 없어",
    "솔바람 양파 3박스 30000원 현금 받고 남은 건 외상 없음",  # credit word negated
    "솔바람 양파 3박스 30000원 받고추 2박스 외상",  # an item with 받고 inside, no 나머지·남은
    "솔바람 남은 양파 3박스 30000원 외상",  # no received word before 남은
    "솔바람 양파 3박스 20000원 받고 남은 10000원 외상 아니고 카드",  # credit word negated
    "솔바람 양파 3박스 20000원 받고 남은 건 다음에 외상 안 해",
])
def test_not_partial_payment(text: str) -> None:
    assert says_partial_payment(text) is False


@pytest.mark.parametrize("text, paid", [
    ("3만원 받았어", True),
    ("돈 받음", True),
    ("나중에 3만원 받기로", False),  # a later marker cancels the phrase
    ("3만원 다음 주에 받음", False),
    ("외상으로 3만원 받았어", False),  # the credit word decides
])
def test_says_paid_phrase_uses_shared_checks(text: str, paid: bool) -> None:
    assert says_paid_phrase(text) is paid


# --- same sentence → parser post-processing (LLM credit null) vs voice-answer credit field ----------------------

def _parser_fill(text: str) -> bool | None:
    return postprocess_candidate({"credit": None, "items": []}, text)[0]["credit"]


def _voice_fill(text: str) -> bool | None:
    result = interpret("credit", text)
    return result.value if result.status == "ok" else None


@pytest.mark.parametrize("text, value", [
    # both leave it empty (asked again)
    ("현금 아직 못 받았어", None),
    ("다음 주에 받음", None),
    ("월말에 받기로", None),
    ("내일 준대", None),
    ("현금 안 받았어", None),
    ("용돈 줬어", None),
    ("배추 3 받았어", None),
    ("어제 받았어요", None),
    # both fill
    ("돈 받았어", False),
    ("돈 줬어", False),
    ("돈 들어왔어", False),
    ("현금 받았어", False),
    ("계좌로 받았어", False),
    ("결제 완료", False),
    ("계산 끝났어", False),
    ("외상으로 해 줘", True),
])
def test_parser_and_voice_answer_agree(text: str, value: bool | None) -> None:
    assert _parser_fill(text) is value
    assert _voice_fill(text) is value


# Known, intended differences: the voice answer fills only exact short answers (PM #72) and has two PM switches.
@pytest.mark.parametrize("text, parser, voice, why", [
    ("3만원 받았어", False, None, "exact answers only"),
    ("돈 받음", False, None, "exact answers only"),
    ("아까 계좌로 받았어요", False, None, "exact answers only — time adverb"),
    ("이체했대", False, None, "ACCEPT_HEARSAY_PAYMENT=False"),
    ("받았어요", None, False, "ACCEPT_PAID_WITHOUT_PAY_WORD=True"),
])
def test_parser_and_voice_answer_known_differences(text: str, parser: bool | None, voice: bool | None,
                                                   why: str) -> None:
    assert _parser_fill(text) is parser, why
    assert _voice_fill(text) is voice, why


# PM 10/12 확정: 결제 단어 + 미래·약속 표지도 "아직 못 받았어"와 같은 판정; 이미 받은 동사 옆 시간 낱말은 아님
@pytest.mark.parametrize("text, not_paid_now", [
    ("현금 아직 못 받았어", True),
    ("현금 나중에", True),
    ("현금으로 내일 준대", True),
    ("계좌로 다음 주에 보낸대", True),
    ("카드는 월말에", True),
    ("다음 주에 현금으로 받음", True),          # 받음 = 약속 ("다음 주에 받음")
    ("현금 나중에 받기로 했어", True),
    ("주말에 현금으로 받았어", False),
    ("내일 배달, 현금 받았어", False),
    ("월말에 카드로 결제했어", False),
    ("외상은 다음에 안 해 현금", False),       # 부정된 절 안의 "다음에" (pp-1.4 부정 보완)
    ("현금 받았어", False),
    ("현금", False),
])
def test_says_not_paid_now(text: str, not_paid_now: bool) -> None:
    assert says_not_paid_now(text) is not_paid_now


# PR #128 PM 요청: an amount right before 받고·받았고·받구, then 남은·나머지, then a credit word → clear (DL-30 unsupported)
CLEAR_PARTIAL = [
    "솔바람 양파 3박스 20000원 받고 남은 건 외상 합계 30000원",
    "솔바람 양파 3박스 20000원 받고 나머지 외상 합계 30000원",
    "솔바람 양파 3박스 20000원 받고 남은 10000원 외상 합계 30000원",
    "솔바람 양파 3박스 20000원 받고 남은 건 다음에 외상 합계 30000원",
    "솔바람 양파 3박스 30000원 20000원 받고 남은 돈 10000원은 외상",
    "솔바람 양파 3박스 30000원 20000원 받았고 나머지 10000원은 다음 달에 외상",
    "솔바람 양파 3박스 30000원 20000원만 받고 나머진 달아놔",
    "솔바람 양파 3박스 30000원 20000원 받구 남은 거 미수",
    "솔바람 양파 3박스 30000원 20000원 받고 남은 양파 2박스는 내일 외상",  # no word limit
    "솔바람 양파 3박스 30000원 20000원은 받았고 나머지는 외상으로",
    "솔바람 양파 3박스 3만원 2만원 받고 남은 1만원 외상",  # not normalized
    # PM 10/14: two different amounts, one received, and a credit word (was ambiguous in #128)
    "솔바람 양파 3박스 30000원 20000원 현금으로 받고 나머지 외상",
    "솔바람 양파 3박스 30000원 20000원 받았어 나머지는 외상",
    # 회의 10/23 (seed2-135): a smaller amount received after 중에 or as "N원만 받", no credit word (was ambiguous)
    "솔바람 양파 3박스 30000원 중에 20000원 받았어",
    "솔바람 양파 3박스 30000원 20000원만 받았어",
]
# partial (credit asked) but not clear: no amount right before 받고, 받았어·받음, or no credit word after 나머지
AMBIGUOUS_PARTIAL = [
    "솔바람 양파 3박스 30000원 현금 받고 나머지 외상",
    "솔바람 양파 3박스 30000원 20000원 받고 나머지는 다음 달에",
    "솔바람 양파 3박스 30000원 주문 받고 나머지 배추 외상",
    "솔바람 배추 받고 남은 박스 외상 20000원",
]


@pytest.mark.parametrize("text", CLEAR_PARTIAL)
def test_clear_partial_payment(text: str) -> None:
    assert says_clear_partial_payment(text) is True
    assert says_partial_payment(text) is True  # every clear one is a partial payment too


@pytest.mark.parametrize("text", AMBIGUOUS_PARTIAL)
def test_ambiguous_partial_payment_is_not_clear(text: str) -> None:
    assert says_partial_payment(text) is True
    assert says_clear_partial_payment(text) is False


@pytest.mark.parametrize("text", [
    "솔바람 양파 3박스 30000원 현금 받고 끝",
    "솔바람 양파 3박스 30000원 카드로 다 받고 외상 없음",
    "솔바람 양파 3박스 20000원 받고 남은 물건은 내일",
    "솔바람 양파 3박스 30000원 받고 남은 양파는 내일 배달",
    "솔바람 양파 3박스 30000원 현금으로 받고 남은 건 없어",
    "솔바람 양파 3박스 30000원 현금 받고 남은 건 외상 없음",
    "솔바람 남은 양파 3박스 30000원 외상",
    "솔바람 양파 3박스 30000원 20000원 받고 남은 10000원 외상 아니고 카드",
    "솔바람 양파 3박스 30000원 20000원 받고 나머지 외상 안 해",
])
def test_not_clear_partial_payment(text: str) -> None:
    assert says_clear_partial_payment(text) is False
