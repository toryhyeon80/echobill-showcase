"""Rule 6 (pp-1.6, PM 10/29): a counterparty equal to an item name is cleared so "어느 집이에요?" is asked.

내부 테스트: STT가 "새롬인쇄에"를 "새로 인쇄해"로 들어 거래처 "명함" · 품목 "명함"이 확인 화면까지 갔다.
All sentences are new (not Dev·Seed).
"""

import pytest

from app.services.postprocess import postprocess_candidate

RULE = "counterparty_is_item_cleared"


def _llm(counterparty: str | None, *names: str, credit: bool | None = True) -> dict:
    return {
        "counterparty": counterparty,
        "items": [{"name": name, "qty": 500, "unit": "장", "unit_price": None, "amount": 35000} for name in names],
        "total_amount": 35000,
        "credit": credit,
    }


def test_counterparty_same_as_item_is_cleared() -> None:
    text = "새로 인쇄해 명함 500장 35000원 외상"
    result, applied = postprocess_candidate(_llm("명함", "명함"), text)
    assert result["counterparty"] is None and RULE in applied
    # nothing else is touched: the other fields stay as the LLM gave them
    assert result["items"][0]["name"] == "명함" and result["total_amount"] == 35000 and result["credit"] is True


def test_match_ignores_spaces() -> None:
    result, applied = postprocess_candidate(_llm("명 함", "명함"), "명함 500장 35000원 외상")
    assert result["counterparty"] is None and RULE in applied


def test_match_with_any_of_several_items() -> None:
    result, applied = postprocess_candidate(_llm("전단지", "명함", "전단지"), "전단지 명함 외상")
    assert result["counterparty"] is None and RULE in applied


def test_real_counterparty_is_kept() -> None:
    result, applied = postprocess_candidate(_llm("새롬인쇄", "명함"), "새롬인쇄에 명함 500장 35000원 외상")
    assert result["counterparty"] == "새롬인쇄" and RULE not in applied


@pytest.mark.parametrize("counterparty", ["명함집", "명함인쇄", "함"])
def test_partial_match_is_kept(counterparty: str) -> None:
    result, applied = postprocess_candidate(_llm(counterparty, "명함"), f"{counterparty}에 명함 500장 35000원 외상")
    assert result["counterparty"] == counterparty and RULE not in applied


@pytest.mark.parametrize("counterparty", [None, ""])
def test_no_counterparty_is_untouched(counterparty: str | None) -> None:
    result, applied = postprocess_candidate(_llm(counterparty, "명함"), "명함 500장 35000원 외상")
    assert result["counterparty"] == counterparty and RULE not in applied


def test_item_without_name_is_ignored() -> None:
    candidate = _llm("명함", "명함")
    candidate["items"][0]["name"] = None
    result, applied = postprocess_candidate(candidate, "명함 500장 35000원 외상")
    assert result["counterparty"] == "명함" and RULE not in applied


def test_runs_with_normalization_off() -> None:
    # Looks only at the candidate, so unit_price_rules=False (Rules 2–5 off) does not skip it.
    result, applied = postprocess_candidate(_llm("명함", "명함"), "명함 500장 35000원 외상", unit_price_rules=False)
    assert result["counterparty"] is None and RULE in applied


def test_input_is_not_modified() -> None:
    candidate = _llm("명함", "명함")
    postprocess_candidate(candidate, "명함 500장 35000원 외상")
    assert candidate["counterparty"] == "명함"
