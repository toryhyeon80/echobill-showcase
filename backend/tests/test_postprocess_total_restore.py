"""Rule 3 (total_restored_stated) guards — #98 리뷰(병철님) 머지 전 요청 후속.

"나머지" leaves the total alone, and the rule runs only for a single item. The single-item case
(Seed v2 114) is in test_postprocess.py.
"""

from types import SimpleNamespace

import pytest

from app.services.parse_llm import build_llm_input
from app.services.postprocess import postprocess_candidate
from app.services.validation import validate_candidate


def _sent(utterance: str) -> str:
    """The text the LLM gets with normalization on ("2만원" → "20000원")."""
    return build_llm_input(utterance, SimpleNamespace(ko_numeral_normalize=True))


@pytest.mark.parametrize("item_amount", [30000, None])
def test_remainder_on_credit_is_not_saved_as_the_stated_total(item_amount) -> None:
    # The LLM put the remainder (10000) as the total. Restoring "합계 3만원" would save 외상 30000 —
    # the 20000 paid in cash would become money owed.
    candidate = {
        "counterparty": "김사장",
        "items": [{"name": "배추", "qty": 3, "unit": "박스", "unit_price": None, "amount": item_amount}],
        "total_amount": 10000,
        "credit": True,
    }
    result, applied = postprocess_candidate(candidate, _sent("김사장 배추 3박스 현금 2만원 받고 나머지 외상 합계 3만원"))
    assert "total_restored_stated" not in applied
    assert result["total_amount"] == 10000
    # #115: "받고 … 나머지" is a partial payment now → credit is asked first
    failure = validate_candidate(result)
    assert failure is not None and failure.problem_fields == ["credit"]
    if item_amount is not None:  # once credit is answered, the contradiction is asked (main behavior kept)
        failure = validate_candidate({**result, "credit": True})
        assert failure is not None and failure.error_code == "AMOUNT_MISMATCH"


@pytest.mark.parametrize("utterance", [
    "어제 김사장 배추 3박스 계좌로 만원 받고 나머지는 외상 전부 4만원",
    "김사장 배추 3박스 3만원 나머지는 달아 놔 합계 3만원",
])
def test_other_remainder_phrasings_leave_the_total_alone(utterance) -> None:
    candidate = {
        "counterparty": "김사장",
        "items": [{"name": "배추", "qty": 3, "unit": "박스", "unit_price": None, "amount": None}],
        "total_amount": 20000,
        "credit": True,
    }
    result, applied = postprocess_candidate(candidate, _sent(utterance))
    assert "total_restored_stated" not in applied and result["total_amount"] == 20000


def test_remainder_of_the_stock_still_restores_the_total() -> None:
    # "나머지 배추" is the rest of the stock, not a partial payment: main asks (AMOUNT_MISMATCH), and so do we
    candidate = {
        "counterparty": "김사장",
        "items": [{"name": "배추", "qty": 2, "unit": "박스", "unit_price": 12000, "amount": 24000}],
        "total_amount": 24000,
        "credit": True,
    }
    result, applied = postprocess_candidate(candidate, _sent("김사장 나머지 배추 2박스 박스당 12000원 전부 2만원 외상"))
    assert "total_restored_stated" in applied and result["total_amount"] == 20000
    failure = validate_candidate(result)
    assert failure is not None and failure.error_code == "AMOUNT_MISMATCH"


@pytest.mark.parametrize("amounts, llm_total", [
    ((5000, 12000), 17000),  # "양파 4망 전부 12000원" read as the onion amount — sums up, nothing to ask
    ((5000, None), 17000),   # restoring 12000 would drop the 5000 of 대파 with HTTP 200
])
def test_several_items_leave_the_total_alone(amounts, llm_total) -> None:
    candidate = {
        "counterparty": "김사장",
        "items": [{"name": "대파", "qty": 2, "unit": "단", "unit_price": None, "amount": amounts[0]},
                  {"name": "양파", "qty": 4, "unit": "망", "unit_price": None, "amount": amounts[1]}],
        "total_amount": llm_total,
        "credit": None,
    }
    result, applied = postprocess_candidate(candidate, _sent("대파 2단 5000원 양파 4망 전부 12000원"))
    assert "total_restored_stated" not in applied
    assert result["total_amount"] == llm_total
    assert result["items"] == candidate["items"]


def test_several_items_said_amounts_are_not_asked() -> None:
    # every item amount is said → the LLM total stands and no amount is asked (credit is: no payment word, Rule 0)
    candidate = {
        "counterparty": "김사장",
        "items": [{"name": "대파", "qty": 2, "unit": "단", "unit_price": None, "amount": 5000},
                  {"name": "양파", "qty": 4, "unit": "망", "unit_price": None, "amount": 12000}],
        "total_amount": 17000,
        "credit": False,
    }
    result, applied = postprocess_candidate(candidate, _sent("대파 2단 5000원 양파 4망 전부 12000원"))
    assert "total_restored_stated" not in applied and result["total_amount"] == 17000
    failure = validate_candidate(result)
    assert failure is not None and failure.problem_fields == ["credit"]


def test_several_items_with_a_computed_amount_restore_the_total() -> None:
    # 배추 14000 is 2×7000, never said: the LLM total 34000 is computed too → 2만원 is restored and asked
    candidate = {
        "counterparty": "김사장",
        "items": [{"name": "배추", "qty": 2, "unit": "박스", "unit_price": 7000, "amount": 14000},
                  {"name": "무", "qty": 3, "unit": "개", "unit_price": None, "amount": 20000}],
        "total_amount": 34000,
        "credit": False,
    }
    result, applied = postprocess_candidate(candidate, _sent("배추 2박스 박스당 7000원 무 3개 다 합해서 2만원"))
    assert "total_restored_stated" in applied and result["total_amount"] == 20000
    assert result["items"] == candidate["items"]
    # no payment word, so credit is asked first (Rule 0); once answered, the amounts are asked
    failure = validate_candidate({**result, "credit": False})
    assert failure is not None and failure.error_code == "AMOUNT_MISMATCH"


@pytest.mark.parametrize("utterance, llm_total", [
    # the LLM put the remainder as the total; Rule 3 used to restore 30000 and save 외상 30000
    ("솔바람 양파 3박스 2만원 받고 남은 건 외상 합계 3만원", 10000),
    ("솔바람 양파 3박스 2만원 받고 남은 건 외상 합계 3만원", 30000),
    # the LLM total was 30000 already; nothing cleared the 외상
    ("솔바람 양파 3박스 2만원 받고 나머지 외상 합계 3만원", 30000),
    # PR #128 리뷰 (병철님 재현 표): an amount or two words between 남은 and 외상
    ("솔바람 양파 3박스 2만원 받고 남은 1만원 외상 합계 3만원", 30000),
    ("솔바람 양파 3박스 2만원 받고 남은 건 다음에 외상 합계 3만원", 10000),
    ("솔바람 양파 3박스 2만원 받고 남은 건 다음에 외상 합계 3만원", 30000),
])
def test_received_then_remainder_on_credit_is_asked(utterance, llm_total) -> None:
    # #115: "받고 … 남은/나머지 외상" is a partial payment (DL-30) → credit asked, never saved as 외상 30000
    candidate = {
        "counterparty": "솔바람",
        "items": [{"name": "양파", "qty": 3, "unit": "박스", "unit_price": None, "amount": 30000}],
        "total_amount": llm_total,
        "credit": True,
    }
    result, applied = postprocess_candidate(candidate, _sent(utterance))
    assert applied == ["credit_cleared_partial_payment"]
    assert result["credit"] is None and result["total_amount"] == llm_total
    failure = validate_candidate(result)
    assert failure is not None and failure.problem_fields == ["credit"]


def test_remainder_after_received_leaves_the_total_alone() -> None:
    # PR #128 리뷰: "받고 … 남은" guards Rule 3 even when the partial-payment check misses (the credit word comes
    # before 남은) — restoring 30000 would save the whole 30000 as 외상
    candidate = {
        "counterparty": "솔바람",
        "items": [{"name": "양파", "qty": 3, "unit": "박스", "unit_price": None, "amount": 30000}],
        "total_amount": 10000,
        "credit": True,
    }
    result, applied = postprocess_candidate(
        candidate, _sent("솔바람 양파 3박스 외상 2만원 받고 남은 건 다음 주에 합계 3만원"))
    assert "total_restored_stated" not in applied and result["total_amount"] == 10000
    failure = validate_candidate(result)
    assert failure is not None and failure.error_code == "AMOUNT_MISMATCH"
