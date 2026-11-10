"""Rule 0 and a payment/credit word glued to a name the LLM extracted (PM 10/12, Seed v2 재채점 후속).

STT drops spaces, so "외상" can sit right before the counterparty or item name, or "카드로" right after the counterparty.
Such a word only keeps Rule 0 from clearing the LLM value as "no word"; Rule 0b · Rule 1 · negation are unchanged.
All sentences are new (not Dev·Seed).
"""

from app.services.postprocess import postprocess_candidate

NO_WORD = "credit_cleared_no_word"


def _llm(counterparty: str, item: str, credit: bool | None) -> dict:
    return {
        "counterparty": counterparty,
        "items": [{"name": item, "qty": 2, "unit": "판", "unit_price": None, "amount": 14000}],
        "total_amount": 14000,
        "credit": credit,
    }


def test_credit_glued_before_counterparty_keeps_llm_credit() -> None:
    result, applied = postprocess_candidate(_llm("푸른마트", "계란", True), "외상푸른마트에 계란 2판 14000원")
    assert result["credit"] is True and NO_WORD not in applied


def test_credit_glued_before_item_keeps_llm_credit() -> None:
    result, applied = postprocess_candidate(_llm("청솔식당", "배추", True), "청솔식당에 외상배추 5포기 14000원")
    assert result["credit"] is True and NO_WORD not in applied


def test_payment_glued_after_counterparty_keeps_llm_cash() -> None:
    result, applied = postprocess_candidate(_llm("미래분식", "단무지", False), "미래분식카드로 단무지 2통 14000원")
    assert result["credit"] is False and NO_WORD not in applied


def test_payment_word_after_item_name_is_no_word() -> None:
    # Only the counterparty counts before a glued payment word: an LLM that reads "명함카드" as item "명함"
    # must not turn 카드 into a payment word and keep its invented 현금 (self-review).
    result, applied = postprocess_candidate(_llm("문구나라", "명함", False), "문구나라에 명함카드 3통 14000원")
    assert result["credit"] is None and NO_WORD in applied


def test_glued_word_does_not_fill_an_empty_credit() -> None:
    # Rule 1 unchanged: the glued word is no basis for filling — the user is asked.
    result, applied = postprocess_candidate(_llm("푸른마트", "계란", None), "외상푸른마트에 계란 2판 14000원")
    assert result["credit"] is None and "credit_from_text" not in applied


def test_shop_name_starting_with_credit_word_is_no_word() -> None:
    # "외상" is part of the extracted name, not glued before it → still no word → asked.
    result, applied = postprocess_candidate(_llm("외상리슈퍼", "라면", True), "외상리슈퍼에 라면 2박스 14000원")
    assert result["credit"] is None and NO_WORD in applied


def test_credit_word_inside_shop_name_is_no_word() -> None:
    result, applied = postprocess_candidate(_llm("대외상사", "볼펜", True), "대외상사에 볼펜 10자루 14000원")
    assert result["credit"] is None and NO_WORD in applied


def test_payment_word_inside_shop_name_is_no_word() -> None:
    text = "현금나라마트에 쌀 1포대 14000원"
    result, applied = postprocess_candidate(_llm("현금나라마트", "쌀", False), text)
    assert result["credit"] is None and NO_WORD in applied
    # Only "외상" counts before a name: an LLM that drops the 현금 prefix does not make it a payment word.
    result, applied = postprocess_candidate(_llm("나라마트", "쌀", False), text)
    assert result["credit"] is None and NO_WORD in applied


def test_glued_credit_with_paid_cash_is_still_a_contradiction() -> None:
    # Rule 0b unchanged: the glued 외상 is not a credit side, so payment only → the LLM 외상 is asked, not kept.
    text = "외상푸른마트한테 계란 2판 14000원 현금으로 받았어"
    result, applied = postprocess_candidate(_llm("푸른마트", "계란", True), text)
    assert result["credit"] is None and "credit_cleared_contradiction" in applied


def test_glued_credit_negated_with_cash() -> None:
    text = "외상바다수산한테는 안 해요 고등어 3마리 14000원 현금으로"
    result, _ = postprocess_candidate(_llm("바다수산", "고등어", False), text)
    assert result["credit"] is False
    result, applied = postprocess_candidate(_llm("바다수산", "고등어", True), text)
    assert result["credit"] is None and "credit_cleared_contradiction" in applied


def test_negated_glued_credit_is_no_basis() -> None:
    # A negated glued word does not keep the value either ("외상 … 안 해" with no other word → asked as before).
    result, applied = postprocess_candidate(_llm("바다수산", "고등어", True), "외상바다수산한테 안 해 고등어 3마리 14000원")
    assert result["credit"] is None and NO_WORD in applied
