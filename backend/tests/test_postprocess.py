"""Amount/credit Rule post-processing tests (A-4c 1-1). New sentences only — no Golden/Seed text."""

import copy

import pytest

from app.services.postprocess import (
    POSTPROCESS_VERSION,
    says_negation,
    says_credit,
    says_negated_credit,
    says_negated_payment,
    says_payment,
    postprocess_active,
    postprocess_candidate,
    postprocess_label,
    unit_price_rules_active,
)


def _candidate(*, credit=None, unit_price=None, amount=None) -> dict:
    return {
        "counterparty": "솔바람",
        "items": [{"name": "품목", "qty": 2, "unit": None, "unit_price": unit_price, "amount": amount}],
        "total_amount": None,
        "credit": credit,
    }


@pytest.mark.parametrize("sent_text, credit_in, credit_out", [
    ("솔바람 양파 2망 9000원 달아놔", None, True),
    ("솔바람 양파 2망 9000원 카드", None, False),
    # both → leave for the user. pp-1.3.1: was "외상 아니다 현금", but "아니다" now negates 외상 (PM 10/8),
    # so that sentence has only a payment word — this case needs both words un-negated
    ("솔바람 양파 2망 9000원 외상 반 현금 반", None, None),
    ("솔바람 양파 2망 9000원 달아놔", False, None),            # pp-1.4: opposite of the only word → ask, never flip
])
def test_credit_is_filled_only_when_null_and_unambiguous(sent_text, credit_in, credit_out) -> None:
    result, _ = postprocess_candidate(_candidate(credit=credit_in), sent_text)
    assert result["credit"] is credit_out


@pytest.mark.parametrize("sent_text, unit_price_in, amount_in, unit_price_out, amount_out", [
    ("솔바람 대파 6000원", 6000, None, None, 6000),            # no marker → whole-item amount
    ("솔바람 대파 2단 12000원", 6000, 12000, None, 12000),
    ("솔바람 대파 2단 단당 3000원", 3000, None, 3000, None),    # "단당 3000" marker
    ("솔바람 당근 2봉 3000원짜리", 3000, None, 3000, None),     # "원짜리" marker
    ("솔바람 당근 2봉 6000원", 3000, 6000, None, 6000),        # "당근" is not a "~당 N" marker
    ("솔바람 김말이 1봉지에 4500원", 4500, None, 4500, None),   # quantity 1 + "에" marker
])
def test_unit_price_is_cleared_only_without_any_marker(sent_text, unit_price_in, amount_in, unit_price_out, amount_out) -> None:
    result, _ = postprocess_candidate(_candidate(unit_price=unit_price_in, amount=amount_in), sent_text)
    assert result["items"][0]["unit_price"] == unit_price_out
    assert result["items"][0]["amount"] == amount_out


def test_input_is_not_mutated_and_applied_rules_are_listed() -> None:
    candidate = _candidate(credit=None, unit_price=6000, amount=None)
    before = copy.deepcopy(candidate)

    result, applied = postprocess_candidate(candidate, "솔바람 대파 6000원 달아놔")

    assert candidate == before
    assert result is not candidate
    assert result["credit"] is True and result["items"][0]["amount"] == 6000
    # 규칙 5 (PM 10/16): 말한 금액 하나 → 총액
    assert result["total_amount"] == 6000
    assert applied == ["credit_from_text", "unit_price_to_amount", "unit_price_cleared", "total_restored_spoken"]


def test_nothing_applied_when_nothing_changes() -> None:
    candidate = {**_candidate(credit=True, unit_price=None, amount=6000), "total_amount": 6000}
    result, applied = postprocess_candidate(candidate, "솔바람 대파 6000원 외상")
    assert applied == []
    assert result == candidate


def test_version_is_set() -> None:
    assert POSTPROCESS_VERSION.startswith("pp-")


# pp-1.1: payment/credit words count only as whole words (item names such as "카드지갑" do not)
@pytest.mark.parametrize("sent_text, payment, credit", [
    ("솔바람 카드지갑 2개 9000원", False, False),
    ("솔바람 현금영수증 용지 3롤 6000원", False, False),
    ("양파 2망 9000원 카드로", True, False),
    ("양파 카드결제", True, False),
    ("양파 2망 9000원 현금이요", True, False),
    ("외상으로 해줘", False, True),
    ("외상값 달아", False, True),
    ("미수로 올려", False, True),
])
def test_payment_and_credit_words_are_matched_as_words(sent_text, payment, credit) -> None:
    assert says_payment(sent_text) is payment
    assert says_credit(sent_text) is credit


def test_item_name_with_a_payment_word_keeps_credit_null() -> None:
    """pp-1.1: "카드지갑" must not turn a missing payment method into credit=false (MISSING stays)."""
    result, applied = postprocess_candidate(_candidate(credit=None), "솔바람 카드지갑 2개 9000원")
    assert result["credit"] is None
    assert "credit_from_text" not in applied


@pytest.mark.parametrize("sent_text, unit_price_out", [
    ("생수 3박스 박스 당 7200원", 7200),   # spaced "박스 당 7200" is a unit-price marker (pp-1.1)
    ("당근 2봉 6000원", None),             # still no marker
])
def test_spaced_unit_price_marker(sent_text, unit_price_out) -> None:
    result, _ = postprocess_candidate(_candidate(unit_price=7200, amount=21600), sent_text)
    assert result["items"][0]["unit_price"] == unit_price_out


@pytest.mark.parametrize(
    ("llm_postprocess", "ko_numeral_normalize", "active", "unit_price", "label"),
    [(True, True, True, True, "pp-1.6"), (True, False, True, False, "pp-1.6-credit"),
     (False, True, False, False, None), (False, False, False, False, None)],
)
def test_credit_rules_do_not_need_normalization(llm_postprocess, ko_numeral_normalize, active, unit_price, label) -> None:
    """PM #35: the unit-price regexes assume digits → normalization needed. PM 10/5: credit rules look only at words."""
    assert postprocess_active(llm_postprocess, ko_numeral_normalize) is active
    assert unit_price_rules_active(llm_postprocess, ko_numeral_normalize) is unit_price
    assert postprocess_label(llm_postprocess, ko_numeral_normalize) == label


# pp-1.2: "이체" is an immediate payment word (PM 승인 10/2), with the same word-boundary rule
@pytest.mark.parametrize("sent_text, credit_in, credit_out", [
    ("솔바람 양파 2망 9000원 이체로", None, False),
    ("솔바람 양파 2망 9000원 계좌이체", None, False),
    ("솔바람 양파 2망 9000원 이체했어 외상", None, None),  # both sides → leave null for the user
    ("솔바람 양파 2망 9000원 이체로", True, None),         # pp-1.4: opposite of the only word → ask
    ("솔바람 양파 2망 9000원 이체로", False, False),       # agreeing LLM value stays
])
def test_transfer_is_a_payment_word(sent_text, credit_in, credit_out) -> None:
    result, _ = postprocess_candidate(_candidate(credit=credit_in), sent_text)
    assert result["credit"] is credit_out


def test_version_is_pp_1_6() -> None:
    assert POSTPROCESS_VERSION == "pp-1.6"


def test_every_label_fits_the_ledger_column() -> None:
    """#71 stores the label in ledger_entries.postprocess_version (String(16)); PostgreSQL rejects longer values."""
    from app.models.ledger import LedgerEntry

    column_length = LedgerEntry.__table__.c.postprocess_version.type.length
    assert column_length == 16
    for flags in ((True, True), (True, False)):
        assert len(postprocess_label(*flags)) <= column_length


# pp-1.3 (D04-6): no payment/credit word → credit=null whatever the LLM said, so the user is asked
@pytest.mark.parametrize("sent_text, credit_in", [
    ("솔바람 대파 3단 9000원", False),           # the LLM's usual invented 현금
    ("솔바람 대파 3단 9000원", True),
    ("솔바람 카드지갑 2개 9000원", False),       # a word inside an item name does not count (pp-1.1)
    ("솔바람 대파 3단 9000원 다음 달에 받기로", True),  # credit without a word → asked (PM accepted)
])
def test_credit_is_cleared_without_any_payment_or_credit_word(sent_text, credit_in) -> None:
    result, applied = postprocess_candidate(_candidate(credit=credit_in), sent_text)
    assert result["credit"] is None
    assert applied == ["credit_cleared_no_word"]


@pytest.mark.parametrize("sent_text, credit_in", [
    ("솔바람 대파 3단 9000원 현금", False),      # LLM agrees with the only word → kept
    ("솔바람 대파 3단 9000원 달아놔", True),
    ("솔바람 대파 3단 9000원 외상 반 현금 반", False),  # both sides → LLM value kept (PM 10/5)
    ("솔바람 대파 3단 9000원 외상 반 현금 반", True),   # (not "외상 아니다 현금": 아니다 negates 외상, pp-1.3.1)
])
def test_credit_is_kept_when_any_word_is_present(sent_text, credit_in) -> None:
    result, applied = postprocess_candidate(_candidate(credit=credit_in), sent_text)
    assert result["credit"] is credit_in
    assert "credit_cleared_no_word" not in applied


def test_already_null_credit_without_words_is_not_recorded() -> None:
    result, applied = postprocess_candidate(_candidate(credit=None), "솔바람 대파 3단 9000원")
    assert result["credit"] is None
    assert applied == []


# pp-1.3: "현찰" is an immediate payment word (PM 10/2)
@pytest.mark.parametrize("sent_text, credit_in, credit_out", [
    ("솔바람 대파 3단 9000원 현찰로", None, False),
    ("솔바람 대파 3단 9000원 현찰", None, False),
    ("솔바람 대파 3단 9000원 현찰로", False, False),  # not cleared: a payment word is present
])
def test_cash_slang_is_a_payment_word(sent_text, credit_in, credit_out) -> None:
    result, _ = postprocess_candidate(_candidate(credit=credit_in), sent_text)
    assert result["credit"] is credit_out


# "송금" is an immediate payment word like 이체 (PM #44 코멘트), with the same word-boundary rule
@pytest.mark.parametrize("sent_text", ["새벽상회 무 4개 8천원 송금했어", "새벽상회 무 4개 8천원 송금으로"])
def test_remittance_is_a_payment_word(sent_text) -> None:
    result, applied = postprocess_candidate(_candidate(credit=None), sent_text)
    assert result["credit"] is False
    assert applied == ["credit_from_text"]


# pp-1.4 (PM 10/5): words of only one side and the LLM chose the other → null (ask), never flipped
@pytest.mark.parametrize("sent_text, credit_in", [
    ("솔바람 대파 3단 9000원 계좌로 받았어", True),   # payment word only, LLM says 외상
    ("솔바람 대파 3단 9000원 현찰로", True),
    ("솔바람 대파 3단 9000원 외상으로", False),       # credit word only, LLM says paid
    ("솔바람 대파 3단 9000원 미수로 올려", False),
])
def test_credit_contradicting_the_only_word_is_cleared(sent_text, credit_in) -> None:
    result, applied = postprocess_candidate(_candidate(credit=credit_in), sent_text)
    assert result["credit"] is None
    assert applied == ["credit_cleared_contradiction"]


@pytest.mark.parametrize("sent_text, credit_in, credit_out, applied_out", [
    # PM 10/8: negated 외상 + payment word → only the payment word counts → LLM 외상 is the opposite → ask
    ("솔바람 대파 3단 9000원 외상 아니고 현금", True, None, ["credit_cleared_contradiction"]),
    ("솔바람 대파 3단 9000원 외상 아니고 현금", False, False, []),
    ("솔바람 대파 3단 9000원 현금 아니고 외상", False, None, ["credit_cleared_contradiction"]),
    ("솔바람 대파 3단 9000원 계좌로 받았어", False, False, []),              # same side → kept
])
def test_contradiction_counts_only_un_negated_words(sent_text, credit_in, credit_out, applied_out) -> None:
    result, applied = postprocess_candidate(_candidate(credit=credit_in), sent_text)
    assert result["credit"] is credit_out
    assert applied == applied_out


# pp-1.4 (PM 10/8): "paid" phrases are payment words, so Rule 0 no longer clears credit
@pytest.mark.parametrize("sent_text", [
    "솔바람 대파 3단 9000원 받았어",
    "솔바람 대파 3단 9000원 받았고 영수증 줬어",
    "솔바람 대파 3단 9000원 받음",
    "솔바람 대파 3단 9000원 돈 받음",
    "솔바람 대파 3단 9000원 돈받음",
    "솔바람 대파 3단 9000원 송금했어",
    "솔바람 대파 3단 9000원 결제 완료",
    "솔바람 대파 3단 9000원 결제했어",
    "솔바람 대파 3단 9000원 계산 끝",
    "솔바람 대파 3단 9000원 계산했어",
])
def test_paid_phrase_is_a_payment_word(sent_text) -> None:
    assert says_payment(sent_text) is True
    assert says_credit(sent_text) is False
    kept, applied = postprocess_candidate(_candidate(credit=False), sent_text)
    assert kept["credit"] is False and applied == []                       # Rule 0 keeps it
    filled, applied = postprocess_candidate(_candidate(credit=None), sent_text)
    assert filled["credit"] is False and applied == ["credit_from_text"]
    asked, applied = postprocess_candidate(_candidate(credit=True), sent_text)
    assert asked["credit"] is None and applied == ["credit_cleared_contradiction"]  # Rule 0b


# PM 10/8 결제 완료 정리: future forms are asked (never filled either way), clear past forms are 현금
@pytest.mark.parametrize("sent_text, out_null, out_false, out_true", [
    ("솔바람 대파 3단 9000원 다음 주에 받음", None, None, None),      # PM 예문
    ("솔바람 대파 3단 9000원 월말에 받기로", None, None, None),       # PM 예문
    ("솔바람 대파 3단 9000원 내일 준대", None, None, None),          # PM 예문
    ("솔바람 대파 3단 9000원 월말에 준다고", None, None, None),
    ("솔바람 대파 3단 9000원 돈 받음", False, False, None),           # PM 예문 — 현금
    ("솔바람 대파 3단 9000원 돈 줬어", False, False, None),           # PM 예문
    ("솔바람 대파 3단 9000원 입금 들어왔어", False, False, None),      # PM 예문
    ("솔바람 대파 3단 9000원 계산 끝났어", False, False, None),        # PM 예문
    ("솔바람 대파 3단 9000원 9000원 받았습니다", False, False, None),
    ("솔바람 대파 3단 9000원 9000원 들어왔어요", False, False, None),
    ("솔바람 대파 3단 9000원 결제 끝났어", False, False, None),
    ("솔바람 대파 3단 9000원 계산 완료", False, False, None),
    ("솔바람에 배추 3포기 줬어", None, None, None),                  # PM 예문 — 물건을 준 것
    ("솔바람 대파 3단 9000원 줬어", None, None, None),               # 금액 뒤 "줬어"는 결제 방식 없음으로 둠
    ("솔바람 대파 3단 9000원 배달 끝났어", None, None, None),          # "끝났어"는 계산·결제 뒤만
    ("솔바람 대파 3단 9000원 배달 완료", None, None, None),           # "완료"도 계산·결제 뒤만
])
def test_paid_phrase_pm_cleanup(sent_text, out_null, out_false, out_true) -> None:
    for credit_in, expected in ((None, out_null), (False, out_false), (True, out_true)):
        result, _ = postprocess_candidate(_candidate(credit=credit_in), sent_text)
        assert result["credit"] is expected, credit_in


# PM #82 2차 리뷰 (10/12): 돈 only as its own word, an amount only as numeral + 원 (LLM null)
@pytest.mark.parametrize("sent_text, credit_out", [
    ("솔바람 배추 3박스 용돈 줬어", None),             # PM 예문 — 낱말 속 "돈"
    ("솔바람 배추 3박스 잔돈 줬어", None),             # PM 예문
    ("솔바람 배추 3 받았어", None),                   # PM 예문 — 단위 없는 숫자
    ("솔바람 배추 3박스 받았어", None),
    ("솔바람 배추 3박스 직원 받았어", None),            # 숫자 아닌 말 뒤 "원"
    ("솔바람 배추 3박스 돈 줬어", False),
    ("솔바람 배추 3박스 돈 받음", False),
    ("솔바람 배추 3박스 3만원 받았어", False),
    ("솔바람 배추 3박스 삼만 원 받았어", False),
    ("솔바람 배추 3박스 30000원 받았어", False),
])
def test_money_and_won_boundaries(sent_text, credit_out) -> None:
    result, _ = postprocess_candidate(_candidate(credit=None), sent_text)
    assert result["credit"] is credit_out


# PM #82 리뷰 (10/12): "아직·안/못 받" → a payment word does not make it 현금; 외상(true) is left alone
@pytest.mark.parametrize("sent_text", [
    "솔바람 배추 3박스 3만원 현금 아직 못 받았어",                 # PM 예문
    "솔바람 배추 3박스 3만원 현금 아직 안 받았어",
    "솔바람 배추 3박스 3만원 계좌로 아직 못받음",
    "솔바람 배추 3박스 3만원 카드는 아직이요",
    "솔바람 배추 3박스 3만원 현금 못 받았어",
])
def test_not_received_is_never_cash(sent_text) -> None:
    for credit_in in (None, False):
        result, _ = postprocess_candidate(_candidate(credit=credit_in), sent_text)
        assert result["credit"] is None, credit_in
    _, applied = postprocess_candidate(_candidate(credit=False), sent_text)
    assert applied == ["credit_cleared_not_received"]


@pytest.mark.parametrize("sent_text, credit_in, credit_out, applied_out", [
    ("솔바람 배추 3박스 3만원 현금 아직 못 받았어", True, True, []),     # PM 10/12 최종: 외상 유지
    ("솔바람 배추 3박스 3만원 현금 아직 못 받았어", False, None, ["credit_cleared_not_received"]),
    ("솔바람 배추 3박스 3만원 현금 아직 못 받았어", None, None, []),
    ("솔바람 배추 3박스 3만원 계좌로 아직 못받음", True, True, []),
    ("솔바람 배추 3박스 3만원 현금 받았어", True, None, ["credit_cleared_contradiction"]),  # 기존 0b 그대로
])
def test_not_received_payment_word_is_not_the_paid_side(sent_text, credit_in, credit_out, applied_out) -> None:
    """PM 10/12: with "아직·안/못 받" Rule 0b does not count the payment word, so an LLM 외상 stays."""
    result, applied = postprocess_candidate(_candidate(credit=credit_in), sent_text)
    assert result["credit"] is credit_out
    assert applied == applied_out


@pytest.mark.parametrize("sent_text, credit_in, credit_out", [
    ("솔바람 배추 3박스 3만원 현금 받았어", None, False),           # 그대로 현금
    ("솔바람 배추 3박스 3만원 계좌로 받았어요", False, False),
    ("솔바람 배추 3박스 3만원 외상이요", None, True),               # 그대로 외상
    ("솔바람 배추 3박스 3만원 외상이요", True, True),
    ("솔바람 배추 3박스 3만원 외상 아직 안 갚았어", True, True),     # 외상(true)은 건드리지 않음
])
def test_not_received_rule_leaves_other_cases(sent_text, credit_in, credit_out) -> None:
    result, _ = postprocess_candidate(_candidate(credit=credit_in), sent_text)
    assert result["credit"] is credit_out


# pp-1.4 (PM 10/8, DL-30): partial payment is neither paid nor credit → always asked, never filled
@pytest.mark.parametrize("sent_text", [
    "솔바람 대파 3단 9000원 3000원만 받았어",
    "솔바람 대파 3단 9000원 중에 3000원 받았어",
    "솔바람 대파 3단 9000원 3000원 받았고 나머지는 외상",
    "솔바람 대파 3단 9000원 3000원 현금으로 받음 나머지 다음에",
])
def test_partial_payment_is_asked(sent_text) -> None:
    for credit_in in (None, False, True):
        result, _ = postprocess_candidate(_candidate(credit=credit_in), sent_text)
        assert result["credit"] is None, credit_in


def test_partial_payment_rule_clears_a_credit_word_value() -> None:
    """"나머지는 외상" has only a credit word, so without Rule 0p the LLM 외상 would be kept or filled."""
    text = "솔바람 대파 3단 9000원 3000원 받았고 나머지는 외상"
    assert postprocess_candidate(_candidate(credit=True), text)[1] == ["credit_cleared_partial_payment"]
    assert postprocess_candidate(_candidate(credit=None), text)[1] == []


# 오탐 방지: "중에·나머지·만"이 부분 결제가 아닌 문장
@pytest.mark.parametrize("sent_text, credit_out", [
    ("솔바람 대파 3단 9000원 받았어", False),                 # 전액
    ("솔바람 대파 3단 9000원 외상 나머지 품목은 내일", True),     # 받은 말 없음 → 부분 결제 아님
    ("솔바람 대파 3단 9만원 받았어", False),                  # "만"은 수사 — "원만 받"이 아님
])
def test_partial_payment_false_positives(sent_text, credit_out) -> None:
    result, applied = postprocess_candidate(_candidate(credit=None), sent_text)
    assert result["credit"] is credit_out
    assert "credit_cleared_partial_payment" not in applied


# 오탐: a credit word, a later/promise form, or not-yet ("못 받았어") is not a payment (PM 10/8)
@pytest.mark.parametrize("sent_text, credit", [
    ("솔바람 대파 3단 9000원 외상으로 받았어", True),
    ("솔바람 대파 3단 9000원 외상 처리 결제 완료", True),
    ("솔바람 대파 3단 9000원 나중에 받기로", False),
    ("솔바람 대파 3단 9000원 받을 돈", False),
    ("솔바람 대파 3단 9000원 다음 달에 받았어 처리", False),
    ("솔바람 대파 3단 9000원 아직 못 받았어", False),
    ("솔바람 대파 3단 9000원 안 받았어", False),
    ("솔바람 대파 3단 9000원 주문받았어", False),                          # 받 inside a word
    ("솔바람 대파 3단 9000원 계산 안 했어", False),
])
def test_paid_phrase_false_positives(sent_text, credit) -> None:
    assert says_payment(sent_text) is False
    assert says_credit(sent_text) is credit


# 셀프 점검(10/8) 새 문장 18개 — LLM credit null / false / true 각각의 후처리 결과.
# 앞 10개는 main(pp-1.3.1)처럼 되물어야 한다: 받은 것이 돈이 아님, 미래, "~어야", 뒤 부정, 금액 아닌 "계산했어".
@pytest.mark.parametrize("sent_text, out_null, out_false, out_true", [
    ("솔바람 대파 3단 9000원 주문 받았어", None, None, None),
    ("솔바람 대파 3단 9000원 반품 받았어", None, None, None),
    ("솔바람 대파 3단 9000원 다음주에 받음", None, None, None),
    ("솔바람 대파 3단 9000원 담주에 받았어로 해 줘", None, None, None),
    ("솔바람 대파 3단 9000원 주말에 받기로 했고 일단 받음 처리", None, None, None),
    ("솔바람 대파 3단 9000원 받았어야 했는데", None, None, None),
    ("솔바람 대파 3단 9000원 결제했어야 하는데", None, None, None),
    ("솔바람 대파 3단 9000원 결제 완료 안 됨", None, None, None),
    ("솔바람 대파 3단 9000원 계산했는데 돈은 아직", None, None, None),
    ("솔바람 대파 3단 9000원 금액만 계산했어", None, None, None),
    ("오늘 솔바람 대파 3단 9000원 받았어", False, False, None),
    ("솔바람 대파 3단 9000원 돈 받았고 영수증 줬어", False, False, None),
    ("솔바람 대파 3단 9000원 외상 노트에 적어 둬", True, None, True),
    ("솔바람 대파 3단 9000원 외상은 이번에 안 해 카드로", False, False, None),
    ("솔바람 대파 3단 9000원 현금은 지금 안 해 외상으로", True, None, True),
    ("솔바람 대파 3단 9000원 외상은 원래 안 해 근데 이번만 외상", True, None, True),
    ("솔바람 대파 세단구천원외상", True, None, True),
    ("솔바람 대파 3단 9000원 외상 노 사장 말고 솔바람", True, None, True),
])
def test_self_review_sentences(sent_text, out_null, out_false, out_true) -> None:
    for credit_in, expected in ((None, out_null), (False, out_false), (True, out_true)):
        result, _ = postprocess_candidate(_candidate(credit=credit_in), sent_text)
        assert result["credit"] is expected, credit_in


# pp-1.4 (PM 10/8): "외상" glued to a numeral + unit/원 (STT drops spaces) counts; words inside names still do not
@pytest.mark.parametrize("sent_text, credit, payment", [
    ("솔바람한테대파세단구천원외상", True, False),                             # PM 예문
    ("솔바람 대파 세단외상으로", True, False),
    ("솔바람 대파 3단외상으로", True, False),
    ("솔바람 대파 3단 9000원외상", True, False),
    ("솔바람 대파 3단 구천원외상 아니고 현금", False, True),                    # a glued word can still be negated
    ("솔바람 카드지갑 2개 9000원", False, False),                             # "카드지갑" still not a payment word
    ("공원외상회 대파 3단 9000원", False, False),                             # 원 not after a numeral
    ("솔바람 대파외상 3단 9000원", False, False),                             # glued to a name, not an amount
])
def test_glued_credit_word(sent_text, credit, payment) -> None:
    assert says_credit(sent_text) is credit
    assert says_payment(sent_text) is payment


def test_unit_price_rule_can_be_skipped_while_credit_rules_run() -> None:
    """Normalization off → unit_price_rules=False: credit rules still apply, unit_price stays."""
    result, applied = postprocess_candidate(_candidate(credit=False, unit_price=6000), "솔바람 대파 육천원",
                                            unit_price_rules=False)
    assert result["credit"] is None
    assert result["items"][0]["unit_price"] == 6000
    assert applied == ["credit_cleared_no_word"]


# pp-1.3.1 (PM 10/8): a negated word ("외상 안 해") is not "said" — it never fills credit=true
@pytest.mark.parametrize("sent_text, credit, payment, credit_negated, payment_negated", [
    ("솔바람 대파 3단 9000원 외상 안 해", False, False, True, False),        # PM 예문
    ("솔바람 대파 3단 9000원 외상 아니야", False, False, True, False),       # PM 예문
    ("솔바람 대파 3단 9000원 외상은 없어", False, False, True, False),       # PM 예문
    ("솔바람 대파 3단 9000원 달아놓지 마", False, False, True, False),       # PM 예문
    ("솔바람 대파 3단 9000원 달아 놓지 마", False, False, True, False),
    ("솔바람 대파 3단 9000원 외상 아니고", False, False, True, False),
    ("솔바람 대파 3단 9000원 외상 아니에요", False, False, True, False),
    ("솔바람 대파 3단 9000원 외상 아니다", False, False, True, False),      # 아니다·아님·아닌 (PM 10/8 추가)
    ("솔바람 대파 3단 9000원 외상 아님", False, False, True, False),
    ("솔바람 대파 3단 9000원 외상 아닌데 현금", False, True, True, False),
    ("솔바람 대파 3단 9000원 외상 아니다 현금", False, True, True, False),
    ("솔바람 대파 3단 9000원 외상 안 했어", False, False, True, False),
    ("솔바람 대파 3단 9000원 외상 안 할게", False, False, True, False),
    ("솔바람 대파 3단 9000원 외상은 없이", False, False, True, False),
    ("솔바람 대파 3단 9000원 외상 말고", False, False, True, False),
    ("솔바람 대파 3단 9000원 외상하지 마", False, False, False, False),     # "외상하" is not a word (pp-1.1) → no word
    ("솔바람 대파 3단 9000원 외상 하지 마", False, False, True, False),
    ("솔바람 대파 3단 9000원 외상 못 해", False, False, True, False),
    ("솔바람 대파 3단 9000원 안 달아", False, False, True, False),          # 앞쪽 "안" + 동사
    ("솔바람 대파 3단 9000원 안 했어 외상", False, False, True, False),     # 앞쪽 "안 했어"
    ("솔바람 대파 3단 9000원 현금 아니야", False, False, False, True),
    ("솔바람 대파 3단 9000원 외상 아니고 현금", False, True, True, False),   # 결제 단어만 있음
    ("솔바람 대파 3단 9000원 현금 아니고 외상", True, False, False, True),   # 외상 단어만 있음
    ("솔바람 대파 3단 9000원 카드 안 돼서 외상", True, False, False, True),
    ("솔바람 대파 3단 9000원 현금 없어서 달아 줘", True, False, False, True),
    ("솔바람 대파 3단 9000원 외상 안 해 카드로", False, True, True, False),  # 그 "안"은 외상 몫 — 카드는 그대로
])
def test_negated_words_do_not_count(sent_text, credit, payment, credit_negated, payment_negated) -> None:
    assert says_credit(sent_text) is credit
    assert says_payment(sent_text) is payment
    assert says_negated_credit(sent_text) is credit_negated
    assert says_negated_payment(sent_text) is payment_negated


# 오탐 방지: 성씨 "안", "월말·말일"의 "말", 낱말 속 결제어는 부정이 아니다
@pytest.mark.parametrize("sent_text, credit, payment", [
    ("안 사장님한테 대파 3단 9000원 외상", True, False),
    ("솔바람 대파 3단 9000원 월말에 외상", True, False),
    ("솔바람 대파 3단 9000원 말일까지 외상", True, False),
    ("솔바람 카드지갑 2개 9000원", False, False),
    ("솔바람 카드지갑 2개 9000원 안 했어", False, False),
])
def test_negation_false_positives(sent_text, credit, payment) -> None:
    assert says_credit(sent_text) is credit
    assert says_payment(sent_text) is payment
    assert says_negated_credit(sent_text) is False
    assert says_negated_payment(sent_text) is False


# pp-1.4 (PM 10/8): 아니라 · a negation a word or two later (after 은·는·도, only 안 해/안 할) · 노·노노
@pytest.mark.parametrize("sent_text, credit, payment, credit_negated, payment_negated", [
    ("솔바람 대파 3단 9000원 외상이 아니라 현금", False, True, True, False),        # PM 예문
    ("솔바람 대파 3단 9000원 외상은 다음에 안 해 현금", False, True, True, False),   # PM 예문 — 그 "안"은 외상 몫
    ("솔바람 대파 3단 9000원 외상은 다음 달에 안 할게 카드로", False, True, True, False),
    ("솔바람 대파 3단 9000원 외상도 이번엔 안 해", False, False, True, False),
    ("솔바람 대파 3단 9000원 외상 노노 현금", False, True, True, False),             # PM 예문
    ("솔바람 대파 3단 9000원 외상 노", False, False, True, False),                  # PM 예문
    ("솔바람 대파 3단 9000원 현금은 이번엔 안 해 외상", True, False, False, True),   # 결제어 쪽도 같은 함수
])
def test_negation_additions(sent_text, credit, payment, credit_negated, payment_negated) -> None:
    assert says_credit(sent_text) is credit
    assert says_payment(sent_text) is payment
    assert says_negated_credit(sent_text) is credit_negated
    assert says_negated_payment(sent_text) is payment_negated


# 오탐: 이름 속 "아닌"·"노", 성씨 "노", 조사 없는 먼 "안 해", 세 낱말 넘게 떨어진 "안 해", 사이에 낀 결제어
@pytest.mark.parametrize("sent_text, credit, payment", [
    ("아닌상회 배추 3포기 9000원 외상", True, False),                             # PM 10/8
    ("솔바람 대파 3단 9000원 외상 노을상회", True, False),
    ("솔바람 대파 3단 9000원 외상 노 사장님한테", True, False),
    ("노 사장님 대파 3단 9000원 외상", True, False),
    ("솔바람 대파 3단 9000원 외상으로 하고 영수증은 안 해", True, False),           # "외상으로"는 주제 조사 아님
    ("솔바람 대파 3단 9000원 외상은 월말에 정산 영수증은 안 해", True, False),      # 세 낱말 → 범위 밖
])
def test_negation_addition_false_positives(sent_text, credit, payment) -> None:
    assert says_credit(sent_text) is credit
    assert says_payment(sent_text) is payment
    assert says_negated_credit(sent_text) is False


def test_later_negation_belongs_to_the_nearer_word() -> None:
    """"외상은 카드 안 해": the 안 negates 카드 (the word in between), so 외상 still counts."""
    text = "솔바람 대파 3단 9000원 외상은 카드 안 해"
    assert says_credit(text) is True
    assert says_negated_payment(text) is True
    assert says_negated_credit(text) is False


@pytest.mark.parametrize("sent_text, credit_in, credit_out, applied_out", [
    ("솔바람 대파 3단 9000원 외상이 아니라 현금", True, None, ["credit_cleared_contradiction"]),
    ("솔바람 대파 3단 9000원 외상은 다음에 안 해 현금", True, None, ["credit_cleared_contradiction"]),
    ("솔바람 대파 3단 9000원 외상 노노 현금", None, False, ["credit_from_text"]),
    ("솔바람 대파 3단 9000원 외상 노", True, None, ["credit_cleared_negated"]),
])
def test_negation_additions_in_postprocess(sent_text, credit_in, credit_out, applied_out) -> None:
    result, applied = postprocess_candidate(_candidate(credit=credit_in), sent_text)
    assert result["credit"] is credit_out
    assert applied == applied_out


def test_says_negation_takes_a_word_span() -> None:
    """Public helper for the voice-answer reader (S-18)."""
    text = "외상 아니고 현금"
    assert says_negation(text, 0, 2) is True
    assert says_negation(text, 7, 9) is False


@pytest.mark.parametrize("sent_text", [
    "솔바람 대파 3단 9000원 외상 안 해",
    "솔바람 대파 3단 9000원 외상 아니야",
    "솔바람 대파 3단 9000원 외상은 없어",
    "솔바람 대파 3단 9000원 달아놓지 마",
    "솔바람 대파 3단 9000원 현금 아니야",
])
def test_only_negated_word_neither_fills_nor_clears_as_no_word(sent_text) -> None:
    """Rule 1 never fills from a negated word, and Rule 0 does not treat it as "no word"."""
    result, applied = postprocess_candidate(_candidate(credit=None), sent_text)
    assert result["credit"] is None
    assert applied == []


@pytest.mark.parametrize("sent_text, credit_in, credit_out, applied_out", [
    ("솔바람 대파 3단 9000원 외상 안 해요", True, None, ["credit_cleared_negated"]),  # 부정된 외상 + AI true → 되묻기
    ("솔바람 대파 3단 9000원 현금 아니야", False, None, ["credit_cleared_negated"]),  # 부정된 결제어 + AI false → 되묻기
    ("솔바람 대파 3단 9000원 외상 안 해요", False, False, []),                         # 부정과 같은 쪽 → 그대로
    ("솔바람 대파 3단 9000원 현금 아니야", True, True, []),
    ("솔바람 대파 3단 9000원 외상 안 해요", None, None, []),                           # AI가 비움 → 비운 채로
    ("솔바람 대파 3단 9000원 달아 놓지 마", True, None, ["credit_cleared_negated"]),
    ("솔바람 대파 3단 9000원 외상 아니다", True, None, ["credit_cleared_negated"]),
])
def test_llm_value_against_the_negation_is_cleared(sent_text, credit_in, credit_out, applied_out) -> None:
    result, applied = postprocess_candidate(_candidate(credit=credit_in), sent_text)
    assert result["credit"] is credit_out
    assert applied == applied_out


@pytest.mark.parametrize("sent_text, credit_out", [
    ("솔바람 대파 3단 9000원 외상 아니고 현금", False),
    ("솔바람 대파 3단 9000원 현금 아니고 외상", True),
    ("솔바람 대파 3단 9000원 카드 안 돼서 외상", True),
    ("솔바람 대파 3단 9000원 현금 없어서 달아 줘", True),
    ("안 사장님한테 대파 3단 9000원 외상", True),
    ("솔바람 대파 3단 9000원 월말에 외상", True),
    ("솔바람 대파 3단 9000원 말일까지 외상", True),
])
def test_the_other_side_word_fills_credit(sent_text, credit_out) -> None:
    result, applied = postprocess_candidate(_candidate(credit=None), sent_text)
    assert result["credit"] is credit_out
    assert applied == ["credit_from_text"]


# PM 10/12 확정: 결제 단어 + 미래·약속 표지 = "현금 아직 못 받았어"와 같은 규칙 (Rule 0u · 0b · Rule 1)
_FUTURE_PAYMENT = [
    "솔바람 배추 3박스 3만원 현금 나중에",                     # PM 예문
    "솔바람 배추 3박스 3만원 현금으로 내일 준대",                # PM 예문
    "솔바람 배추 3박스 3만원 계좌로 다음 주에 보낸대",           # PM 예문
    "솔바람 배추 3박스 3만원 카드는 월말에",                    # PM 예문 (카드도 같은 규칙, PM 확정)
    "솔바람 배추 3박스 3만원 현금 나중에 받기로",
    "솔바람 배추 3박스 3만원 계좌로 나중에 줄게요",
    "솔바람 배추 3박스 3만원 현금으로 모레 준대",
    "솔바람 배추 3박스 3만원 현금 나중에 받기로 했어",
]


@pytest.mark.parametrize("sent_text", _FUTURE_PAYMENT)
@pytest.mark.parametrize("credit_in, credit_out, applied_out", [
    (True, True, []),                                     # AI 외상 → 외상 유지 (받을 돈이 사라지지 않음)
    (False, None, ["credit_cleared_not_received"]),       # AI 현금 → 비움 → 되묻기
    (None, None, []),                                     # AI 판단 안 함 → 현금으로 안 채움
])
def test_future_payment_is_never_cash(sent_text, credit_in, credit_out, applied_out) -> None:
    result, applied = postprocess_candidate(_candidate(credit=credit_in), sent_text)
    assert result["credit"] is credit_out
    assert applied == applied_out


@pytest.mark.parametrize("sent_text, credit_in, credit_out, applied_out", [
    # a time word next to an already-paid verb is not a later payment — unchanged from before
    ("솔바람 배추 3박스 3만원 주말에 현금으로 받았어", True, None, ["credit_cleared_contradiction"]),
    ("솔바람 배추 3박스 3만원 내일 배달, 현금 받았어", None, False, ["credit_from_text"]),
    ("솔바람 배추 3박스 3만원 월말에 카드로 결제했어", False, False, []),
    # existing behaviour kept
    ("솔바람 배추 3박스 3만원 현금 받았어", False, False, []),
    ("솔바람 배추 3박스 3만원 현금", None, False, ["credit_from_text"]),
    ("솔바람 배추 3박스 3만원 외상 다음 주에 받기로", None, True, ["credit_from_text"]),
])
def test_paid_verb_or_no_marker_is_unchanged(sent_text, credit_in, credit_out, applied_out) -> None:
    result, applied = postprocess_candidate(_candidate(credit=credit_in), sent_text)
    assert result["credit"] is credit_out
    assert applied == applied_out


# --- Rule 3 (PM 10/12): LLM total not in the text + one stated total → the stated total ---------------

def _priced(qty, unit_price, total, credit=None) -> dict:
    """An LLM candidate that computed qty×unit price into amount and total (credit is filled from the text)."""
    return {
        "counterparty": "솔바람",
        "items": [{"name": "품목", "qty": qty, "unit": None, "unit_price": unit_price, "amount": total}],
        "total_amount": total,
        "credit": credit,
    }


@pytest.mark.parametrize("sent_text, qty, unit_price, llm_total, stated", [
    # 114번 재현용 (PM 10/12 요청 건): Seed v2 114 문장 그대로 — AI가 6×2100=12600을 총액에 넣음
    ("가온유통한테 튀김가루 6봉지 봉지당 2100원, 전부 9600원 외상", 6, 2100, 12600, 9600),
    ("솔바람 양파 4망 망당 3500원 전부 12000원 외상", 4, 3500, 14000, 12000),
    ("솔바람 양파 4망 망당 3500원 총 12000원 카드로", 4, 3500, 14000, 12000),
    ("솔바람 양파 4망 망당 3500원 총액 12,000원 카드로", 4, 3500, 14000, 12000),
    ("솔바람 양파 4망 망당 3500원 합계 12000원 외상", 4, 3500, 14000, 12000),
    ("솔바람 양파 4망 망당 3500원 다 합해서 12000원 외상", 4, 3500, 14000, 12000),
    ("솔바람 양파 4망 망당 3500원 합해서 12000원 외상", 4, 3500, 14000, 12000),
    ("솔바람 두부 5모 모당 1200원 전부 7000원 현금", 5, 1200, 6000, 7000),  # stated more than computed
    ("솔바람 두부 5모 모당 1200원 총7000원 현금", 5, 1200, 6000, 7000),     # glued after normalization
])
def test_computed_total_goes_back_to_the_stated_total(sent_text, qty, unit_price, llm_total, stated) -> None:
    from app.services.validation import validate_candidate

    candidate = _priced(qty, unit_price, llm_total)
    result, applied = postprocess_candidate(candidate, sent_text)
    assert result["total_amount"] == stated
    assert "total_restored_stated" in applied
    # qty·unit_price·amount are not touched — the contradiction is left for the validator to ask about
    assert result["items"] == candidate["items"]
    failure = validate_candidate(result)
    assert failure is not None and failure.error_code == "AMOUNT_MISMATCH"
    assert "total_amount" in failure.problem_fields


@pytest.mark.parametrize("sent_text, llm_total", [
    ("솔바람 양파 4망 망당 3500원 외상", 14000),                        # (나) computed total, nothing stated
    ("솔바람 총각김치 2통 통당 8000원 외상", 16000),                     # 총 inside a word is no stated total
    ("솔바람 양파 4망 망당 3500원 전부 12000원 외상", 12000),            # LLM total is the stated number
    ("솔바람 양파 4망 망당 3000원 전부 12000원 외상", 12000),            # consistent sentence
    ("솔바람 양파 4망 12000원 대파 3500원 외상", 3500),                   # LLM total is another number in the text
    ("솔바람 양파 2망 총 7000원 대파 3단 총 6000원 외상", 14000),        # two stated totals → left as is
    ("솔바람 양파 4망 망당 3500원 전부 12000원 합계 12000원 외상", 14000),
    ("솔바람 양파 4망 총 12000원 중 5000원 받음", 7000),                 # "N원 중" is not this sale's total
    ("솔바람 양파 4망 전부 12000원인데 5000원만 받았어", 7000),          # partial payment
    ("솔바람 양파 4망 총 14000원에서 2000원 깎아줬어 카드", 12000),      # "N원에서": not the final total
    ("솔바람 양파 4망 망당 3500원 전부 14000원인데 2000원 할인해 줬어", 12000),  # discount word → left alone
    ("솔바람 양파 4망 망당 3500원 할인해서 전부 12000원 카드", 13000),  # discount before the total too
    ("솔바람 양파 4망 전부 3500원씩 외상", 14000),                       # "전부 N원씩·짜리" is a unit price
    ("솔바람 양파 4망 전부 3500원짜리 외상", 14000),
    ("솔바람 양파 4망 망당 3500원 총 14000원 배달비 2000원 외상", 16000),  # another amount after the total
    ("솔바람 양파 4망 망당 3500원 도합 12000원 외상", 14000),            # 도합·합쳐서: later candidates (PM 10/12)
    ("솔바람 양파 4망 망당 3500원 합쳐서 12000원 외상", 14000),
])
def test_total_is_left_alone_without_exactly_one_stated_total(sent_text, llm_total) -> None:
    candidate = _priced(4, 3500, llm_total)
    # Rule 3 only: no item money, else an amount the text never says (3500·qty×3500) is cleared by Rule 4 (PM 10/13)
    candidate["items"][0].update(unit_price=None, amount=None)
    result, applied = postprocess_candidate(candidate, sent_text)
    assert result["total_amount"] == llm_total
    assert "total_restored_stated" not in applied


def test_no_total_or_normalization_off_is_left_alone() -> None:
    sent_text = "솔바람 양파 4망 망당 3500원 전부 12000원 외상"
    result, applied = postprocess_candidate(_priced(4, 3500, None), sent_text)
    assert result["total_amount"] is None and "total_restored_stated" not in applied
    # digits only: with normalization off ("만이천원") the rule does not run, like Rule 2
    result, applied = postprocess_candidate(_priced(4, 3500, 14000), sent_text, unit_price_rules=False)
    assert result["total_amount"] == 14000 and "total_restored_stated" not in applied


# PM 10/13: "단가 3000원"·"하나에 3000원"·"1단 3000원 3단" are unit-price markers too; amounts without a marker are unchanged.
# Rows of the 10/13 study table (fake LLM output, normalized sentence): qty·unit_price·amount per item before → after.
_KEEP = "keep"


@pytest.mark.parametrize("sent_text, items_in, total, items_out", [
    # 1·2 "단가 N원"
    ("솔바람 배추 단가 3000원 3단 외상", [(3, 3000, 9000)], 9000, _KEEP),
    ("솔바람 배추 단가 3000원 3단 외상", [(3, 3000, None)], 9000, _KEEP),
    # 3 "개당" (existing marker)
    ("솔바람 사과 개당 3000원 3개 외상", [(3, 3000, 9000)], 9000, _KEEP),
    # 4·5 "하나에 N원"
    ("솔바람 사과 하나에 3000원 3개 외상", [(3, 3000, 9000)], 9000, _KEEP),
    ("솔바람 사과 하나에 3000원 3개 외상", [(3, 3000, None)], 9000, _KEEP),
    # 6 "한 단에" → "1단에" (existing marker)
    ("솔바람 파 1단에 3000원 3단 외상", [(3, 3000, 9000)], 9000, _KEEP),
    # 7·8 "1단 N원" then another quantity of 단
    ("솔바람 파 1단 3000원 3단 외상", [(3, 3000, 9000)], 9000, _KEEP),
    ("솔바람 파 1단 3000원 3단 외상", [(3, 3000, None)], 9000, _KEEP),
    # 9 "박스당" (existing marker)
    ("솔바람 배추 박스당 5000원 2박스 외상", [(2, 5000, 10000)], 10000, _KEEP),
    # 10 "하나 N원" (no 에): not a marker yet — later candidate. 9000 is never said → Rule 4 (#101) clears it (asked)
    ("솔바람 사과 하나 3000원 3개 외상", [(3, 3000, 9000)], 9000, [(3, None, None)]),
    # 11~14 no marker: unchanged behavior
    ("솔바람 파 2단 6000원 외상", [(2, 3000, 6000)], 6000, [(2, None, 6000)]),
    ("솔바람 파 2단 6000원 외상", [(2, 6000, None)], 6000, [(2, None, 6000)]),
    ("솔바람 배추 2박스 4000원 외상", [(2, 2000, 4000)], 4000, [(2, None, 4000)]),
    ("솔바람 배추 2박스 4000원 외상", [(2, 4000, None)], 4000, [(2, None, 4000)]),
    # 15 total sentence: Rule 2 moves 1600 into amount, but 1600 is never said → Rule 4 (#101) clears both amounts
    ("솔바람 사과 3개 배 3개 전부 9600원 외상", [(3, 1600, None), (3, 1600, None)], 9600, [(3, None, None), (3, None, None)]),
    # 16 lone "1박스 N원" is the item amount (Golden) — unchanged behavior
    ("솔바람 사과 1박스 20000원 외상", [(1, 20000, None)], 20000, [(1, None, 20000)]),
    # 17 "원짜리" (existing marker)
    ("솔바람 사과 6000원짜리 3개 외상", [(3, 6000, 18000)], 18000, _KEEP),
])
def test_pm_1013_unit_price_markers(sent_text, items_in, total, items_out) -> None:
    candidate = {
        "counterparty": "솔바람",
        "items": [{"name": f"품목{i}", "qty": q, "unit": None, "unit_price": p, "amount": a} for i, (q, p, a) in enumerate(items_in)],
        "total_amount": total,
        "credit": True,
    }
    result, applied = postprocess_candidate(candidate, sent_text)
    expected = items_in if items_out == _KEEP else items_out
    assert [(i["qty"], i["unit_price"], i["amount"]) for i in result["items"]] == expected
    assert ("unit_price_cleared" in applied) is (items_out != _KEEP)


@pytest.mark.parametrize("sent_text", [
    "솔바람 배추 단가 3000원 3단 외상",
    "솔바람 사과 하나에 3000원 3개 외상",
    "솔바람 파 1단 3000원 3단 외상",
])
def test_kept_unit_price_now_passes_validation(sent_text) -> None:
    """Rows 2·5·8: before, the unit price moved to amount (3000 ≠ total 9000) → 400 AMOUNT_MISMATCH for a normal sentence."""
    from app.services.validation import validate_candidate

    candidate = {"counterparty": "솔바람", "items": [{"name": "품목", "qty": 3, "unit": "개", "unit_price": 3000, "amount": None}],
                 "total_amount": 9000, "credit": True}
    result, _ = postprocess_candidate(candidate, sent_text)
    assert validate_candidate(result) is None


@pytest.mark.parametrize("raw, kept", [
    ("솔바람 파 한 단 삼천 원 세 단 외상", True),         # normalized to "1단 3000원 3단"
    ("솔바람 파 1단 3000원 3단이요 외상", True),
    ("솔바람 배추 단가가 3000원 3단 외상", True),
    ("솔바람 파 1단 3000원 외상", False),                 # quantity 1 only: item amount
    ("솔바람 콜라 1박스 12000원에 과자 5개 5000원 외상", False),  # other unit after it
    ("솔바람 테이프 2개 아니 1개 2400원 외상", False),     # correction: the other quantity comes before
    ("솔바람 파 1단 3000원 10단 외상", True),              # 10 is another quantity, not 1
    ("솔바람 대파 1단 3000원 쪽파 2단 4000원 외상", False),  # 2단 belongs to another item (self-review)
    ("솔바람 단가표 보고 사과 3개 9000원 외상", False),     # "단가" without a number right after
    ("솔바람 사과 하나에요 3개 9000원 외상", False),        # "하나에요" is not "하나에 N"
])
def test_pm_1013_marker_shapes(raw, kept) -> None:
    from app.services.normalize_ko import normalize_korean_numerals

    sent_text = normalize_korean_numerals(raw).text
    result, _ = postprocess_candidate(_candidate(unit_price=3000, amount=9000), sent_text)
    assert (result["items"][0]["unit_price"] == 3000) is kept


def test_pm_1013_markers_do_not_run_without_normalization() -> None:
    """Normalization off → Rule 2 is skipped as before, so the markers change nothing."""
    result, applied = postprocess_candidate(_candidate(unit_price=6000, amount=None), "솔바람 대파 2단 12000원",
                                            unit_price_rules=False)
    assert result["items"][0]["unit_price"] == 6000 and "unit_price_cleared" not in applied


def test_one_unit_marker_does_not_reach_another_item() -> None:
    """Self-review: "대파 1단 3000원 쪽파 2단 4000원" stays as on main (200), not a 400 from 쪽파 unit_price 4000."""
    from app.services.validation import validate_candidate

    candidate = {"counterparty": "솔바람", "items": [
        {"name": "대파", "qty": 1, "unit": "단", "unit_price": 3000, "amount": None},
        {"name": "쪽파", "qty": 2, "unit": "단", "unit_price": 4000, "amount": None}], "total_amount": 7000, "credit": True}
    result, _ = postprocess_candidate(candidate, "솔바람 대파 1단 3000원 쪽파 2단 4000원 외상")
    assert [(i["unit_price"], i["amount"]) for i in result["items"]] == [(None, 3000), (None, 4000)]
    assert validate_candidate(result) is None
