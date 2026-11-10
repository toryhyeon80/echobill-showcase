"""Rule 4 tests (PM 10/13): an item amount the text never says, without a unit-price marker, is cleared with the total.

Sentences are the LLM input (normalized). Golden Dev / Seed v2 rows are reproductions PM asked for (10/13 —
g-dev-013 r2, seed2-129 r2) or guards that the rule must not break (단가 표지·한글 수사).
"""

from decimal import Decimal

import pytest

from app.services.normalize_ko import normalize_korean_numerals, spoken_numbers
from app.services.postprocess import postprocess_candidate
from app.services.validation import validate_candidate

RULE = "item_amount_invented"


def _item(name: str, qty, unit, amount, unit_price=None) -> dict:
    return {"name": name, "qty": qty, "unit": unit, "unit_price": unit_price, "amount": amount}


def _candidate(items: list[dict], total, credit=True) -> dict:
    return {"counterparty": "거래처", "items": items, "total_amount": total, "credit": credit}


def test_g_dev_013_r2_invented_amount_and_total_are_cleared() -> None:
    """g-dev-013 r2 재현: 과자 5개 5000원 → AI 25000 (5×5000), 총액 37000 → 과자 금액·총액 null (되묻기)."""
    sent = "대박마트 콜라 1박스 12000원에 과자 5개 5000원 외상"
    raw = _candidate([_item("콜라", 1, "박스", 12000, 12000), _item("과자", 5, None, 25000, 5000)], 37000)
    result, applied = postprocess_candidate(raw, sent)
    assert [i["amount"] for i in result["items"]] == [12000, None]
    assert result["total_amount"] is None
    assert RULE in applied
    assert raw["total_amount"] == 37000  # input untouched


def test_g_dev_013_r1_amounts_said_and_total_their_sum_are_kept() -> None:
    """같은 문장 r1: 금액이 모두 문장에 있고 총액이 그 합 → 그대로 (규칙 2가 단가만 지움)."""
    sent = "대박마트 콜라 1박스 12000원에 과자 5개 5000원 외상"
    raw = _candidate([_item("콜라", 1, "박스", 12000, 12000), _item("과자", 5, None, 5000, 5000)], 17000)
    result, applied = postprocess_candidate(raw, sent)
    assert [i["amount"] for i in result["items"]] == [12000, 5000]
    assert result["total_amount"] == 17000
    assert RULE not in applied


def test_seed2_129_r2_discount_rounded_amount_is_cleared() -> None:
    """seed2-129 r2 재현: "원래 3만9300원인데 좀 깎아 줬어" → AI 39000은 문장에 없음 → null (되묻기). 할인어가 있어도 적용."""
    sent = "알찬마트에 삼겹살 3근 원래 3만9300원인데 좀 깎아 줬어, 달아줘"
    result, applied = postprocess_candidate(_candidate([_item("삼겹살", 3, "근", 39000)], 39000), sent)
    assert result["items"][0]["amount"] is None
    assert result["total_amount"] is None
    assert RULE in applied


@pytest.mark.parametrize("sent, items, total", [
    # g-dev-024: 앞에 수량 없는 "킬로에 9500원"은 단가 표지 → 2.5×9500 계산은 정당
    ("한식집에 돼지앞다리 2.5킬로 킬로에 9500원 미수", [_item("돼지앞다리", 2.5, "킬로", 23750, 9500)], 23750),
    # g-dev-037: 한글 수사 앞 "킬로당 만이천원"(정규화 안 됨)도 단가 표지
    ("한식당 돼지고기 3킬로 킬로당 만이천원인데 총액은 50000원으로 외상", [_item("돼지고기", 3, "킬로", 36000, 12000)], 50000),
])
def test_unit_price_marker_keeps_computed_amount(sent, items, total) -> None:
    result, applied = postprocess_candidate(_candidate(items, total), sent)
    assert result["items"][0]["amount"] == items[0]["amount"]
    assert result["total_amount"] == total
    assert RULE not in applied


@pytest.mark.parametrize("sent, amounts, total", [
    ("외상 라온마트 마늘 90접 207만", [2070000], 2070000),                    # seed2-016
    ("튀김가루 2만1600 보람상회 외상으로", [21600], None),                     # seed2-099
    ("철수씨 가게 마늘 1접 20000원이요 고추 햇반접은 만오천 합치면 삼만오천 달아요", [20000, 15000], 35000),  # g-dev-026
])
def test_korean_numerals_not_normalized_still_count_as_said(sent, amounts, total) -> None:
    """정규화기가 바꾸지 않은 "207만"·"2만1600"·"만오천"·"삼만오천"도 문장 숫자 — 맞던 금액을 지우지 않음."""
    items = [_item(f"품목{n}", 1, None, a) for n, a in enumerate(amounts)]
    result, applied = postprocess_candidate(_candidate(items, total), sent)
    assert [i["amount"] for i in result["items"]] == amounts
    assert result["total_amount"] == total
    assert RULE not in applied


def test_new_sentence_invented_amount_is_cleared() -> None:
    """Dev 밖 새 문장: 양파 3망 4000원 → AI 12000 (3×4000) 지움 → 규칙 5(PM 10/16)가 말한 4000을 되살림."""
    result, applied = postprocess_candidate(_candidate([_item("양파", 3, "망", 12000, 4000)], 12000),
                                            "솔바람 양파 3망 4000원 외상")
    assert result["items"][0]["amount"] == 4000
    assert result["total_amount"] == 4000
    assert applied.count(RULE) == 1
    assert "total_restored_spoken" in applied


def test_spaced_small_places_amount_is_said_not_invented() -> None:
    """#109: "3천 5백원" was normalized to "3천 500원", so a correct AI 3500 looked invented and was cleared."""
    sent = normalize_korean_numerals("고등어 2마리 3천 5백원 현금").text
    result, applied = postprocess_candidate(_candidate([_item("고등어", 2, "마리", 3500)], 3500, credit=False), sent)
    assert result["items"][0]["amount"] == 3500
    assert result["total_amount"] == 3500
    assert RULE not in applied


def test_spaced_group_before_man_amount_is_said_not_invented() -> None:
    """#109 2/2: "3천 5백만원" was normalized to "3천 5000000원", so a correct AI 35000000 looked invented."""
    sent = normalize_korean_numerals("한우상회 소 2마리 3천 5백만원 외상").text
    result, applied = postprocess_candidate(_candidate([_item("소", 2, "마리", 35000000)], 35000000), sent)
    assert result["items"][0]["amount"] == 35000000
    assert result["total_amount"] == 35000000
    assert RULE not in applied


def test_unit_price_marker_won_ssik_keeps_computed_amount() -> None:
    """기존 표지 "4000원씩"이 있으면 3×4000=12000은 정당한 계산."""
    result, applied = postprocess_candidate(_candidate([_item("양파", 3, "망", 12000, 4000)], 12000),
                                            "솔바람 양파 3망 4000원씩 외상")
    assert result["items"][0]["amount"] == 12000
    assert result["total_amount"] == 12000
    assert RULE not in applied


def test_normalization_off_skips_rule() -> None:
    """정규화 off(unit_price_rules=False)에서는 규칙 2·3처럼 적용하지 않는다."""
    result, applied = postprocess_candidate(_candidate([_item("양파", 3, "망", 12000)], 12000),
                                            "솔바람 양파 3망 4000원 외상", unit_price_rules=False)
    assert result["items"][0]["amount"] == 12000
    assert RULE not in applied


@pytest.mark.parametrize("text, expected", [
    ("마늘 90접 207만", {Decimal(90), Decimal(2070000)}),
    ("튀김가루 2만1600", {Decimal(21600)}),
    ("고추 만오천 합치면 삼만오천", {Decimal(15000), Decimal(35000)}),
    ("과자 다섯 개", {Decimal(5)}),
    # #109: spaced chunks are read as one amount too (as normalization joins them)
    ("고등어 삼천 오백", {Decimal(3500)}),
    ("배추 1만 2천 5백", {Decimal(12500)}),
    ("식용유 삼만 오천", {Decimal(35000)}),
])
def test_spoken_numbers_reads_korean_numerals(text, expected) -> None:
    assert expected <= spoken_numbers(text)


# PM 10/13 후속: the total stays only when every item amount was invented and the total is a said number that is
# no item qty. Otherwise (an amount said or never given, total = a qty) the total is cleared as before.
def _status(candidate: dict) -> int:
    failure = validate_candidate(candidate)
    return 200 if failure is None else 400


def test_seed2_012_all_invented_said_total_is_kept() -> None:
    """seed2-012 재현: 갈치 7마리 → AI 14000(7×2000)은 지어냄, 총액 94500은 문장 숫자 → 총액 유지, 200."""
    sent = "외상으로 꽃마을분식한테 갈치 7마리 2000원 할인해서 94500원"
    raw = _candidate([_item("갈치", 7.0, "마리", 14000, 2000)], 94500)
    result, applied = postprocess_candidate(raw, sent)
    assert result["items"][0]["amount"] is None
    assert result["total_amount"] == 94500
    assert RULE in applied
    assert _status(result) == 200


def test_bachu_apple_stated_total_is_kept_and_saved() -> None:
    """"배추 3박스 사과 2박스 전부 오만원" — 품목 금액 3만·2만을 지어냄 → 총액 50000으로 200."""
    sent = "솔바람 배추 3박스 사과 2박스 전부 50000원 외상"
    raw = _candidate([_item("배추", 3, "박스", 30000), _item("사과", 2, "박스", 20000)], 50000)
    result, applied = postprocess_candidate(raw, sent)
    assert [i["amount"] for i in result["items"]] == [None, None]
    assert result["total_amount"] == 50000
    assert RULE in applied
    assert _status(result) == 200


@pytest.mark.parametrize("sent, items, total", [
    # seed2-041 재현: 키친타월 7300원이 남음 → 총액도 지움
    ("너른들농산한테 우유 2팩 5800원, 키친타월 1묶음 7300원, 합쳐서 13100원 외상값으로 달아줘",
     [_item("우유", 2.0, "팩", 11600, 5800), _item("키친타월", 1.0, "묶음", 7300, 7300)], 13100),
    # AI가 금액을 null로 낸 품목이 섞임 → 6000이 양파 값인지 총액인지 모름 → 총액도 지움
    ("솔바람 양파 3망 6000원 대파 2단 외상", [_item("양파", 3, "망", 18000, 6000), _item("대파", 2, "단", None)], 6000),
    # 총액이 수량과 같음 ("6마리" → 6원)·품목 1개는 규칙 5가 18000을 되살림 → test_postprocess_spoken_total
])
def test_total_is_cleared_unless_every_amount_invented_and_said_total(sent, items, total) -> None:
    result, applied = postprocess_candidate(_candidate(items, total), sent)
    assert result["total_amount"] is None
    assert RULE in applied
    assert _status(result) == 400


@pytest.mark.parametrize("sent, items, total, amounts_out", [
    # g-dev-013 r2: 총액 37000은 문장에 없음 → 지금처럼 지움
    ("대박마트 콜라 1박스 12000원에 과자 5개 5000원 외상",
     [_item("콜라", 1.0, "박스", 12000, 12000), _item("과자", 5.0, None, 25000, 5000)], 37000, [12000, None]),
    # g-dev-004 r3 (150000 = 10×15000)은 규칙 5가 15000을 되살림 → test_postprocess_spoken_total
])
def test_unsaid_total_is_still_cleared(sent, items, total, amounts_out) -> None:
    """main(#101)과 같은 결과 — 변화 없음."""
    result, applied = postprocess_candidate(_candidate(items, total), sent)
    assert [i["amount"] for i in result["items"]] == amounts_out
    assert result["total_amount"] is None
    assert RULE in applied
