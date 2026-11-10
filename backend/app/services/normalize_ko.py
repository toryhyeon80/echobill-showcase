"""Korean numeral normalization before parsing (DL-05: numbers, units and spacing only).

Turns Korean numerals followed by a unit into Arabic digits ("삼만사천원" → "34000원",
"열두 개" → "12개") so the parser does not have to read them. Words without a unit
right after them — counterparty names such as "한빛상회" or "삼원상회" — are left alone.
Pure function: no I/O, no logging (utterances may contain PII, CLAUDE.md §3②).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

NORMALIZER_VERSION = "ko-num-1.5"

SINO_DIGITS = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "륙": 6, "칠": 7, "팔": 8, "구": 9}
DECIMAL_DIGITS = {**SINO_DIGITS, "영": 0, "공": 0}
SMALL_PLACES = {"십": 10, "백": 100, "천": 1000}
BIG_PLACES = {"만": 10_000, "억": 100_000_000}
PLACES = {**SMALL_PLACES, **BIG_PLACES}

# Attributive native numerals as used before a unit ("열두 개", "스무 포대").
NATIVE_TENS = {
    "열": 10, "스물": 20, "스무": 20, "서른": 30, "마흔": 40,
    "쉰": 50, "예순": 60, "일흔": 70, "여든": 80, "아흔": 90,
}
NATIVE_ONES = {
    "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5,
    "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9,
}

# Units that may follow a numeral. Left out on purpose because they collide with everyday
# words: 장(사장) 대(이대로) 부(두부) 번(이번) 회 포(육포) 분 명 차 말 되.
UNITS = tuple(sorted(
    "원 개 박스 상자 킬로 키로 kg 그램 g 리터 병 봉지 봉 팩 판 단 포기 포대 마리 모 자루 권 롤 접 "
    "묶음 세트 통 캔 근 망 송이 다발 짝 줄 개입 인분 그릇 잔 켤레 벌 곽 갑 톤".split(),
    key=len, reverse=True,
))
# Particles allowed right after a unit; anything else (e.g. "삼원상회", "구판장") blocks the match.
UNIT_SUFFIXES = tuple(sorted(
    "에 에다 씩 당 을 를 은 는 이요 요 으로 로 하고 랑 이랑 과 와 어치 짜리 정도 이고 만".split(),
    key=len, reverse=True,
))
# Total markers glued to an amount ("합삼만이천원", "총사만원"); a numeral right after one of
# these may start even though the previous character is Hangul (review v1.1 B2).
# Note: glued "합이만원" is read as 합 + 20000; spaced "합이 만원" stays "합이 10000원".
TOTAL_PREFIXES = ("합계", "도합", "합", "총")
# Numeral+unit spellings that are ordinary words.
# Checked only when numeral and unit are attached, so "콜라 두 통" still converts (PM review 2-5).
FORBIDDEN_WORDS = frozenset({
    "일단", "이모", "칠판", "사장", "이번", "이대로",
    "사망", "오판", "두통", "세모", "네모", "삼봉",
})
# A spaced "이 <unit>" is converted only for these Sino measure units; with native units
# ("이 박스", "이 병") 이 is usually the demonstrative "this" (PM review 2-5).
SINO_MEASURE_UNITS = ("킬로", "키로", "kg", "그램", "g", "리터", "톤")
# "N 단위 반" = N + 0.5 (PM 10/26: "배추 한 박스 반" was saved as 1박스), "반 단위" = 0.5. Same shapes as the DL-31
# voice answer.
# 반 must stand alone ("반품"·"반찬" are words) — "반은 외상"·"반이 현금" (half of it) are left alone by the lookahead
_HALF_AFTER_UNIT = re.compile(r" ?반(?=$|[\s,.?!]|이요|요|이고|씩|에|짜리)")
# "두 박스 반 외상 반 현금" — the halves split the payment, not the quantity
_HALF_SPLIT_PAYMENT = re.compile(r"반\s?(?:은|는)?\s?(?:외상|현금|카드|입금|이체|계좌)")
# A numeral never starts right after a closing bracket or ㈜ ("(주)한통" is a name).
CLOSERS = ")]}」』〉》）㈜"

_ARABIC = re.compile(r"\d{1,3}(?:,\d{3})+(?!\d)|\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Replacement:
    """One normalized span; start/end index the input text."""

    start: int
    end: int
    original: str
    normalized: str


@dataclass(frozen=True)
class NormalizationResult:
    """Normalized text plus the spans that were replaced."""

    text: str
    replacements: list[Replacement]


@dataclass(frozen=True)
class _Number:
    """A parsed numeral chunk: value, end index and what kind of numeral it was."""

    value: Decimal
    end: int
    kind: str  # "sino" (Korean digits lead), "arabic" (Arabic digits lead), "native"
    raw: str
    has_korean: bool
    has_comma: bool
    has_small: bool
    places: int  # count of 십·백·천·만·억; a unit-less Arabic amount needs 2+ ("1만2천")


def normalize_korean_numerals(text: str) -> NormalizationResult:
    """Replace Korean numerals followed by a unit (or 원) with Arabic digits.

    Input: utterance text (already sanitized). Output: normalized text and the replaced
    spans in input coordinates. Idempotent: normalizing the output changes nothing.
    """
    replacements: list[Replacement] = []
    i = 0
    while i < len(text):
        if not _is_start_boundary(text, i):
            i += 1
            continue
        match = _half_alone_at(text, i) or (None if _blocked_after_arabic(text, i) else _match_at(text, i))
        if match is None:
            number = _parse_sino_or_arabic(text, i) or _parse_native(text, i)
            # Skip the whole failed chunk so its tail is not read as a new numeral.
            i = number.end if number else i + 1
            continue
        replacements.append(match)
        i = match.end

    parts: list[str] = []
    cursor = 0
    for rep in replacements:
        parts.append(text[cursor:rep.start])
        parts.append(rep.normalized)
        cursor = rep.end
    parts.append(text[cursor:])
    return NormalizationResult(text="".join(parts), replacements=replacements)


def spoken_numbers(text: str) -> set[Decimal]:
    """Every numeral value in the text, normalized or not ("207만" → 2070000, "만오천" → 15000, "다섯" → 5).

    Read-only (the text is not changed): post-processing uses it to tell whether an amount was said at all.
    Every word start is read, so stray readings (a particle "이" → 2) only add values, never drop one.
    Spaced chunks are also read joined, as normalization joins them ("삼천 오백" → 3500 as well as 3000, 500).
    """
    values: set[Decimal] = set()
    for i in range(len(text)):
        if _is_start_boundary(text, i):
            number = _parse_sino_or_arabic(text, i) or _parse_native(text, i)
            if number is not None:
                values.add(number.value)
                values.add(_join_spaced_places(text, number).value)
    return values


def _match_at(text: str, start: int) -> Replacement | None:
    """Return the replacement for a numeral chunk starting at `start`, or None."""
    for number in (_parse_sino_or_arabic(text, start), _parse_native(text, start)):
        if number is None:
            continue
        number = _join_spaced_places(text, number)
        unit, unit_start, unit_end = _match_unit(text, number.end)
        if unit is None:
            # Only Arabic-led numbers with 2+ places or a comma are unambiguous without a unit
            # ("1만2천", "45,000"); a lone "3만" may be "only 3" (만 = only), so it stays.
            if number.kind == "arabic" and (number.places >= 2 or number.has_comma):
                return Replacement(start, number.end, text[start:number.end], _format(number.value))
            continue
        half_end = _half_end(text, unit_end) if unit != "원" else None
        if half_end is None and not _is_end_boundary(text, unit_end):
            continue
        if not _allowed(number, unit, spaced=unit_start > number.end, text=text, start=start):
            continue
        if half_end is not None:  # "한 박스 반" · "1박스 반" → "1.5박스"
            return Replacement(start, half_end, text[start:half_end], _format(number.value + Decimal("0.5")) + unit)
        if number.kind == "arabic" and not (number.has_korean or number.has_comma):
            return None  # already Arabic digits; leave spacing as written
        return Replacement(start, unit_end, text[start:unit_end], _format(number.value) + unit)
    return None


def _half_alone_at(text: str, start: int) -> Replacement | None:
    """"반 단위" with no number before it ("원두 반 봉지") → "0.5봉지" — the model answered 0 for it (PM #217 리뷰).
    Same shape as the DL-31 voice answer: the space is required, so glued "반대"·"반장"·"반찬" stay words."""
    if not text.startswith("반 ", start):
        return None
    unit, unit_start, unit_end = _match_unit(text, start + 1)
    if unit is None or unit == "원" or unit_start != start + 2 or not _is_end_boundary(text, unit_end):
        return None
    return Replacement(start, unit_end, text[start:unit_end], "0.5" + unit)


def _half_end(text: str, unit_end: int) -> int | None:
    """End of a "반" right after a unit ("박스 반" → N + 0.5), or None (반품·반찬, "반은 외상", "반 외상 반 현금")."""
    match = _HALF_AFTER_UNIT.match(text, unit_end)
    if match is None or _HALF_SPLIT_PAYMENT.search(text, match.start()):
        return None
    return match.end()


def _allowed(number: _Number, unit: str, *, spaced: bool, text: str, start: int) -> bool:
    """Apply the word-collision rules that the boundaries alone cannot catch."""
    if not spaced and (number.raw + unit) in FORBIDDEN_WORDS:
        return False
    if spaced and number.kind == "sino" and number.raw == "이" and unit.lower() not in SINO_MEASURE_UNITS:
        return False
    if number.kind == "native" and _follows_approximation(text, start, number.value):
        return False  # 지시서 이탈(안전 방향) D2: "한 두 개" is an approximation like 한두 (§3.3-5)
    if number.kind == "sino" and len(number.raw) == 1 and unit == "원" and number.raw in SINO_DIGITS:
        return False  # 일원·구원: under 10 won is unrealistic and collides with words
    return True


def _blocked_after_arabic(text: str, i: int) -> bool:
    """True when a Hangul numeral at `i` must not start because an Arabic number precedes it (#106).

    Glued ("30구판", "3일 박스") it is never a new numeral — places like "3만" are already part
    of the Arabic chunk. One space apart it counts only if the chunk has a place word
    (십·백·천·만·억, "2 오천원"), so "30 구 판당" does not become "30 9판당".
    """
    if i == 0 or not _is_hangul(text[i]):
        return False
    if text[i - 1].isdigit():
        return True
    if not (i >= 2 and text[i - 1] == " " and text[i - 2].isdigit()):
        return False
    number = _parse_sino_or_arabic(text, i) or _parse_native(text, i)
    return number is None or number.places == 0


def _follows_approximation(text: str, start: int, value: Decimal) -> bool:
    """True when the word before `start` (one space apart) is the next-lower native number."""
    if start < 2 or text[start - 1] != " ":
        return False
    for word, prev_value in NATIVE_ONES.items():
        begin = start - 1 - len(word)
        if (
            prev_value == value - 1
            and text.startswith(word, begin)
            and _is_start_boundary(text, begin)
        ):
            return True
    return False


def _is_start_boundary(text: str, i: int) -> bool:
    """A numeral may start at the text start, after whitespace, a digit or punctuation,
    or right after a total marker (합·총·합계·도합) that itself starts a word."""
    if i == 0:
        return True
    prev = text[i - 1]
    if prev in CLOSERS:
        return False
    if prev.isspace() or prev.isdigit() or not (_is_hangul(prev) or prev.isalpha()):
        return True
    return any(
        i >= len(prefix)
        and text.startswith(prefix, i - len(prefix))
        and _is_start_boundary(text, i - len(prefix))
        for prefix in TOTAL_PREFIXES
    )


def _is_end_boundary(text: str, i: int) -> bool:
    """After a unit: end, whitespace, digit, punctuation or an allowed particle."""
    if i >= len(text):
        return True
    nxt = text[i]
    if nxt.isspace() or nxt.isdigit() or not (_is_hangul(nxt) or nxt.isalpha()):
        return True
    return any(text.startswith(suffix, i) for suffix in UNIT_SUFFIXES)


def _match_unit(text: str, i: int) -> tuple[str | None, int, int]:
    """Longest unit at `i`, or after exactly one space. Returns (unit, unit_start, unit_end)."""
    for pos in (i, i + 1):
        if pos == i + 1 and not (i < len(text) and text[i] == " "):
            break
        for unit in UNITS:
            if text[pos:pos + len(unit)].lower() == unit:
                return text[pos:pos + len(unit)], pos, pos + len(unit)
    return None, i, i


def _parse_sino_or_arabic(text: str, start: int) -> _Number | None:
    """Parse "이만이천", "3만5천", "일점오", "45,000" style numerals starting at `start`."""
    tokens: list[tuple[str, Decimal]] = []
    j = start
    has_korean = has_comma = has_small = False
    places = 0
    arabic_led = start < len(text) and text[start].isdigit()
    fraction: Decimal | None = None
    while j < len(text):
        ch = text[j]
        if ch.isdigit():
            m = _ARABIC.match(text, j)
            has_comma = has_comma or "," in m.group()
            tokens.append(("coef", Decimal(m.group().replace(",", ""))))
            j = m.end()
        elif ch in SINO_DIGITS:
            # Arabic-led "3만이에요": 이 is a particle unless a place follows ("3만이천").
            if arabic_led and not (j + 1 < len(text) and (text[j + 1] in SMALL_PLACES or text[j + 1] in BIG_PLACES)):
                break
            tokens.append(("coef", Decimal(SINO_DIGITS[ch])))
            has_korean = True
            j += 1
        elif ch in SMALL_PLACES:
            tokens.append(("small", Decimal(SMALL_PLACES[ch])))
            has_korean = has_small = True
            places += 1
            j += 1
        elif ch in BIG_PLACES:
            tokens.append(("big", Decimal(BIG_PLACES[ch])))
            has_korean = True
            places += 1
            j += 1
        # "3점이요" is a score, so a decimal point is read only after Korean digits ("일점오").
        elif ch == "점" and not arabic_led and tokens and j + 1 < len(text) and text[j + 1] in DECIMAL_DIGITS:
            k = j + 1
            while k < len(text) and text[k] in DECIMAL_DIGITS:
                k += 1
            fraction = Decimal("0." + "".join(str(DECIMAL_DIGITS[c]) for c in text[j + 1:k]))
            has_korean = True
            j = k
            break  # "일점오" ends the numeral; places after a decimal are not supported
        else:
            break
    if not tokens:
        return None
    value = _combine(tokens)
    if value is None:
        return None
    if fraction is not None:
        value += fraction
    return _Number(
        value=value, end=j, kind="arabic" if arabic_led else "sino",
        raw=text[start:j], has_korean=has_korean, has_comma=has_comma,
        has_small=has_small, places=places,
    )


def _combine(tokens: list[tuple[str, Decimal]]) -> Decimal | None:
    """Fold coefficient/place tokens into a value; None if the sequence is not a numeral."""
    total = Decimal(0)
    section = Decimal(0)
    pending: Decimal | None = None
    last_small = last_big = Decimal("Infinity")
    for kind, val in tokens:
        if kind == "coef":
            if pending is not None:
                return None  # two coefficients in a row ("이삼") is not a number
            pending = val
        elif kind == "small":
            if val >= last_small:
                return None
            section += (pending if pending is not None else 1) * val
            pending, last_small = None, val
        else:
            if val >= last_big:
                return None
            block = section + (pending or 0)
            total += (block if block else 1) * val
            section, pending = Decimal(0), None
            last_small, last_big = Decimal("Infinity"), val
    return total + section + (pending or 0)


def _parse_native(text: str, start: int) -> _Number | None:
    """Parse attributive native numerals: 한·두·세·…, 열두, 스무, 서른두."""
    j = start
    value = 0
    tens = next((w for w in sorted(NATIVE_TENS, key=len, reverse=True) if text.startswith(w, j)), None)
    if tens:
        value += NATIVE_TENS[tens]
        j += len(tens)
    if tens != "스무":  # 스무 is only used on its own
        # 지시서 이탈(안전 방향) D3: speech/STT spaces compounds ("열 두"); join across one space.
        at = j + 1 if tens and text.startswith(" ", j) else j
        ones = next((w for w in sorted(NATIVE_ONES, key=len, reverse=True) if text.startswith(w, at)), None)
        if ones:
            value += NATIVE_ONES[ones]
            j = at + len(ones)
    if j == start:
        return None
    return _Number(
        value=Decimal(value), end=j, kind="native", raw=text[start:j],
        has_korean=True, has_comma=False, has_small=False, places=0,
    )


def _join_spaced_places(text: str, number: _Number) -> _Number:
    """Join "삼만 오천원" / "3천 5백원" / "1만 2천 5백원": a chunk ending in a place word, one space, a smaller chunk.

    Repeats while the joined chunk still ends in a place larger than the next chunk (#109: 천·백·십 too).
    A larger tail joins only as a 천·백·십 group said apart before 만·억 ("3천 5백만원", "2천 3백 4십 5만원",
    #109 2/2): the tail ends in 만·억 and has a 천·백·십 (or the chunk ends in 십), and the whole span read
    glued is one numeral — its value is read again from the glued span, so "1억 2천 5백만" is not 1억2천 + 5백만.
    Otherwise a larger tail is a separate number ("3천 5천원", "3만 5천 2만원", "3천 5만원").
    """
    start = number.end - len(number.raw)
    while True:
        place = PLACES.get(number.raw[-1])
        if place is None or not text.startswith(" ", number.end):
            return number
        tail = _parse_sino_or_arabic(text, number.end + 1)
        # The tail may be Arabic ("3만 5000원"); a smaller value plus 원 or a place is enough.
        if tail is None:
            return number
        tail_unit, _, _ = _match_unit(text, tail.end)
        # A plain Arabic tail of 100+ ("3만 5000", "3천 500") is unambiguous even without 원.
        arabic_tail = not tail.has_korean and tail.value >= 100
        if not (tail.has_small or tail_unit == "원" or arabic_tail):
            return number  # "3천 5개": the tail is a quantity
        value = number.value + tail.value
        if tail.value >= place:
            if (number.raw[-1] not in SMALL_PLACES or tail.raw[-1] not in BIG_PLACES
                    or not (tail.has_small or number.raw[-1] == "십")):
                return number
            glued = text[start:tail.end].replace(" ", "")
            whole = _parse_sino_or_arabic(glued, 0)
            if whole is None or whole.end != len(glued):
                return number  # "3만 5천 2만원", "5백 3천만원": not one numeral
            value = whole.value
        number = _Number(
            value=value, end=tail.end, kind=number.kind,
            raw=text[start:tail.end], has_korean=True,
            has_comma=number.has_comma or tail.has_comma, has_small=number.has_small or tail.has_small,
            places=number.places + max(tail.places, 1),  # a joined amount is unambiguous
        )


def _format(value: Decimal) -> str:
    """Integers without separators ("22000"), decimals trimmed ("2.5")."""
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def _is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣" or "ㄱ" <= ch <= "ㆎ"
