"""DL-31 voice answer interpretation — one field, one short answer, rules only (no LLM).

BASELINE owned by AI (수경님): keep the interface `interpret(field, text, ...) -> AnswerResult`.
Approach (PM #72, 10/8): strip hesitation and politeness, then fill a value ONLY when the answer
exactly matches a short expected shape (외상: a fixed word list · 수량: number + known unit ·
총액: number + 원). Anything else fails and the FE shows buttons — DL-31 allows failures, never a
wrong value (10/12 판정: 해석 성공 90%+ and 0 wrong fills).
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.config import Settings, get_settings
from app.services.normalize_ko import normalize_korean_numerals
from app.services.payment_phrases import says_not_paid_yet
from app.services.postprocess import says_credit, says_payment

FIELDS = ("credit", "qty", "total", "counterparty")

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# said after a name: one polite ending, then one "to" particle ("박사장님한테요"). 에·로 are NOT stripped —
# real names end with them (종로·을지로), so "종로요" stays "종로"
_POLITE_TAIL = re.compile(r"(?:이에요|에요|예요|이요|이야|요|야|입니다)$")  # 반말 -야/-이야 = same as -요
_TO_TAIL = re.compile(r"(?:한테|에게)$")
# Shared "unsure" check for every field (PM #72 review, 130 short answers): any negation, guess,
# question, approximation or hesitation → fail and let the user tap a button. Merge with 수경님's
# shared negation helper later.
_UNSURE = re.compile(
    r"아니|아냐|아님|아뇨|말고|없어|없음|없는|하지\s?마|안\s?(?:해|하|했|할|돼|됐|되)|(?:^|\s)노(?:$|\s)"
    r"|미만|초과|넘어|넘게|이상|이하|아마|인가|일걸|일\s걸|같은데|같아|모르|몰라|글쎄|\?"
    r"|정도|쯤|대략|남짓|가량|(?:^|\s)약\s"
)
# fillers before an answer ("음 새벽상회", "어 그 김씨네") — the speaker is not sure
_FILLER = re.compile(r"^(?:그러니까|음+|어+|그+|저+|아+|흠+|에+)(?:\s|$)")
# DL-31: a counterparty answer is filled only if it looks like a shop/person name (10/14 this field is
# off; later: match against the user's own counterparty list). "배추" → fail, never a wrong fill.
_NAME_MARKERS = ("네", "씨", "님", "상회", "마트", "슈퍼", "가게", "식당", "집", "점", "상사", "농장", "수산",
                 "청과", "반찬", "마을", "공방", "카페", "소", "왕", "창고", "정육", "사장")
# not a shop name: yes/no as a whole first word ("예림상회" is still a name)
_YES_NO_WORDS = frozenset({"네", "예", "응", "아니", "아니요", "아뇨"})


def _unsure(text: str) -> bool:
    """True when the answer hedges, negates or asks — DL-31 allows failure, never a wrong value."""
    return bool(_UNSURE.search(text) or _FILLER.search(text.strip()))


@dataclass(frozen=True)
class AnswerResult:
    """ok → value (and unit for qty); failed → reason the FE can show before switching to buttons."""

    status: str  # "ok" | "failed"
    value: str | int | bool | None = None
    unit: str | None = None
    reason: str | None = None  # no_answer · ambiguous · no_number · out_of_range · not_in_choices · not_a_name


def _fail(reason: str) -> AnswerResult:
    return AnswerResult("failed", reason=reason)


# PM #72 (10/8): instead of blocking ever more phrases, fill a value only when the answer — fillers
# removed — EXACTLY matches a short expected shape. Everything else fails (the user taps a button).
_LEAD_FILLER = re.compile(r"^(?:그러니까|음+|어+|아+|그+|저+|흠+|에+)(?:[\s,]+|$)")  # "아홉" is not "아" + "홉"
_TAIL_FILLER = re.compile(r"[\s.!~]*(?:이에요|에요|예요|이요|이야|요|야|입니다)?[\s.!~]*$")
# 10/12 judge set (DL-31), mechanical cleanup for every field: STT punctuation (… ... , . !) becomes a space and a
# trailing hesitation ("외상이요 음") is dropped. "?" stays — a question fails ("외상?"). A dot between digits is a
# decimal point ("1.5킬로") and a comma between digits a thousands separator ("15,000원") — both stay.
# Leading fillers are handled per field (counterparty: "음 새벽상회" = unsure → fail).
_PUNCT = re.compile(r"[…!]|(?<!\d)[.,]|[.,](?!\d)")
_TRAIL_FILLER = re.compile(r"(?:\s+(?:그러니까|음+|어+|아+))+\s*$")


def _clean(text: str) -> str:
    """Punctuation → spaces, trailing hesitation dropped, spaces collapsed."""
    text = re.sub(r"\s+", " ", _PUNCT.sub(" ", text)).strip()
    return _TRAIL_FILLER.sub("", text).strip()


# 외상 칸 — the whole answer must be one of these exact phrases (PM #72 3rd: widened shape by shape).
# Built as head × tail so every listed form is an exact, complete answer — still no word spotting.
_CREDIT_HEADS = ("외상", "외상으로", "미수", "미수로", "장부에", "외상 장부에")
_CREDIT_TAILS = ("해 줘", "해 주세요", "해요", "할게요", "할게", "해 둬", "해 놔", "부탁해요", "달아 줘",
                 "달아 주세요", "달아 놔", "달아 둬", "달아 놔 줘", "달아", "적어 줘", "적어 주세요")
_CREDIT_ALONE = ("외상", "외상으로", "미수", "미수로", "달아", "달아 줘", "달아 주세요", "달아 놔", "달아 둬")
_PAY_HEADS = ("현금", "현금으로", "카드", "카드로", "계좌", "계좌로", "계좌이체", "계좌 이체", "계좌이체로",
              "이체", "이체로", "입금", "송금", "현찰", "현찰로")
# clear past tense only (pp-1.4, PM 10/8): 줬어·들어왔어 count only after a payment word (현금·계좌·입금…) or 돈
_PAY_TAILS = ("받았어", "받았습니다", "받음", "줬어", "줬습니다", "해 줬어", "했어", "했습니다", "됐어",
              "됐습니다", "왔어", "들어왔어")
_PAY_ALONE = ("현금", "현금으로", "카드", "카드로", "계좌", "계좌로", "계좌이체", "계좌 이체", "이체", "현찰", "현찰로")
_PAY_OTHER = ("결제 끝났어", "결제했어", "결제 됐어", "결제 완료", "계산 끝났어", "계산했어", "계산 됐어")
# --- Two answers decided by PM 10/12 (#90 review; asked 10/8). To change either later, edit ONLY these switches. ---
# ① "받았어요" · "돈 받았어" — a clear past "paid" without a payment word: PM 10/12 확정 → 현금 (False)
ACCEPT_PAID_WITHOUT_PAY_WORD = True
_PAID_WITHOUT_PAY_WORD = ("받았어", "다 받았어", "돈 받았어", "돈 줬어", "돈 들어왔어")
# ② hearsay "이체했대" · "이체해 줬대" after a payment word: PM 10/12 확정 → not a clear payment, fail
ACCEPT_HEARSAY_PAYMENT = False
_HEARSAY_PAY_TAILS = ("했대", "해 줬대")


def _key(phrase: str) -> str:
    """Comparison form: politeness/filler stripped (same as answers), spaces removed."""
    return re.sub(r"\s+", "", _core(phrase) or "")


# 수량 칸 — number + (known unit | nothing)
_QTY_UNITS = frozenset({"개", "박스", "상자", "킬로", "kg", "키로", "그램", "g", "단", "포기", "판", "팩", "병", "봉지",
                        "망", "마리", "모", "롤", "접", "통", "자루", "권", "세트", "봉", "묶음", "근", "줄", "장", "캔",
                        "봉투", "송이", "알", "대", "켤레", "벌", "짝", "포대", "가마", "톤", "다발"})
_QTY_SHAPE = re.compile(r"(\d+(?:\.\d+)?)\s*([가-힣A-Za-z]{0,3})")
# "반" only in two shapes (10/8 수경): "N 단위 반" → N+0.5 · "반 단위" → 0.5. "반값" · "반은 외상" fail.
# "반 단위" needs the space (PM #90): glued "반대"·"반장"·"반짝" are ordinary words, not 0.5대·0.5장·0.5짝
_HALF_AFTER = re.compile(r"(.*\S)\s*반")
_HALF_ALONE = re.compile(r"반\s+([가-힣A-Za-z]{1,3})")
# A bare number said without 원 or a unit: below this it is a quantity, from this up an amount (PM #72 review).
# Dev Golden: item amounts ≥ 3000, totals ≥ 9500, quantities ≤ 100 — 1000 sits between the two.
# qty "3000" (STT wrote an amount as digits) fails · total "3" (a quantity was said) fails
_BARE_NUMBER_SPLIT = 1000
# 수량 칸, number only (10/8 수경 (가)): native counting words "하나"–"아흔아홉" → value, unit left empty (FE adds the
# question's unit). Determiner forms (한·두·세·네·스무) are not here — "네" alone is "yes"
_NATIVE_ONES = ("", "하나", "둘", "셋", "넷", "다섯", "여섯", "일곱", "여덟", "아홉")  # index = value
_NATIVE_TENS = ("", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔")  # index = value / 10
_NATIVE_NUMBERS = {
    tens + ones: 10 * t + o
    for t, tens in enumerate(_NATIVE_TENS) for o, ones in enumerate(_NATIVE_ONES) if tens + ones
}
# 총액 칸 — number + (원 | nothing)
# the question is "전부 얼마예요?" — echoing 전부/합쳐서 is still an exact short answer
# "다 합해서"·"합해서": PM 10/12 — restored, dropped with the revert of 8cffdad (e93d8c5)
# "총액"·"총액은"·"총액이" (QA 10/13): must come before 총 (else "액 15000원" is left). 총액이 needs a space after it and
# bare 총액 a space or digit — glued "총액이만원" is either 총액이 만 원 (1만) or 총액 이만 원 (2만), so it fails
_TOTAL_PREFIX = re.compile(
    r"^(?:전부|합쳐서|다\s?해서|다\s?합해서|합해서|총액은|총액이(?=\s)|총액(?=\s|\d)|총|모두|합계)\s*"
)
_TOTAL_SHAPE = re.compile(r"(\d+)\s*(?:원)?")
# "사만 원이 맞아요" (합계 선택) — the confirming tail is part of the short shape
_RIGHT_TAIL = re.compile(r"\s*(?:이|가)?\s*맞(?:아|습니다|네)$")
# a run of Sino-Korean numeral words with spaces ("팔천 오백 원") — spaces are dropped before normalizing
_SPACED_SINO = re.compile(r"[일이삼사오육칠팔구십백천만억]+(?:\s+[일이삼사오육칠팔구십백천만억]+)+(?:\s*원)?")


def _core(text: str) -> str | None:
    """Strip leading hesitation and trailing politeness; None if the answer is a question."""
    if "?" in text:
        return None
    core = _TAIL_FILLER.sub("", _LEAD_FILLER.sub("", text.strip())).strip()
    return core or None


_CREDIT_EXACT = frozenset(
    {_key(f"{h} {t}") for h in _CREDIT_HEADS for t in _CREDIT_TAILS} | {_key(a) for a in _CREDIT_ALONE}
)
_PAYMENT_EXACT = frozenset(
    {_key(f"{h} {t}") for h in _PAY_HEADS
     for t in _PAY_TAILS + (_HEARSAY_PAY_TAILS if ACCEPT_HEARSAY_PAYMENT else ())}
    | {_key(a) for a in _PAY_ALONE} | {_key(o) for o in _PAY_OTHER}
    | {_key(o) for o in (_PAID_WITHOUT_PAY_WORD if ACCEPT_PAID_WITHOUT_PAY_WORD else ())}
)


def _credit(text: str) -> AnswerResult:
    core = _core(text)
    if core is None:
        return _fail("no_answer")
    if says_not_paid_yet(core):  # later/promise/not-yet marker (shared with pp-1.4) → neither credit nor paid
        return _fail("ambiguous")  # "월말에 받기로" · "외상 다음 주에" — a future payment, ask instead of guessing
    key = re.sub(r"\s+", "", core)
    if key in _CREDIT_EXACT:
        return AnswerResult("ok", value=True)
    if key in _PAYMENT_EXACT:
        return AnswerResult("ok", value=False)
    return _fail("ambiguous")  # "외상 아닌데" · "외상 달까" · "현금으로 할까" — not an exact answer


def _qty(text: str, settings: Settings) -> AnswerResult:
    core = _core(text)
    if core is None:
        return _fail("no_answer")
    alone = _HALF_ALONE.fullmatch(core)
    if alone:  # "반 박스" → 0.5박스; "반값" (값 is not a unit) fails
        if alone.group(1) not in _QTY_UNITS:
            return _fail("ambiguous")
        return AnswerResult("ok", value="0.5", unit=alone.group(1))
    after = _HALF_AFTER.fullmatch(core)
    if after:  # "세 단 반" → 3.5단; a unit is required ("세 반" · "절반" fail)
        whole = _qty_number(after.group(1), settings, half=True)
        return whole if whole.status == "failed" or whole.unit is not None else _fail("ambiguous")
    native = _NATIVE_NUMBERS.get(re.sub(r"\s+", "", core))
    if native is not None:  # "셋이요" → 3, unit empty (the FE adds the question's unit)
        return AnswerResult("ok", value=str(native), unit=None)
    return _qty_number(core, settings)


def _qty_number(core: str, settings: Settings, *, half: bool = False) -> AnswerResult:
    """number + (known unit | nothing) → value string; `half` adds 0.5 ("세 단 반")."""
    match = _QTY_SHAPE.fullmatch(normalize_korean_numerals(core).text)
    if not match:
        return _fail("ambiguous" if _NUMBER.search(core) or _NUMBER.search(normalize_korean_numerals(core).text)
                     else "no_number")  # "세 박스나 네 박스" · "세 개 더" · "박스요" (never guess 1)
    number, unit = match.group(1), match.group(2) or None
    if unit is not None and unit not in _QTY_UNITS:
        return _fail("ambiguous")  # "삼만 원" → 30000원 is a price, not a quantity
    try:
        qty = Decimal(number) + (Decimal("0.5") if half else 0)
    except InvalidOperation:
        return _fail("no_number")
    if not (0 < qty <= settings.qty_max):
        return _fail("out_of_range")
    if unit is None and qty >= _BARE_NUMBER_SPLIT:
        return _fail("ambiguous")  # "3000" without a unit is an amount STT wrote as digits, not a quantity
    value = str(int(qty)) if qty == qty.to_integral_value() else str(qty)  # "10", never "1E+1"
    return AnswerResult("ok", value=value, unit=unit)


def _total_amount(core: str) -> tuple[int, bool] | None:
    """Exact amount shapes only: [전부…] number [원] [이 맞아] → (amount, 원 said). None = not that shape."""
    body = _RIGHT_TAIL.sub("", _TOTAL_PREFIX.sub("", core)).strip()
    if _SPACED_SINO.fullmatch(body):
        body = re.sub(r"\s+", "", body)  # "팔천 오백 원" → "팔천오백원"
    for candidate in (body, f"{body}원"):  # "사만 구천" (no 원) is still an amount answer
        match = _TOTAL_SHAPE.fullmatch(normalize_korean_numerals(candidate).text)
        if match:
            return int(match.group(1)), body.endswith("원")
    return None


def _total(text: str, settings: Settings, choices: list[int] | None, *, question: bool = False) -> AnswerResult:
    core = _core(text)
    if core is None:
        return _fail("no_answer")
    parsed = _total_amount(core)
    if parsed is None:
        return _fail("ambiguous" if _NUMBER.search(normalize_korean_numerals(core).text) else "no_number")
    amount, said_won = parsed
    if question and (not said_won or _RIGHT_TAIL.search(core)):
        return _fail("ambiguous")  # "만 오천?" (no 원) · "삼만 원 맞아요?" (asks back to check) stay questions
    if not said_won and amount < _BARE_NUMBER_SPLIT:
        return _fail("ambiguous")  # "3" · "오백" without 원 is more likely a quantity than an amount
    if not (settings.amount_min_krw <= amount <= settings.amount_max_krw):
        return _fail("out_of_range")
    if choices is not None and amount not in choices:  # 산술 모순: must pick one of the two shown
        return _fail("not_in_choices")
    return AnswerResult("ok", value=amount)


def _counterparty(text: str, item_names: list[str] | None = None) -> AnswerResult:
    name = re.sub(r"[\s.,!?~]+", " ", text).strip()
    name = _TO_TAIL.sub("", _POLITE_TAIL.sub("", name)).strip()
    if not name:
        return _fail("no_answer")
    if item_names and re.sub(r"\s+", "", name) in {re.sub(r"\s+", "", i) for i in item_names if i}:
        # "배추요" when this trade's item is 배추 — the user named the item, not the shop.
        # Exact match only: "배추상회" is still a shop name.
        return _fail("not_a_name")
    if _NUMBER.search(normalize_korean_numerals(name).text) or len(name) > 20 or len(name.split()) > 3:
        return _fail("not_a_name")  # an amount or a whole sentence is not a shop name
    if _unsure(text) or says_credit(name) or says_payment(name) or name.split()[0] in _YES_NO_WORDS:
        return _fail("not_a_name")  # "외상이요" · "몰라요" · "솔바람 아니고 새벽상회" · "새벽상회인가"
    if not any(marker in name for marker in _NAME_MARKERS):
        return _fail("not_a_name")  # "배추" — without a name marker we cannot tell a shop from an item
    return AnswerResult("ok", value=name)


# PM 10/16: a short answer with one trailing "?" ("세 단이요?" · "만 오천 원이요?") is still an answer — qty and
# total only, with a unit / 원. "?" mid-sentence (two values), with a correction word, or on 외상·거래처 still fails.
_QUESTION_END = re.compile(r"\s*\?+$")


def _strip_question(field: str, text: str) -> tuple[str, bool]:
    """(text without the trailing "?", True) when PM 10/16 allows it; otherwise (text, False) unchanged."""
    if field not in ("qty", "total") or not _QUESTION_END.search(text):
        return text, False
    stripped = _QUESTION_END.sub("", text)
    if not stripped or "?" in stripped or _CORRECTION.search(stripped):
        return text, False  # "세 단이요? 네 단이요?" · "두 단 아니 세 단이요?" (correction + "?" is out of scope)
    return stripped, True


# 정정 (10/8 수경): exactly two values with exactly one 아니/아니요/아니고 between → the second value.
# "만 원 아니야" (no second value) · two corrections · either side not an exact answer → fail
_CORRECTION = re.compile(r"아니요|아니고|아니")


def _corrected(field: str, parts: list[str], choices: list[int] | None, item_names: list[str] | None,
               settings: Settings) -> AnswerResult:
    """"구천 원이요 아니 만 원" → 10000. Both sides must be exact answers for this field on their own."""
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return _fail("ambiguous")
    # the first value is only said, not chosen — it need not be one of the two choices
    first = _interpret_one(field, parts[0].strip(), None, item_names, settings)
    second = _interpret_one(field, parts[1].strip(), choices, item_names, settings)
    return second if first.status == "ok" and second.status == "ok" else _fail("ambiguous")


def _interpret_one(field: str, text: str, choices: list[int] | None, item_names: list[str] | None,
                   settings: Settings, *, question: bool = False) -> AnswerResult:
    if field == "credit":
        return _credit(text)
    if field == "qty":
        result = _qty(text, settings)
        if question and result.status == "ok" and result.unit is None:
            return _fail("ambiguous")  # "하나요?" (also the "-나요" question ending) · "3?" — a unit is required
        return result
    if field == "total":
        return _total(text, settings, choices, question=question)
    if field == "counterparty":
        return _counterparty(text, item_names)
    raise ValueError(f"unknown field: {field}")


def interpret(
    field: str,
    text: str,
    *,
    choices: list[int] | None = None,
    item_names: list[str] | None = None,
    settings: Settings | None = None,
) -> AnswerResult:
    """Interpret a transcript for one asked field. Answers about another field simply fail.

    `choices`: the two amounts shown for an arithmetic contradiction (total).
    `item_names`: this trade's item names — a counterparty answer equal to one of them fails.
    One correction ("A 아니 B") is allowed when A and B are both exact answers → B.
    One trailing "?" is allowed on qty (with a unit) and total (with 원) — PM 10/16.
    """
    settings = settings or get_settings()
    text = _clean(text or "")
    if not text:
        return _fail("no_answer")
    if field not in FIELDS:
        raise ValueError(f"unknown field: {field}")
    text, question = _strip_question(field, text)
    parts = _CORRECTION.split(text)
    if len(parts) > 1:
        return _corrected(field, parts, choices, item_names, settings)
    return _interpret_one(field, text, choices, item_names, settings, question=question)
