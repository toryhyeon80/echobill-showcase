"""Rule 5 tests (PM 10/16): the one amount said goes back as the total of a single item.

Real-device repros: the LLM put the amount in unit_price (Rule 2 moved it to the item amount, total stayed null) or
multiplied it (Rule 4 cleared amount and total), so the total was asked again. Sentences go through normalization
like the LLM input. Counterparties are made up.
"""

import pytest

from app.services.normalize_ko import normalize_korean_numerals
from app.services.postprocess import postprocess_candidate
from app.services.validation import validate_candidate

RULE = "total_restored_spoken"


def _item(qty, unit, amount, unit_price=None, name="품목") -> dict:
    return {"name": name, "qty": qty, "unit": unit, "unit_price": unit_price, "amount": amount}


def _run(utterance: str, items: list[dict], total, credit=False) -> tuple[dict, list[str]]:
    sent = normalize_korean_numerals(utterance).text
    return postprocess_candidate({"counterparty": "거래처", "items": items, "total_amount": total, "credit": credit},
                                 sent)


@pytest.mark.parametrize("utterance, qty, unit, price, credit, expected", [
    ("마바분식에 고등어 2마리 3천 5백원 현금", 2, "마리", 3500, False, 3500),
    ("가나상회 외상, 대파 세단 8천원 치", 3, "단", 8000, True, 8000),
])
@pytest.mark.parametrize("shape", ["unit_price_only", "multiplied"])
def test_repro_spoken_amount_becomes_total(utterance, qty, unit, price, credit, expected, shape) -> None:
    """실기기 재현 2문장 × LLM 모양 2가지: 총액·품목 금액이 말한 금액으로 돌아오고 바로 저장 가능 (200)."""
    if shape == "unit_price_only":
        items, total = [_item(qty, unit, None, price)], None
    else:
        items, total = [_item(qty, unit, qty * price, price)], qty * price
    result, applied = _run(utterance, items, total, credit)
    assert result["total_amount"] == expected
    assert result["items"][0]["amount"] == expected
    assert result["items"][0]["unit_price"] is None
    assert RULE in applied
    assert validate_candidate(result) is None


@pytest.mark.parametrize("utterance, qty, unit, price, credit, expected_credit", [
    # PM 운영 재현 (10/16): 새 STT가 금액을 "15,000원"처럼 숫자·쉼표로 자주 적음 — 3표기 × "에" 있음/없음
    ("가나상회 배추 세 박스 15,000원 매상", 3, "박스", 15000, True, None),
    ("가나상회에 배추 세 박스 15,000원 외상", 3, "박스", 15000, True, True),
    ("가나상회 외상, 대파 세단 8천원 치", 3, "단", 8000, True, True),
    ("가나상회에 대파 세단 8천원 치 외상", 3, "단", 8000, True, True),
    ("마바분식에 고등어 2마리 3천 5백원 현금", 2, "마리", 3500, False, False),
    ("마바분식 고등어 2마리 3,500원 현금", 2, "마리", 3500, False, False),
])
@pytest.mark.parametrize("shape", ["unit_price_only", "multiplied"])
def test_pm_repro_three_amount_spellings(utterance, qty, unit, price, credit, expected_credit, shape) -> None:
    """PM 운영 재현 3표기 (숫자·쉼표 / 한글+숫자 / 한글 숫자 띄어 씀): 총액 되살림. "매상"은 외상만 비워 되묻기."""
    if shape == "unit_price_only":
        items, total = [_item(qty, unit, None, price)], None
    else:
        items, total = [_item(qty, unit, qty * price, price)], qty * price
    result, applied = _run(utterance, items, total, credit)
    assert result["total_amount"] == price
    assert result["items"][0]["amount"] == price
    assert RULE in applied
    assert result["credit"] is expected_credit
    problem = validate_candidate(result)
    if expected_credit is None:
        assert problem.problem_fields == ["credit"]
    else:
        assert problem is None


@pytest.mark.parametrize("utterance, items, total", [
    # 단가 표현 → 계산한 금액이 정당, 그대로
    ("다라상회 사과 개당 2천원 5개 현금", [_item(5, "개", 10000, 2000)], 10000),
    ("다라상회 대파 한 단에 3천원 4단 외상", [_item(4, "단", 12000, 3000)], 12000),
    ("다라상회 사과 5개 2천원씩 현금", [_item(5, "개", 10000, 2000)], 10000),
    # 금액 2개 → 되묻기
    ("다라상회 배추 3포기 6천원 무 2개 4천원 현금",
     [_item(3, "포기", None, 6000, "배추"), _item(2, "개", None, 4000, "무")], None),
    # 부분 결제·할인·다른 천·만 숫자 → 되묻기
    ("다라상회 양파 2망 만원 받고 나머지 외상", [_item(2, "망", None, 10000)], None),
    ("다라상회 고추 2근 만 이천원 할인해서 현금", [_item(2, "근", None, 12000)], None),
    ("다라상회 마늘 한접 이만원 고추 만오천 외상", [_item(1, "접", None, 20000)], None),
    # 붙여 쓴 "8천원치"는 정규화되지 않아 금액 판단 밖 (알려진 한계, 후속 이슈)
    ("가나상회 외상, 대파 세단 8천원치", [_item(3, "단", None, 8000)], None),
])
def test_guard_sentences_keep_current_total(utterance, items, total) -> None:
    """가상 8문장: 규칙 5가 걸리지 않고 지금 결과(총액)를 그대로 둔다."""
    result, applied = _run(utterance, items, total)
    assert RULE not in applied
    assert result["total_amount"] == total


@pytest.mark.parametrize("utterance, qty, unit, price", [
    # #162 리뷰 9문장: 같은 단위 두 번 · 한 + 단위 + 금액 · 각·각각·낱개
    ("다라상회 쌀 2포대 한 포대 5만원 외상", 2, "포대", 50000),
    ("다라상회 계란 2판 한판 6천원 현금", 2, "판", 6000),
    ("다라상회 우유 3팩 한팩 2천원 외상", 3, "팩", 2000),
    ("다라상회 생선 3마리 마리 3천원 현금", 3, "마리", 3000),
    ("다라상회 수박 2통 통 만원 카드", 2, "통", 10000),
    ("다라상회 사과 5개 개 천원 현금", 5, "개", 1000),
    ("다라상회 오이 10개 낱개 5백원 현금", 10, "개", 500),
    ("다라상회 배추 3포기 각 2천원 외상", 3, "포기", 2000),
    ("다라상회 배추 3포기 각각 2천원 외상", 3, "포기", 2000),
    # 직접 만든 문장: 단가가 숨어 있는 다른 말투 (마리에·판에·박스당·근에·개별로)
    ("다라상회 고등어 3마리 마리에 3천원 현금", 3, "마리", 3000),
    ("다라상회 계란 2판 판에 6천원 외상", 2, "판", 6000),
    ("다라상회 사과 3박스 박스당 2만원 외상", 3, "박스", 20000),
    ("다라상회 고추 2근 근에 만원 현금", 2, "근", 10000),
    ("다라상회 양파 4망 개별로 3천원 외상", 4, "망", 3000),
    # 직접 만든 문장: 다른 수량과 단위가 다른 "한 + 단위" 단가
    ("다라상회 귤 2박스 한 상자 2만원 외상", 2, "박스", 20000),
    ("다라상회 맥주 2박스 한 짝 3만원 현금", 2, "박스", 30000),
    ("다라상회 생수 3묶음 한 팩 5천원 외상", 3, "묶음", 5000),
    ("다라상회 고기 3근 1kg 만오천원 카드", 3, "근", 15000),
    # 허용 모양 밖 (PM 10/16, 수량 표현이 둘 이상이거나 금액 바로 앞이 아님) — 2차 리뷰 4문장
    ("다라상회 사과 5개 한 알 천원 현금", 5, "개", 1000),
    ("다라상회 감자 3봉 봉지 2천원 외상", 3, "봉", 2000),
    ("다라상회 참외 6개 따로따로 천원 현금", 6, "개", 1000),
    ("다라상회 굴 3봉지 봉지마다 5천원 외상", 3, "봉지", 5000),
    # 허용 모양 밖 — 직접 만든 숨은 단가 말투 (마다·씩·짜리·따로·낯선 단위 알·톨·쪽·축·톳)
    ("다라상회 마늘 3접 접마다 2만원 외상", 3, "접", 20000),
    ("다라상회 두부 3모 모마다 천오백원 현금", 3, "모", 1500),
    ("다라상회 귤 4봉지 한 봉지씩 3천원 외상", 4, "봉지", 3000),
    ("다라상회 생선 2마리 마리씩 5천원 현금", 2, "마리", 5000),
    ("다라상회 수박 2통 한 통짜리 만원 카드", 2, "통", 10000),
    ("다라상회 배 4개 따로 2천원 현금", 4, "개", 2000),
    ("다라상회 메추리알 3판 한 알 백원 외상", 3, "판", 100),
    ("다라상회 밤 2킬로 한 톨 2백원 현금", 2, "킬로", 200),
    ("다라상회 마늘 2접 한 쪽 오백원 외상", 2, "접", 500),
    ("다라상회 오징어 5마리 한 축 2만원 외상", 5, "마리", 20000),
    ("다라상회 김 3톳 톳 하나 만원 외상", 3, "톳", 10000),
    ("다라상회 고등어 4마리 마리 단위로 3천원 외상", 4, "마리", 3000),
    ("다라상회 계란 3판 판 기준 6천원 외상", 3, "판", 6000),
    ("다라상회 사과 2박스 세 알 천원 현금", 2, "박스", 1000),
])
@pytest.mark.parametrize("shape", ["unit_price_only", "multiplied", "amount_only_multiplied"])
def test_hidden_unit_price_is_not_restored(utterance, qty, unit, price, shape) -> None:
    """단가가 숨어 있는 문장: 규칙 5가 걸리지 않아 한 단위 값이 총액으로 저장되지 않는다 (되묻기 또는 단가×수량)."""
    items, total = {
        "unit_price_only": ([_item(qty, unit, None, price)], None),
        "multiplied": ([_item(qty, unit, qty * price, price)], qty * price),
        "amount_only_multiplied": ([_item(qty, unit, qty * price)], qty * price),
    }[shape]
    result, applied = _run(utterance, items, total)
    assert RULE not in applied
    assert result["total_amount"] != price
    if result["total_amount"] is None:
        assert validate_candidate(result) is not None


@pytest.mark.parametrize("utterance, qty, unit, price, credit", [
    ("오늘 아침에 다라상회 대파 2단 4천원 외상", 2, "단", 4000, True),
    ("다라상회 고등어 2마리 3천 5백원 송금했어", 2, "마리", 3500, False),
    # 허용 모양 (PM 10/16): 수량 표현 하나가 "N원" 바로 앞, 또는 수량 표현 없음
    ("방금 다라상회 사과 3박스 6만원 외상", 3, "박스", 60000, True),
    ("어제 저녁에 다라상회 계란 2판 만이천원 카드", 2, "판", 12000, False),
    ("다라상회 무 2개 4천원 계좌로 보냈어", 2, "개", 4000, False),
    ("다라상회 계란 1.5판 11100원 카드", 1.5, "판", 11100, False),
    ("다라상회 쌀 20kg 6만원 외상", 20, "kg", 60000, True),
    ("다라상회 열무 다섯 단 만원 현금", 5, "단", 10000, False),
])
@pytest.mark.parametrize("shape", ["unit_price_only", "multiplied"])
def test_review_improved_sentences_save_spoken_total(utterance, qty, unit, price, credit, shape) -> None:
    """허용 모양 문장(리뷰에서 개선된 2문장 포함): 말한 금액이 총액으로 돌아와 바로 저장 (200)."""
    if shape == "unit_price_only":
        items, total = [_item(qty, unit, None, price)], None
    else:
        items, total = [_item(qty, unit, qty * price, price)], qty * price
    result, applied = _run(utterance, items, total, credit)
    assert result["total_amount"] == price
    assert result["items"][0]["amount"] == price
    assert RULE in applied
    assert validate_candidate(result) is None


def test_no_quantity_restores_total_and_asks_quantity() -> None:
    """허용 모양 — 수량 표현 없음 (g-dev-007 모양): 총액은 말한 금액, 수량만 되묻기."""
    result, applied = _run("다라상회에 배추 35000원 외상", [_item(None, None, None, 35000)], None, True)
    assert result["total_amount"] == 35000
    assert RULE in applied
    assert validate_candidate(result).problem_fields == ["items[0].qty"]


@pytest.mark.parametrize("sent, items, total, expected", [
    # g-dev-004 r3: 150000(10×15000)을 규칙 4가 지운 뒤 말한 15000을 되살림 (Golden 총액 15000)
    ("철수씨한테 양파 10킬로 15000원", [_item(10.0, "킬로", 150000, 15000)], 150000, 15000),
    # 총액이 수량과 같은 6(원) → 문장에 있는 숫자지만 6마리의 수량 — 규칙 4가 지운 뒤 18000을 되살림
    ("솔바람 고등어 6마리 18000원 외상", [_item(6, "마리", 108000, 18000)], 6, 18000),
])
def test_rule_4_cleared_total_comes_back(sent, items, total, expected) -> None:
    """규칙 4(지어낸 금액 지우기)가 지운 총액을 말한 금액으로 되살림 — 지어낸 108000·150000은 돌아오지 않는다."""
    result, applied = postprocess_candidate({"counterparty": "거래처", "items": items, "total_amount": total,
                                             "credit": True}, sent)
    assert result["total_amount"] == expected
    assert result["items"][0]["amount"] == expected
    assert applied[-2:] == ["item_amount_invented", RULE]


def test_price_for_one_is_not_restored_as_total() -> None:
    """"사과 하나 3000원 3개"의 3000은 하나 값 — 총액 3000을 넣지 않고 되묻기 그대로 (틀린 값 막기)."""
    result, applied = _run("솔바람 사과 하나 3000원 3개 외상", [_item(3, None, 9000, 3000)], 9000, True)
    assert result["total_amount"] is None
    assert RULE not in applied


def test_glued_chi_before_credit_word_is_still_asked() -> None:
    """대조: "8천원치"가 문장 앞쪽에 붙어 있어도 정규화 밖 → 총액은 되묻기 그대로."""
    result, applied = _run("가나상회 8천원치 외상, 대파 세단이야", [_item(3, "단", None, 8000)], None, True)
    assert result["total_amount"] is None
    assert RULE not in applied


def test_total_already_said_is_left_alone() -> None:
    """LLM 총액이 문장에 있는 숫자면 손대지 않는다 (품목 금액도)."""
    result, applied = _run("마바분식에 고등어 2마리 3천 5백원 현금", [_item(2, "마리", 3500)], 3500)
    assert result["total_amount"] == 3500
    assert RULE not in applied


def test_llm_without_any_amount_is_still_asked() -> None:
    """LLM이 단가·금액·총액을 하나도 내지 않았으면 되살리지 않고 지금처럼 되묻는다 (규칙이 지운 금액만 대상)."""
    result, applied = _run("마바분식에 고등어 2마리 3천 5백원 현금", [_item(2, "마리", None)], None)
    assert result["total_amount"] is None
    assert RULE not in applied


@pytest.mark.parametrize("credit", [True, False, None])
def test_maesang_misheard_credit_is_asked(credit) -> None:
    """"외상"이 "매상"으로 들림: 외상·결제 단어가 없으니 LLM 값과 상관없이 외상은 되묻기 (잘못 확정·반대 저장 없음)."""
    result, _ = _run("가나상회 배추 세박스 15,000원 매상", [_item(3, "박스", 15000)], 15000, credit)
    assert result["credit"] is None
    assert result["total_amount"] == 15000
    assert validate_candidate(result).problem_fields == ["credit"]


def test_normalization_off_skips_rule() -> None:
    """정규화 off(unit_price_rules=False)면 규칙 2~5 모두 건너뛴다."""
    result, applied = postprocess_candidate(
        {"counterparty": "거래처", "items": [_item(2, "마리", None, 3500)], "total_amount": None, "credit": False},
        "마바분식에 고등어 2마리 3천 5백원 현금", unit_price_rules=False)
    assert result["total_amount"] is None
    assert RULE not in applied
