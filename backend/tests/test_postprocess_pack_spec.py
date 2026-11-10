"""Pack-size rule (PM 10/13, AI 개선 4-2): a qty read from the pack size "N개입" is cleared with the amounts computed from it.

g-dev-057 / g-dev-049 are development Golden sentences (Dev may be used for improvement, CLAUDE.md §4-11).
"""

import pytest

from app.services.postprocess import postprocess_candidate
from app.services.validation import validate_candidate

RULE = "pack_spec_qty_cleared"
# g-dev-057: the LLM read 20 packs in all three runs (193115 p0-on r1·r2·r3) and saved 90000 with HTTP 200
DEV_057 = "떡집사랑에 인절미 20개입 팩당 4500원으로 납품했어 몇 팩 보냈는지는 못 적었고 외상으로 남겨줘"
DEV_049 = "약수슈퍼에 생수 500ml 24개입 3박스 납품했고 박스당 7200원 총액 21600원 카드로 받았어"


def _llm(qty, unit_price, amount, total, *, name="인절미", unit="개") -> dict:
    return {
        "counterparty": "떡집사랑",
        "items": [{"name": name, "qty": qty, "unit": unit, "unit_price": unit_price, "amount": amount}],
        "total_amount": total,
        "credit": True,
    }


def test_dev_057_pack_size_qty_and_computed_amounts_are_cleared() -> None:
    result, applied = postprocess_candidate(_llm(20.0, 4500, 90000, 90000), DEV_057)
    assert RULE in applied
    item = result["items"][0]
    assert item["qty"] is None and item["amount"] is None and result["total_amount"] is None
    assert item["unit_price"] == 4500  # the stated unit price stays
    failure = validate_candidate(result)
    assert failure is not None and failure.error_code == "MISSING_REQUIRED_FIELD"
    assert set(failure.problem_fields) == {"items[0].qty", "total_amount"}  # = g-dev-057 required_clarifications


def test_amounts_said_in_the_text_stay() -> None:
    text = "떡집사랑에 인절미 20개입 팩당 4500원 오늘 90000원어치 외상"
    result, applied = postprocess_candidate(_llm(20, 4500, 90000, 90000), text)
    assert RULE in applied
    assert result["items"][0]["qty"] is None
    assert result["items"][0]["amount"] == 90000 and result["total_amount"] == 90000


@pytest.mark.parametrize("sent_text, qty, unit_price, amount, total", [
    (DEV_049, 3, 7200, 21600, 21600),                                   # g-dev-049: "3박스" is the qty
    (DEV_049, 24, 7200, 172800, 172800),                                # LLM 24 — another qty is said, left alone
    ("떡집사랑에 인절미 20개입 3팩 9000원 외상", 3, 3000, 9000, 9000),
    ("떡집사랑에 인절미 20개입 3팩 9000원 외상", 20, 450, 9000, 9000),   # LLM 20 with "3팩" — not this rule's call
    ("떡집사랑에 인절미 20개입 20팩 팩당 4500원 외상", 20, 4500, 90000, 90000),  # 20 also said as packs
    ("떡집사랑에 인절미 20개입 팩당 4500원 외상", 5, 4500, 22500, 22500),  # qty is not the pack size
    ("떡집사랑에 인절미 팩당 4500원 외상", 20, 4500, 90000, 90000),        # no "N개입" at all
])
def test_other_quantities_keep_the_llm_qty(sent_text, qty, unit_price, amount, total) -> None:
    """Only this rule's effect is checked — Rules 2·3 may still act on the same candidate."""
    result, applied = postprocess_candidate(_llm(qty, unit_price, amount, total), sent_text)
    assert RULE not in applied
    assert result["items"][0]["qty"] == qty


def test_computed_total_is_cleared_after_rule_2_dropped_the_unit_price() -> None:
    """No unit-price marker Rule 2 knows ("박스에 12000원") → Rule 2 clears unit_price; the pack-size rule still uses the LLM's."""
    text = "솔밭식당 두유 16개입 박스에 12000원 외상"
    result, applied = postprocess_candidate(_llm(16, 12000, 192000, 192000, name="두유"), text)
    assert {"unit_price_cleared", RULE} <= set(applied)
    assert result["items"][0]["qty"] is None and result["items"][0]["amount"] is None
    assert result["total_amount"] is None


def test_stated_total_is_left_to_rule_3() -> None:
    text = "떡집사랑에 인절미 20개입 팩당 4500원 전부 9000원 외상"
    result, applied = postprocess_candidate(_llm(20, 4500, 90000, 90000), text)
    assert applied[-2:] == [RULE, "total_restored_stated"]
    assert result["items"][0]["qty"] is None and result["items"][0]["amount"] is None
    assert result["total_amount"] == 9000


def test_pack_size_alone_clears_qty() -> None:
    result, applied = postprocess_candidate(_llm(20, None, None, 9000), "떡집사랑에 인절미 20개입 9000원 외상")
    assert RULE in applied
    assert result["items"][0]["qty"] is None
    assert result["total_amount"] == 9000  # said in the text, not computed from the qty


def test_normalization_off_skips_the_rule() -> None:
    """Digits only (like Rules 2·3): with KO_NUMERAL_NORMALIZE off the live path passes unit_price_rules=False."""
    candidate = _llm(20, 4500, 90000, 90000)
    for text in (DEV_057, "떡집사랑에 인절미 이십개입 팩당 사천오백원 외상"):
        result, applied = postprocess_candidate(candidate, text, unit_price_rules=False)
        assert RULE not in applied
        assert result["items"] == candidate["items"] and result["total_amount"] == 90000
