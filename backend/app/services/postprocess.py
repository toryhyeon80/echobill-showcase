"""Amount/credit Rule post-processing of an LLM candidate (CLAUDE.md §4 AI: 금액·외상 Rule 후처리).

Deterministic: looks only at the candidate and the text actually sent to the LLM, so it can be
applied live (LLM_POSTPROCESS) or offline to saved predictions. Pure function, no logging.
"""

from __future__ import annotations

import copy
import re
from decimal import Decimal
from typing import Any

from app.services.normalize_ko import UNIT_SUFFIXES, UNITS, spoken_numbers
from app.services.payment_phrases import (
    has_paid_phrase,
    says_not_paid_now,
    says_not_paid_yet,
    says_partial_payment,
)

POSTPROCESS_VERSION = "pp-1.6"  # pp-1.6: 거래처가 품목 이름과 같으면 거래처를 비움 (규칙 6, PM 10/29)
# pp-1.5: 말한 금액 하나를 총액으로 되살림 (규칙 5, PM 10/16)
# pp-1.4: 문장 단어와 반대인 credit=null, 외상 규칙은 정규화와 무관 (PM 10/5)
# pp-1.3.1: 부정된 단어("외상 안 해")는 안 셈 (PM 10/8) · pp-1.3: 단어 없으면 credit=null, 결제어 "현찰" (PM 10/2)
# 결제어 "송금" — 이체와 같은 급 (PM #44 코멘트)

PAYMENT_WORDS = ("현금", "현찰", "카드", "입금", "계좌", "이체", "송금")
CREDIT_WORDS = ("외상", "미수")
# "달아" keeps substring matching: many inflections (달아줘·달아놔·달아요) and few item-name clashes.
CREDIT_SUBSTRINGS = ("달아",)
# pp-1.1: a payment/credit word counts only as a whole word — not inside an item name such as
# "카드지갑" or "현금영수증" — so a missing payment method still triggers the MISSING follow-up.
WORD_SUFFIXES = ("으로", "로", "이요", "요", "이고", "이에요", "결제", "계산", "거래", "값", "이", "처리",
                 "했", "받", "줬", "주", "해", "는", "은", "도", "만")
_SUFFIX_PATTERN = "|".join(sorted(WORD_SUFFIXES, key=len, reverse=True))


def _word_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    """Match a word preceded by a non-Hangul char and followed by a non-Hangul char or allowed suffix."""
    return re.compile(rf"(?<![가-힣])(?:{'|'.join(words)})(?=$|[^가-힣]|(?:{_SUFFIX_PATTERN}))")


_PAYMENT_PATTERN = _word_pattern(PAYMENT_WORDS)
_CREDIT_PATTERN = _word_pattern(CREDIT_WORDS)
# pp-1.4 (PM 10/8): "외상" glued to an amount or quantity ("구천원외상", "세단외상으로" — STT drops spaces).
# Only a numeral + unit/원 right before it counts; words inside names ("카드지갑") stay excluded.
_GLUED_CREDIT = re.compile(rf"(?<=[가-힣])외상(?=$|[^가-힣]|(?:{_SUFFIX_PATTERN}))")
_NUMERAL_UNIT_BEFORE = re.compile(rf"[\d일이삼사오육칠팔구십백천만억한두세네섯곱덟홉열](?:{'|'.join(UNITS)})$")
# pp-1.4 (PM 10/8): clear past-tense "paid" phrases ("9000원 받았어", "돈 받음", "결제 완료") count as a payment
# word — the phrase and the later/not-yet/partial markers live in payment_phrases.py (shared with Rule and voice answer).
# Unit-price markers (PM-approved A-4): "박스당 7200"·"박스 당 7200"(pp-1.1), "6000원짜리"·"6000원씩",
# "한 박스에 15000"·"1봉지에 4500"
UNIT_PRICE_MARKER = re.compile(r"\S+\s?당\s*\d|\d[\d,]*\s*원\s*(짜리|씩)|(?:^|\s)(한|1)\s*\S{1,4}에\s*\d")
# PM 10/12 (Seed v2 114): a stated total — "전부·총·총액·합계·(다) 합해서" right before "N원". "N원 중"·"N원에서" is
# not the final total ("총 30000원 중 20000원 받음", "총 18000원에서 2000원 깎아줬어") and "전부 3000원씩·짜리" is a
# unit price, so they do not count. Another amount after it ("총 15000원 배달비 2000원") or a discount word anywhere
# leaves the total alone (the LLM may have added or taken it off correctly). 도합·합쳐서: later candidates.
_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?!\d)|\d+(?:\.\d+)?")
_STATED_TOTAL = re.compile(
    r"(?<![가-힣])(?:전부|총액|총|합계|(?:다\s?)?합해서)\s*(\d{1,3}(?:,\d{3})+(?!\d)|\d+)\s*원(?!\s?(?:중|에서|씩|짜리))"
)
_AMOUNT = re.compile(r"\d\s*원")
_DISCOUNT = re.compile(r"깎|할인|에누리|빼\s?(?:줬|주|드렸|드려)")
# PM 10/13: more unit-price markers next to UNIT_PRICE_MARKER — "단가 3000원"·"하나에 3000원", and "1단 3000원" only
# when another quantity of the same unit comes right after it ("1단 3000원 3단"). A lone "1박스 20000원" is the item
# amount (Golden labels it so), "2개 아니 1개 2400원" is a correction, and "대파 1단 3000원 쪽파 2단" is another item,
# so none of them counts. Later candidates: "하나 3000원", "개에 3000원", "단가는 삼천"(not normalized).
_UNIT_PRICE_WORD = re.compile(r"단가\s*(?:는|가|은)?\s*\d|(?<![가-힣])하나에\s*\d")
_ONE_UNIT_THEN_QTY = re.compile(
    rf"(?:^|\s)1\s*({'|'.join(UNITS)})\s+\d[\d,]*\s*원(?:씩)?\s+(?!1(?![\d.]))\d+(?:\.\d+)?\s*\1"
    r"(?=$|[^가-힣]|에|씩|이|요|으로|로|만)"
)


def _says_unit_price(text: str) -> bool:
    """Whether the text states a unit price (UNIT_PRICE_MARKER or the PM 10/13 markers) — Rule 2 keeps it then."""
    return bool(UNIT_PRICE_MARKER.search(text) or _UNIT_PRICE_WORD.search(text) or _ONE_UNIT_THEN_QTY.search(text))


# pp-1.3.1 (PM 10/8): negation right after the word (rest of the word and one space allowed):
# 아니야·아니고·아니에요·아니다·아니라·아님·아닌 / 안 해·안 했·안 할·안 돼 / (은·는) 없어·없이 / 말고 / (하)지 마·놓지 마 /
# 못 해·못 했 / 노·노노. "말" alone is not a negation ("월말에 외상"), "안" only before a verb ("안 사장님한테 외상"),
# and "노" only as a whole word, not a surname ("외상 노 사장님한테"). pp-1.4 added 아니라·노·노노.
_NEGATION_AFTER = re.compile(
    r"[가-힣]*?\s?(?:아니(?:야|고|에요|다|라)|아님|아닌|안\s?(?:하|해|했|할|돼|됐|되)|없(?:어|이)|말고|(?:하|놓|두|주)?지\s?마"
    r"|못\s?(?:해|했)|노(?:노)?(?![가-힣])(?!\s?(?:사장|씨|선생|여사)))"
)
_TERMS = "|".join(PAYMENT_WORDS + CREDIT_WORDS + CREDIT_SUBSTRINGS)
# pp-1.4 (PM 10/8): a negation a word or two later — only after a topic particle and only "안 해/안 할"
# ("외상은 다음에 안 해 현금"). The words in between must not hold another payment/credit word ("외상은 카드 안 해").
_LATER_WORDS = rf"(?:은|는|도)(?:\s+(?![가-힣]*(?:{_TERMS}))[가-힣]+){{1,2}}\s+"
_NEGATION_LATER = re.compile(rf"{_LATER_WORDS}안\s?(?:해|할)")
# Before the word only fixed forms: "안 달아" (verb word directly) and "안 했어 외상" (안 + 하다 form).
_NEGATION_BEFORE = re.compile(r"(?<![가-힣])안\s?(?P<verb>(?:하|해|했|할)[가-힣]*\s)?$")
# A preceding payment/credit word owns that "안": in "외상 안 해 카드로" the 안 negates 외상, not 카드 —
# also a word or two earlier ("외상은 다음에 안 해 현금", see _NEGATION_LATER).
_TERM_BEFORE = re.compile(rf"(?:{_TERMS})(?:[가-힣]*\s?|{_LATER_WORDS})$")


def says_negation(text: str, start: int, end: int) -> bool:
    """True when the payment/credit word at text[start:end] is negated ("외상 안 해", "안 달아", "현금 아니고").

    Shared with the voice-answer reader (S-18), which passes spans of the same words.
    """
    if _NEGATION_AFTER.match(text, end) or _NEGATION_LATER.match(text, end):
        return True
    before = _NEGATION_BEFORE.search(text[:start])
    if before is None or _TERM_BEFORE.search(text[: before.start()]):
        return False
    return bool(before.group("verb")) or text[start:end] in CREDIT_SUBSTRINGS


def _mentions(text: str, pattern: re.Pattern[str], substrings: tuple[str, ...] = (),
              extra_spans: tuple[tuple[int, int], ...] = ()) -> list[bool]:
    """Negated flag of each payment/credit word found in the text (whole words, substrings, extra spans)."""
    spans = [m.span() for m in pattern.finditer(text)]
    spans += [m.span() for word in substrings for m in re.finditer(word, text)]
    spans += extra_spans
    return [says_negation(text, start, end) for start, end in spans]


def _glued_credit_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Spans of "외상" glued right after a numeral + unit/원 ("구천원외상", "3단외상으로")."""
    return tuple(m.span() for m in _GLUED_CREDIT.finditer(text) if _NUMERAL_UNIT_BEFORE.search(text[: m.start()]))


# PM 10/12 (Seed v2 seed2-020): a word glued to a name the LLM extracted — "외상" right before it ("외상푸른마트에",
# "외상배추") or a payment/credit word right after the counterparty ("미래분식카드로"). Only keeps Rule 0 from clearing.
_CREDIT_BEFORE_NAME = re.compile(r"(?<![가-힣])외상")
_WORD_AFTER_NAME = re.compile(rf"(?:{'|'.join(PAYMENT_WORDS + CREDIT_WORDS)})(?=$|[^가-힣]|(?:{_SUFFIX_PATTERN}))")


def _says_word_glued_to_name(text: str, candidate: dict[str, Any]) -> bool:
    """True when a payment/credit word is glued to a name the LLM extracted, none of them negated (PM 10/12).

    "외상" right before the counterparty or an item name, or a payment/credit word right after the counterparty —
    not after an item name: "명함카드" read as item "명함" would make 카드 a payment word (self-review). Names under
    2 letters are skipped. A name that itself starts with 외상 ("외상리슈퍼") has no glued word.
    """
    counterparty = candidate.get("counterparty")
    names = [counterparty] + [item.get("name") for item in candidate.get("items") or [] if isinstance(item, dict)]
    names = [name for name in names if isinstance(name, str) and len(name) >= 2]
    spans = [m.span() for m in _CREDIT_BEFORE_NAME.finditer(text) if any(text.startswith(n, m.end()) for n in names)]
    if counterparty in names:
        spans += [after.span() for m in re.finditer(re.escape(counterparty), text)
                  if (after := _WORD_AFTER_NAME.match(text, m.end()))]
    return bool(spans) and not any(says_negation(text, start, end) for start, end in spans)


def _credit_mentions(text: str) -> list[bool]:
    """Negated flag of each credit word: 외상·미수 as a word, glued 외상, any 달아… form."""
    return _mentions(text, _CREDIT_PATTERN, CREDIT_SUBSTRINGS, _glued_credit_spans(text))


def says_paid_phrase(text: str) -> bool:
    """True when a "paid" phrase (받았어·돈 받음·결제 완료·계산 끝…) states an immediate payment (pp-1.4).

    Not counted with a later/promise/not-yet marker ("나중에 받기로", "내일 준대", "아직 못 받았어").
    """
    if not has_paid_phrase(text) or says_not_paid_yet(text):
        return False
    return not says_credit(text)  # "외상으로 받았어": the credit word decides, not the phrase


def says_payment(text: str) -> bool:
    """True when the text names an immediate payment: 현금·현찰·카드·입금·계좌·이체·송금 as a word, not negated,
    or a "paid" phrase (pp-1.4)."""
    return not all(_mentions(text, _PAYMENT_PATTERN)) or says_paid_phrase(text)


def says_credit(text: str) -> bool:
    """True when the text says credit (외상·미수 as a word, glued 외상, or any 달아… form), not negated."""
    return not all(_credit_mentions(text))


def says_negated_payment(text: str) -> bool:
    """True when a payment word appears negated ("현금 아니야", "카드 안 돼서")."""
    return any(_mentions(text, _PAYMENT_PATTERN))


def says_negated_credit(text: str) -> bool:
    """True when a credit word appears negated ("외상 안 해", "달아놓지 마")."""
    return any(_credit_mentions(text))


def postprocess_active(llm_postprocess: bool, ko_numeral_normalize: bool) -> bool:
    """Whether any rule runs. The credit rules look only at words, so LLM_POSTPROCESS alone turns them on (PM 10/5)."""
    del ko_numeral_normalize  # kept in the signature: callers pass both flags; only the unit-price rule needs it
    return llm_postprocess


def unit_price_rules_active(llm_postprocess: bool, ko_numeral_normalize: bool) -> bool:
    """단가 규칙(Rule 2) 정규식은 숫자 표기를 전제로 하므로 정규화가 켜져 있을 때만 적용한다 (PM #35 리뷰)."""
    return llm_postprocess and ko_numeral_normalize


def postprocess_label(llm_postprocess: bool, ko_numeral_normalize: bool) -> str | None:
    """Version string for what actually ran: full, credit rules only (normalization off), or nothing."""
    if not postprocess_active(llm_postprocess, ko_numeral_normalize):
        return None
    if unit_price_rules_active(llm_postprocess, ko_numeral_normalize):
        return POSTPROCESS_VERSION
    return f"{POSTPROCESS_VERSION}-credit"


def postprocess_candidate(candidate: dict[str, Any], sent_text: str, *,
                          unit_price_rules: bool = True) -> tuple[dict[str, Any], list[str]]:
    """Apply the credit, unit-price and pack-size rules; return a new candidate dict and the rules applied.

    The input dict is not modified. Values the LLM already set are never overwritten, except that
    credit is cleared when the text has no payment/credit word (pp-1.3), has only negated words and the
    LLM chose the negated side (pp-1.3.1), or has (un-negated) words of only one side and the LLM chose
    the other side (pp-1.4), and without any unit-price marker in the text unit_price is cleared (moved
    to amount if amount is empty), and a total that is none of the text's numbers goes back to the one
    stated total (Rule 3, PM 10/12), and an item amount that is none of the numbers said (no unit-price marker)
    is cleared with the total — the total stays when every item amount was invented and it is a said number that
    is no item qty (Rule 4, PM 10/13), and the one amount said goes back as the total of a single item (Rule 5,
    PM 10/16). `unit_price_rules=False` (normalization off) skips Rules 2–5.
    A counterparty equal to an item name (spaces ignored) is cleared (Rule 6, PM 10/29).
    """
    result = copy.deepcopy(candidate)
    applied: list[str] = []
    credit_said = says_credit(sent_text)
    payment_said = says_payment(sent_text)
    credit_negated = says_negated_credit(sent_text)
    payment_negated = says_negated_payment(sent_text)
    only_negated = not credit_said and not payment_said and (credit_negated or payment_negated)
    not_received = says_not_paid_now(sent_text)
    # PM 10/12: with "아직·안/못 받" or a later/promise marker ("현금 나중에", "카드는 월말에") a payment word is not
    # the paid side for Rule 0b ("현금 아직 못 받았어" + LLM 외상 → 외상 kept, the money owed does not disappear). Rule 0 still sees the word, so it does not clear as "no word".
    payment_side = payment_said and not not_received

    # Rule 0 (pp-1.3, D04-6): no payment/credit word at all → credit is unknown, whatever the LLM said.
    # The LLM almost always invents credit=false (현금) here, which would hide a 외상 from the 미수 list.
    # Credit said without a word ("다음 달에 받기로") also becomes null → 400 follow-up (PM accepted).
    # pp-1.3.1: a negated word ("외상 안 해") is still a basis, so the LLM value is kept.
    # PM 10/12: so is a word glued to a name the LLM extracted ("외상푸른마트에" — STT drops spaces). It is a basis
    # only here: Rule 0b, Rule 1 and the negation rules see the words as before.
    if (not credit_said and not payment_said and not only_negated and result.get("credit") is not None
            and not _says_word_glued_to_name(sent_text, candidate)):
        result["credit"] = None
        applied.append("credit_cleared_no_word")
    # Rule 0b (pp-1.4): words of only one side, LLM chose the other ("계좌로 받았어" → 외상) → ask, never flip.
    # Both sides present ("외상 반 현금 반") → the LLM value stays (PM 10/5: rules act only without evidence).
    # A negated word is not "said" (pp-1.3.1): "외상 아니고 현금" + LLM 외상 → payment only → null (PM 10/8).
    elif credit_said != payment_side and result.get("credit") is not None and result["credit"] is not credit_said:
        result["credit"] = None
        applied.append("credit_cleared_contradiction")

    # Rule 0-n (pp-1.3.1, PM 10/8): only negated words and the LLM says the negated side → null (asked).
    # "외상 안 해요" + credit=true, "현금 아니야" + credit=false. Never flipped to the other side.
    if only_negated and credit_negated != payment_negated and result.get("credit") is credit_negated:
        result["credit"] = None
        applied.append("credit_cleared_negated")

    # Rule 0p (pp-1.4, PM 10/8, DL-30): partial payment ("9000원 중에 3000원 받았어", "나머지는 외상") → ask.
    partial = says_partial_payment(sent_text)
    if partial and result.get("credit") is not None:
        result["credit"] = None
        applied.append("credit_cleared_partial_payment")

    # Rule 0u (PM #82 리뷰 10/12): "현금 아직 못 받았어" — not received yet → an LLM 현금 is cleared (asked).
    # PM 10/12: the same for a payment word + later/promise marker ("현금으로 내일 준대", "계좌로 다음 주에 보낸대").
    if not_received and result.get("credit") is False:
        result["credit"] = None
        applied.append("credit_cleared_not_received")

    # Rule 1: fill credit only when the LLM itself left it null and the text is one-sided (negated words never
    # fill). A value cleared by Rule 0b is not refilled — that would flip the LLM, not ask the user.
    if candidate.get("credit") is None and not partial:
        # Never fill 현금 when the money is not in yet (Rule 0u); filling 외상 is unchanged
        if credit_said != payment_said and not (not_received and not credit_said):
            result["credit"] = credit_said
            applied.append("credit_from_text")

    # Rule 2: no unit-price marker anywhere → a stated amount is the whole-item amount (PM A-4).
    if unit_price_rules and not _says_unit_price(sent_text):
        for item in result.get("items") or []:
            if item.get("unit_price") is None:
                continue
            if item.get("amount") is None:
                # PM 확인 요청 (A-4c 1장): keep the only amount instead of deleting it (e.g. "배추 35000원")
                item["amount"] = item["unit_price"]
                _add(applied, "unit_price_to_amount")
            item["unit_price"] = None
            _add(applied, "unit_price_cleared")

    # Pack-size rule (PM 10/13, AI 개선 4-2): "인절미 20개입 팩당 4500원" — 20 is the pack size, not how many packs.
    # An LLM qty N that the text says only as "N개입", with no other numeral + unit, is cleared (asked), and so are the
    # item amount and total equal to N × the LLM's unit price that the text never says (computed from the invented qty;
    # the LLM's own unit price since Rule 2 may have cleared it). A stated total ("전부 9000원") is left to Rule 3.
    # "20개입 3팩"·"20개입 20팩" keep qty. Digits only, so normalization on (like Rules 2·3).
    if unit_price_rules and _clear_pack_size_qty(result, candidate, sent_text):
        applied.append("pack_spec_qty_cleared")

    # Rule 3 (PM 10/12): the LLM total is none of the numbers in the text (it computed qty×unit price) and the
    # text states the total once → put the stated total back, so the arithmetic contradiction reaches the
    # validator (AMOUNT_MISMATCH → asked) instead of saving a larger amount owed. qty·unit_price·amount stay.
    # Not with two or more stated totals, a partial payment or a discount. Digits only, so normalization on (like Rule 2).
    # #98 리뷰 후속: not with "나머지"·"남은" either — restoring "합계 3만원" would save the whole 30000 as 외상. Since #115
    # _PARTIAL_PAYMENT catches "받고 … 나머지"; _REMAINDER still covers "나머지 외상" with no 받 before it. With several items
    # only when an item amount is none of the numbers said (computed, "배추 2박스 박스당 7000원" → 14000): with every
    # amount said, "양파 4망 전부 12000원" may be one item's amount, so the LLM total is left alone.
    # (Skipping when the LLM total equals the item-amount sum was measured too: it undoes Seed v2 114 in all 3 runs.)
    if unit_price_rules and not partial and not _DISCOUNT.search(sent_text) and not _REMAINDER.search(sent_text):
        stated = _stated_total(sent_text)
        total = result.get("total_amount")
        if (stated is not None and total is not None and Decimal(str(total)) not in _numbers(sent_text)
                and _has_computed_amount_or_one_item(result, sent_text)):
            result["total_amount"] = stated
            applied.append("total_restored_stated")

    # Rule 4 (PM 10/13): an item amount that is none of the numbers said, with no unit-price marker, was made up by
    # the LLM (qty×price: "과자 5개 5000원" → 25000) → that amount and the total become null, so the total is asked
    # (MISSING_REQUIRED_FIELD) instead of saving a larger amount owed. Amounts all said → nothing changes.
    # PM 10/13 후속: every item amount invented and the total is a said number (not an item qty) → the total is kept
    # ("배추 3박스 사과 2박스 전부 오만원"). An item without an amount or a said amount left → the total goes too.
    if unit_price_rules and not _says_unit_price_for_amount(sent_text):
        said = _numbers(sent_text) | spoken_numbers(sent_text)
        items = result.get("items") or []
        invented = [item for item in items
                    if item.get("amount") is not None and Decimal(str(item["amount"])) not in said]
        keep_total = len(invented) == len(items) and _total_said_not_qty(result.get("total_amount"), items, said)
        for item in invented:
            item["amount"] = None
        if invented:
            if not keep_total:
                result["total_amount"] = None
            applied.append("item_amount_invented")

    # Rule 5 (PM 10/16): one item and the text says exactly one amount "N원" ("고등어 2마리 3500원 현금") — the LLM
    # put it in unit_price (Rule 2 moved it to the item amount) or multiplied it (Rule 4 cleared it), so the total
    # was asked again. That amount becomes the total (and a missing or invented item amount). Not with a unit-price
    # marker, a partial payment, a discount, "나머지", another 천·만 numeral ("고추 만오천"), or a total that is
    # already a said number. PM 10/16 허용 방식 (#162 리뷰): only the shape "one quantity right before N원" ("2마리
    # 3500원", "3단 8000원 치") or no quantity at all ("배추 35000원") — any other wording may hide a unit price ("3봉지
    # 봉지마다 5천원", "5개 한 알 천원") and would save one unit's price as the total. The hidden unit-price exclusions
    # (a unit said twice, "각·각각·낱개·개별", "한/1 + unit + amount") stay as a second guard. Only when the LLM gave some
    # amount (unit_price, item amount or total) — an amount the rules then moved or cleared; with none at all it is
    # asked as before. Digits only, so normalization on (like Rules 2–4).
    if (unit_price_rules and _llm_gave_amount(candidate) and not partial and not _DISCOUNT.search(sent_text)
            and not _REMAINDER.search(sent_text)):
        spoken = _one_spoken_amount(sent_text)
        if spoken is not None and _restore_spoken_total(result, spoken, sent_text):
            applied.append("total_restored_spoken")

    # Rule 6 (PM 10/29, 내부 테스트): the LLM put an item name in the counterparty slot — STT heard "새롬인쇄에" as
    # "새로 인쇄해", so no shop name was left and the model returned counterparty "명함" with item "명함" → the
    # confirm screen showed a wrong shop with 외상. A counterparty that is the same word as one of the item names
    # (ignoring spaces) is cleared, so MISSING_REQUIRED_FIELD asks "어느 집이에요?". Exact match only: a partial
    # match ("명함집" vs "명함") may be a real shop name. Looks only at the candidate, so it also runs with
    # normalization off (unlike Rules 2–5).
    if _counterparty_is_item_name(result):
        result["counterparty"] = None
        applied.append("counterparty_is_item_cleared")
    return result, applied


def _squash(name: Any) -> str:
    """Name without any whitespace, for comparing a counterparty with item names ("명 함" == "명함")."""
    return re.sub(r"\s+", "", name) if isinstance(name, str) else ""


def _counterparty_is_item_name(result: dict[str, Any]) -> bool:
    """Rule 6 condition: the counterparty equals an item name once spaces are dropped (both non-empty)."""
    counterparty = _squash(result.get("counterparty"))
    if not counterparty:
        return False
    return any(counterparty == _squash(item.get("name"))
               for item in result.get("items") or [] if isinstance(item, dict))


_WON_AMOUNT = re.compile(r"(\d{1,3}(?:,\d{3})+(?!\d)|\d+)\s*원")
# "사과 하나 3000원 3개": a price for one — not a Rule 2 marker yet (later candidate), but never restored as the total.
_PRICE_FOR_ONE = re.compile(r"(?<![가-힣])하나\s*\d")
# A Korean numeral + 원 normalization left alone ("8천원치": 치 is not an allowed particle) is another amount.
_KO_WON = re.compile(r"[일이삼사오육칠팔구십백천만억]\s?원")
# #162 리뷰: hidden unit-price phrasings Rule 5 never restores — a unit said twice ("2판 한판", "3마리 마리"),
# "각·각각·낱개·개별", and "1 + unit + amount" (normalized "한 포대 5만원" → "1포대 50000원", "1kg 3000원").
_QTY_UNITS = "|".join(re.escape(unit) for unit in UNITS if unit != "원")
_UNIT_WORD = re.compile(
    rf"(?<![가-힣A-Za-z])({_QTY_UNITS})(?=$|[^가-힣A-Za-z]|{'|'.join(UNIT_SUFFIXES)})", re.IGNORECASE)
_EACH = re.compile(r"(?<![가-힣])(?:각각?|낱개|개별)(?:로|에|씩)?(?![가-힣])")
_ONE_UNIT_AMOUNT = re.compile(rf"(?<![\d.,])1\s*(?:{_QTY_UNITS})\S{{0,2}}\s*[\d,]+\s*원", re.IGNORECASE)
# PM 10/16 허용 방식: a quantity said — digits + any word ("2마리", "1알"), a native numeral + a spaced word or a glued
# unit ("한 알", "세 톨"), "하나". Bare units ("봉지", "마리") come from _UNIT_WORD.
_NATIVE_NUMERAL = "한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무"
_QTY_EXPR = re.compile(
    rf"(?<![\d.,])\d+(?:\.\d+)?\s*(?!원)[가-힣A-Za-z]+"
    rf"|(?<![가-힣])(?:{_NATIVE_NUMERAL})(?:\s+[가-힣A-Za-z]+|(?:{_QTY_UNITS})(?![가-힣]))"
    r"|(?<![가-힣])하나(?:에|씩|만|요)?(?![가-힣])", re.IGNORECASE)


def _llm_gave_amount(candidate: dict[str, Any]) -> bool:
    """Whether the LLM output (before any rule) has a total, an item amount or an item unit price."""
    items = [item for item in candidate.get("items") or [] if isinstance(item, dict)]
    return candidate.get("total_amount") is not None or any(
        item.get("amount") is not None or item.get("unit_price") is not None for item in items)


def _one_spoken_amount(text: str) -> int | None:
    """The only amount said in the text ("N원" once, no other 천·만 numeral, no unit-price marker or "하나 N"), else None.

    Only in the allowed shape (one quantity right before it, or none; PM 10/16), and also None with a hidden unit
    price: a unit said twice, "각·각각·낱개·개별", or "1 + unit + amount" (#162 리뷰).
    """
    found = list(_WON_AMOUNT.finditer(text))
    if (len(found) != 1 or not _quantity_right_before(text, found[0].start())
            or _says_unit_price_for_amount(text) or _PRICE_FOR_ONE.search(text)
            or _EACH.search(text) or _ONE_UNIT_AMOUNT.search(text) or _unit_said_twice(text)):
        return None
    rest = _WON_AMOUNT.sub(" ", text)
    if _KO_WON.search(rest) or any(value >= 1000 for value in spoken_numbers(rest)):
        return None
    return int(found[0].group(1).replace(",", ""))


def _quantity_right_before(text: str, amount_start: int) -> bool:
    """Rule 5 allowed shape: no quantity said, or exactly one that ends right before the amount ("2마리 3500원")."""
    spans: list[list[int]] = []
    for start, end in sorted([m.span() for m in _QTY_EXPR.finditer(text)]
                             + [m.span() for m in _UNIT_WORD.finditer(text)]):
        if spans and start < spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    if not spans:
        return True
    return len(spans) == 1 and spans[0][1] <= amount_start and not text[spans[0][1]:amount_start].strip()


def _unit_said_twice(text: str) -> bool:
    """Whether one unit word appears twice or more ("2판 1판", "3마리 마리에") — a hint that one of them prices a unit."""
    units = [m.group(1).lower() for m in _UNIT_WORD.finditer(text)]
    return len(units) != len(set(units))


def _restore_spoken_total(result: dict[str, Any], amount: int, text: str) -> bool:
    """Rule 5: put the one spoken amount back as the total of a single item (in place); True when changed.

    The total is left alone when it is already a said number; the item amount is set only when missing or invented.
    """
    items = result.get("items") or []
    if len(items) != 1:
        return False
    said = _numbers(text) | spoken_numbers(text)
    total = result.get("total_amount")
    if total is not None and Decimal(str(total)) in said:
        return False
    result["total_amount"] = amount
    item_amount = items[0].get("amount")
    if item_amount is None or Decimal(str(item_amount)) not in said:
        items[0]["amount"] = amount
    return True


def _numbers(text: str) -> set[Decimal]:
    """Every number written in digits in the text ("12,600" → 12600)."""
    return {Decimal(m.group().replace(",", "")) for m in _NUMBER.finditer(text)}


# Rule 4 (PM 10/13): unit-price phrasings Rule 2's marker misses — "킬로당 만이천원"(not normalized after 당) and a
# unit with no quantity before it ("2.5킬로 킬로에 9500원"). Rule 4 only; Rule 2 keeps UNIT_PRICE_MARKER (follow-up).
_SPOKEN_UNIT_PRICE = re.compile(
    rf"\S+\s?당\s*[일이삼사오육칠팔구십백천만]|(?:^|\s)(?:{'|'.join(UNITS)})에\s*\d"
)


def _says_unit_price_for_amount(text: str) -> bool:
    """Whether a computed (qty×unit price) item amount is expected — Rule 4 leaves amounts alone then."""
    return _says_unit_price(text) or bool(_SPOKEN_UNIT_PRICE.search(text))


def _total_said_not_qty(total: Any, items: list[dict[str, Any]], said: set[Decimal]) -> bool:
    """Whether the total is one of the numbers said and not any item's quantity ("고등어 6마리" + total 6 → False)."""
    if total is None or Decimal(str(total)) not in said:
        return False
    return all(item.get("qty") is None or Decimal(str(item["qty"])) != Decimal(str(total)) for item in items)


def _stated_total(text: str) -> int | None:
    """The total said with 전부·총·합계 etc., only when said exactly once and no amount follows it."""
    found = list(_STATED_TOTAL.finditer(text))
    if len(found) != 1 or _AMOUNT.search(text, found[0].end()):
        return None
    return int(found[0].group(1).replace(",", ""))


# #98 리뷰 후속: "나머지" after a received amount or before a credit word — the stated total is not what is owed
# (Rule 3 leaves the total alone). "나머지 배추 2박스"(the rest of the stock) still counts for Rule 3.
# PR #128 리뷰: "남은" after a received word too ("2만원 받고 남은 건 다음에 외상 합계 3만원").
_REMAINDER = re.compile(r"받[가-힣]*\s.*(?:나머지|(?<![가-힣])남은)|나머지[가-힣]*\s*(?:외상|미수|달아)")


def _has_computed_amount_or_one_item(result: dict[str, Any], text: str) -> bool:
    """Rule 3 item condition: a single item, or an item amount that is none of the numbers said (computed)."""
    items = result.get("items") or []
    if len(items) == 1:
        return True
    said = _numbers(text) | spoken_numbers(text)
    return any(item.get("amount") is not None and Decimal(str(item["amount"])) not in said for item in items)


def _add(applied: list[str], rule: str) -> None:
    """Record a rule once, in the order it first applied."""
    if rule not in applied:
        applied.append(rule)


# Pack-size rule (PM 10/13): "20개입", and any other numeral + unit ("3팩", "2박스") — 원 is an amount, not a quantity.
# Later candidates (not counted yet): N입·N개들이·N구·N개짜리.
_PACK_SIZE = re.compile(r"(?<![\d.,])(\d+)\s?개입")
_OTHER_QTY = re.compile(rf"\d+(?:\.\d+)?\s?(?:{'|'.join(u for u in UNITS if u not in ('원', '개입'))})")


def _clear_pack_size_qty(result: dict[str, Any], llm: dict[str, Any], text: str) -> bool:
    """Clear (in `result`, in place) each qty that is only a pack size "N개입" and the amounts computed from it.

    `llm` is the candidate before any rule (same items), for the unit price the LLM multiplied by.
    Returns True when a qty was cleared. Not when the text has another quantity ("3팩") or says N elsewhere.
    """
    sizes = [Decimal(m.group(1)) for m in _PACK_SIZE.finditer(text)]
    if not sizes or _OTHER_QTY.search(_PACK_SIZE.sub(" ", text)):
        return False
    numbers = [Decimal(m.group().replace(",", "")) for m in _NUMBER.finditer(text)]
    only_sizes = {size for size in sizes if numbers.count(size) == sizes.count(size)}
    cleared = False
    computed: set[Decimal] = set()
    for item, llm_item in zip(result.get("items") or [], llm.get("items") or []):
        qty = item.get("qty")
        if qty is None or Decimal(str(qty)) not in only_sizes:
            continue
        item["qty"] = None
        cleared = True
        if llm_item.get("unit_price") is None:
            continue
        value = Decimal(str(qty)) * Decimal(str(llm_item["unit_price"]))
        if value in numbers:
            continue
        computed.add(value)
        if item.get("amount") is not None and Decimal(str(item["amount"])) == value:
            item["amount"] = None
    total = result.get("total_amount")
    if total is not None and Decimal(str(total)) in computed and _stated_total(text) is None:
        result["total_amount"] = None
    return cleared
