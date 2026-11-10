"""Shared "paid / not paid yet / partial" phrase checks (pp-1.4, PM 10/8 · #82 리뷰 10/12).

Used by the parser post-processing (postprocess.py, AI path), the Rule fallback (parse_mock.py) and the
voice-answer credit field (voice_answer.py, DL-31). Pure regex checks on the text only — whether a phrase
decides the credit field is up to each caller (the voice answer fills only exact short answers).
"""

from __future__ import annotations

import re

from app.services.normalize_ko import normalize_korean_numerals

# Clear past-tense "paid" phrases —
# right after an amount or 돈: 받았어·받았습니다·받았고·받음·계산했어·들어왔어 ("9000원 받았어", "돈 받음";
# not "주문 받았어", "금액만 계산했어") / right after 돈 only: 줬어 ("돈 줬어"; "솔바람에 배추 줬어" is goods, and
# "…105300원 줬어" stays a follow-up — Seed v2 결제 유형 1) / after 결제·계산: 완료·끝(났어)·결제했어
# ("송금했어"·"입금 들어왔어" already have a payment word). The phrase must end the word ("받았어야" no) and not be
# negated after ("결제 완료 안 됨").
# PM #82 2차 리뷰 (10/12): an amount is a numeral + "원" ("3만원", "삼만 원", "30000원"; not "배추 3", "3박스"), and
# 돈 counts only as its own word — no Hangul right before it ("돈 줬어"; not 용돈·잔돈·쌈짓돈·목돈).
_NUMERAL = r"[\d일이삼사오육칠팔구십백천만억]"
_AFTER_WON = rf"(?:(?<={_NUMERAL}원)|(?<={_NUMERAL}원\s)|(?<={_NUMERAL}\s원)|(?<={_NUMERAL}\s원\s))"
_AFTER_MONEY = r"(?:(?<=(?<![가-힣])돈)|(?<=(?<![가-힣])돈\s))"
_AFTER_AMOUNT = rf"(?:{_AFTER_WON}|{_AFTER_MONEY})"
_PAID_PHRASE = re.compile(
    rf"(?:{_AFTER_AMOUNT}(?:받(?:았어요?|았습니다|았고|음)|계산\s?했어요?|들어왔어요?)|{_AFTER_MONEY}줬어요?"
    r"|(?<![가-힣])(?:결제\s?(?:완료|끝났어요?|했어요?)|계산\s?(?:완료|끝(?:났어요?)?)))"
    r"(?![가-힣])(?!\s*(?:안|못)\s?(?:됨|됐|돼|되|했|해))"
)
# later/promise/not-yet markers (PM 10/8): "나중에 받기로", "다음 주에 받음", "내일 준대", "아직 못 받았어"
_NOT_PAID_YET = re.compile(
    r"나중에|다음에|다음\s?달|다음\s?주|담주|이번\s?주|주말|모레|이따|내일|월말|말일|기로|준대|준다고|아직|중에|나머지"
    r"|(?<![가-힣])(?:안|못)\s?받"
)
# DL-30: partial payment — a received amount plus "중에·나머지", or "3000원만 받" — is neither paid nor credit
# ("9000원 중에 3000원 받았어", "3000원 받았고 나머지는 외상").
# #115: "받고·받구" count as received too, "나머진" as 나머지 ("2만원 받고 나머진 외상"), and "남은" when a credit
# word follows it, not negated ("2만원 받고 남은 건 다음에 외상"; not "남은 물건은 내일", "남은 건 외상 없음").
_REMAINDER_WORD = r"나머(?:지|진)"
_RECEIVED = r"받(?:았|음|고|구)"
_CREDIT_NOT_NEGATED = r"(?:외상|미수|달아)(?![가-힣]*\s?(?:없|아니|아님|안\s?(?:해|함|할|했)|말고))"
_PARTIAL_PAYMENT = re.compile(
    rf"(?:중에|{_REMAINDER_WORD}).*받(?:았|음)|{_RECEIVED}.*{_REMAINDER_WORD}|원\s?만\s?받"
    rf"|{_RECEIVED}.*(?<![가-힣])남은.*{_CREDIT_NOT_NEGATED}"
)
# PR #128 PM 요청: a clear partial payment — an amount right before 받고·받았고·받구 (만·은·는 allowed: "1000원만
# 받고"), then 남은·나머지, then a credit word not negated ("2만원 받고 남은 1만원 외상", "2만원 받았고 나머지 1만원은
# 다음 달에 외상"). DL-30 unsupported → asked to say it again. Not "주문 받고 나머지 배추 외상" (no amount before 받고)
# and not "현금 받고 나머지 외상": those stay a credit question only (_PARTIAL_PAYMENT).
_CLEAR_PARTIAL_PAYMENT = re.compile(
    rf"{_NUMERAL}\s?원(?:만|은|는)?\s?받(?:고|았고|구)(?:요)?(?![가-힣])"
    rf".*(?:(?<![가-힣])남은|{_REMAINDER_WORD}).*{_CREDIT_NOT_NEGATED}"
)
# PM 10/14: a clear partial payment without 남은·나머지 — two different amounts, one received or paid, one on credit.
# An amount is numerals + 원 ("20000원", "3만원"); amounts are told apart by their text (digits once normalized).
_NUMERALS = r"\d,일이삼사오육칠팔구십백천만억"
_AMOUNT = rf"(?<![{_NUMERALS}])([{_NUMERALS}]+)\s?원"
# (C2) an amount right before 받았·받고·받구·받음 ("20000원은 받았고", "20000원 현금으로 받고") — not right after
# 외상·외상값·미수 ("외상 20000원 받았어" is credit coming in, not this sale) — and a credit word anywhere
# ("3만원인데 2만원 받고 만원 외상", "외상 만원 하고 2만원은 받았어"). Not "주문 받고 배추 2만원 무 만원 외상".
_RECEIVED_AMOUNT = re.compile(
    rf"(?<!외상)(?<!외상\s)(?<!외상값\s)(?<!미수)(?<!미수\s){_AMOUNT}(?:만|은|는)?\s?"
    r"(?:(?:현금|현찰|카드|계좌)(?:으로|로)?\s?)?받(?:았|고|구|음)"
)
# (C3) each amount takes the payment/credit word right before it, else right after it ("현금 2만원 외상 만원",
# "카드 결제 2만원 외상 만원", "카드로 2만원 결제하고 만원은 외상"). A negated payment word does not count ("배추 만원
# 현금 아니고 외상 2만원"). "만원은 다음에 받기로" is credit too, only right after the amount ("무 만원어치는 다음에
# 주기로" is goods given later).
_PAY_WORD = r"(?:현금|현찰|카드|계좌|이체|송금)(?:으로|로)?(?:\s?결제)?(?![가-힣]*\s?(?:아니|아님|안\s?(?:돼|되|해|함)|말고))"
_CREDIT_WORD = rf"{_CREDIT_NOT_NEGATED}(?:으로|로)?"
_LATER_PAYMENT = r"(?:다음에|나중에|다음\s?달에)\s?(?:받기로|주기로)"
_AMOUNT_WITH_PARTICLE = re.compile(rf"{_AMOUNT}(?:은|는|만|씩)?")
_PAY_BEFORE = re.compile(rf"(?<![가-힣]){_PAY_WORD}\s?$")
_CREDIT_BEFORE = re.compile(rf"(?<![가-힣]){_CREDIT_WORD}\s?$")
_PAY_AFTER = re.compile(rf"\s?{_PAY_WORD}")
_CREDIT_AFTER = re.compile(rf"\s?(?:{_CREDIT_WORD}|{_LATER_PAYMENT})")
_CREDIT_SIDE = re.compile(rf"{_CREDIT_NOT_NEGATED}|[{_NUMERALS}]\s?원(?:은|는)?\s?{_LATER_PAYMENT}")


def _amount_key(raw: str) -> str:
    """An amount's text without commas, to tell amounts apart ("20,000" = "20000")."""
    return raw.replace(",", "")


def _says_received_amount_and_credit(text: str) -> bool:
    """(C2) "20000원 받고" + another amount + a credit side ("10000원 외상", "10000원은 다음에 받기로")."""
    if not _RECEIVED_AMOUNT.search(text):
        return False
    amounts = {_amount_key(m.group(1)) for m in re.finditer(_AMOUNT, text)}
    return len(amounts) >= 2 and bool(_CREDIT_SIDE.search(text))


def _says_paid_and_credit_amounts(text: str) -> bool:
    """(C3) one amount on the payment side and a different one on the credit side ("현금 2만원 외상 만원")."""
    paid: set[str] = set()
    credit: set[str] = set()
    for m in _AMOUNT_WITH_PARTICLE.finditer(text):
        key, before, after = _amount_key(m.group(1)), text[: m.start()], text[m.end():]
        if _PAY_BEFORE.search(before):
            paid.add(key)
        elif _CREDIT_BEFORE.search(before):
            credit.add(key)
        elif _PAY_AFTER.match(after):
            paid.add(key)
        elif _CREDIT_AFTER.match(after):
            credit.add(key)
    return bool(paid - credit) and bool(credit - paid)
# 회의 10/23 (seed2-135): only part of a sale received and no credit word — "98400원어치 팔고 그중에 32000원만 받았어".
# (C4) a received amount right after (그)중에 or as "N원만 받" (원 right before 만 — "삼만 받았어" is 3만 원 received),
# and another amount said is larger. One amount alone ("3000원만 받았어") stays a credit question. Not read: an amount
# without 원 ("십만원어치 팔고 삼만 받았어" — known limit, 회의 10/23).
_RECEIVED_VERB = r"받(?:았|고|구|음)"
_ONLY_RECEIVED = re.compile(rf"{_AMOUNT}\s?만\s?{_RECEIVED_VERB}")
# 중에 right after an amount ("9000원 중에", "98400원어치 그중에") or as 그중에 — not "회의 중에" (#198 리뷰).
_OUT_OF_RECEIVED = re.compile(
    rf"(?:원(?:어치)?\s?(?:그\s?)?|(?<![가-힣])그\s?)중에\s?[^,.]*?{_AMOUNT}(?:만|은|는)?\s?{_RECEIVED_VERB}"
)
# A discount is a full payment at a lower price, not part of a sale received ("3만원인데 깎아서 2만5천원만 받았어",
# "단골이라 25000원만 받았어" — #198 리뷰); same words as the post-processing discount check, plus 단골.
_DISCOUNT_WORD = re.compile(r"깎|할인|에누리|빼\s?(?:줬|주|드렸|드려)|단골")


def _amount_value(raw: str) -> int | None:
    """Won value of an amount's text ("32,000" → 32000, "삼만" → 30000), None when not a number."""
    key = _amount_key(raw)
    if not key.isdigit():
        key = normalize_korean_numerals(f"{key}원").text.removesuffix("원")
    return int(key) if key.isdigit() else None


def _says_only_part_received(text: str) -> bool:
    """(C4) a received amount ("N원만 받", "그중에 N원 받") smaller than another amount said."""
    if _DISCOUNT_WORD.search(text):
        return False
    received = [_amount_value(m.group(1)) for pattern in (_ONLY_RECEIVED, _OUT_OF_RECEIVED)
                for m in pattern.finditer(text)]
    amounts = [_amount_value(m.group(1)) for m in re.finditer(_AMOUNT, text)]
    return any(r is not None and any(a is not None and a > r for a in amounts) for r in received)


# PM #82 리뷰 (10/12): "아직" or "안/못 받" anywhere ("현금 아직 못 받았어", "계좌로 아직 못받음") — a payment word
# does not mean it was paid.
_NOT_RECEIVED = re.compile(r"아직|(?:안|못)\s?받")
# Already-paid verbs (PM 10/12 미래 표지): "주말에 현금으로 받았어", "내일 배달, 현금 받았어" — a time word there is not a
# later payment. "다음 주에 받음" stays a promise (받음 not listed); "못 받았어" is caught by _NOT_RECEIVED first.
_PAID_VERB = re.compile(r"받았|들어왔|줬|긁었|(?:결제|계산|송금|이체|입금)\s?했")
# "외상은 다음에 안 해 현금" — the marker sits inside a negated clause, not a later payment (pp-1.4 부정 보완 kept).
_LATER_NEGATED = re.compile(r"(?:나중에|다음에)\s*(?:안|못)\s?(?:해|하|할|함)")


def has_paid_phrase(text: str) -> bool:
    """True when a "paid" phrase (받았어·돈 받음·결제 완료·계산 끝…) appears — markers and credit words not checked."""
    return bool(_PAID_PHRASE.search(text))


def says_not_paid_yet(text: str) -> bool:
    """True when a later/promise/not-yet marker appears ("나중에", "다음 주에", "기로", "준대", "아직", "못 받")."""
    return bool(_NOT_PAID_YET.search(text))


def says_partial_payment(text: str) -> bool:
    """True when only part of the amount was received ("중에 3000원 받았어", "3000원 받았고 나머지는 외상",
    "3000원만 받았어", "2만원 받고 남은 건 외상"), and every clear partial payment."""
    return bool(_PARTIAL_PAYMENT.search(text)) or says_clear_partial_payment(text)


def says_clear_partial_payment(text: str) -> bool:
    """True for a clear partial payment ("2만원 받고 남은 1만원 외상", PM 10/14: "3만원인데 2만원 받고 만원 외상",
    "현금 2만원 외상 만원", 회의 10/23: "98400원어치 팔고 그중에 32000원만 받았어") — DL-30 unsupported, not just a
    credit question.

    Every such text is also a `says_partial_payment` text.
    """
    return (bool(_CLEAR_PARTIAL_PAYMENT.search(text)) or _says_received_amount_and_credit(text)
            or _says_paid_and_credit_amounts(text) or _says_only_part_received(text))


def says_not_received(text: str) -> bool:
    """True when the text says the money is not in yet ("아직", "안 받", "못받") — never 현금 then (PM #82)."""
    return bool(_NOT_RECEIVED.search(text))


def says_not_paid_now(text: str) -> bool:
    """True when the money is not in yet or only promised — "현금 아직 못 받았어", and a payment word with a
    later/promise marker ("현금 나중에", "계좌로 다음 주에 보낸대", "카드는 월말에"). Never 현금 then (PM 10/12).

    A marker next to an already-paid verb ("주말에 현금으로 받았어") or inside a negated clause ("외상은 다음에 안 해
    현금") does not count.
    """
    if says_not_received(text):
        return True
    rest = _LATER_NEGATED.sub(" ", text)
    return says_not_paid_yet(rest) and not _PAID_VERB.search(rest)
