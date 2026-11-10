"""Digits → Korean words for 읽어 주기 (PM 11/2): "12,000원" → "만 이천 원", "3개까지" → "세 개까지".

Only the text sent to the TTS vendor changes; the screen keeps its digits. Units come from normalize_ko, which reads
the same words the other way. Pure function, no logging (the text holds counterparty names).
"""

import re

from app.services.normalize_ko import SINO_MEASURE_UNITS, UNITS

_SINO_DIGITS = "영일이삼사오육칠팔구"
_NATIVE_ONES = ("", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉")
_NATIVE_TENS = ("", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔")
_SMALL_PLACES = ((1000, "천"), (100, "백"), (10, "십"), (1, ""))
_BIG_PLACES = ("", "만", "억", "조")
# 원·무게 단위와 "2인분"·"10개입"은 한자어 수사로, 나머지 세는 말은 고유어 수사로 읽는다 ("세 박스")
_SINO_UNITS = {"원", "개입", "인분", *SINO_MEASURE_UNITS}
_NATIVE_UNITS = {*UNITS, "번째", "곳", "명", "건", "가지"} - _SINO_UNITS
_ALL_UNITS = sorted(_SINO_UNITS | _NATIVE_UNITS, key=len, reverse=True)
_MAX_READ = 10**16  # above 조 there is no place word; such a digit string is not an amount
# 낱말 일부("B2B")와 하이픈으로 이은 번호는 건드리지 않고, 이름("3단지", "1번가")은 세는 말로 읽지 않는다
_NUMBER = re.compile(
    r"(?<![A-Za-z\d.-])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?![\d-])"
    rf"(?:\s?(?!단지|번지|번가)({'|'.join(_ALL_UNITS)}))?"
)


def _sino(value: int) -> str:
    """Sino-Korean reading in spaced 만 groups: 15000 → "만 오천", 2500000 → "이백오십만"."""
    if value == 0:
        return "영"
    groups = []
    for index, big in enumerate(_BIG_PLACES):
        group = value // 10000**index % 10000
        if not group:
            continue
        words = [("" if digit == 1 and small else _SINO_DIGITS[digit]) + small
                 for place, small in _SMALL_PLACES if (digit := group // place % 10)]
        groups.insert(0, ("" if group == 1 and big == "만" else "".join(words)) + big)
    return " ".join(groups)


def _native(value: int, unit: str) -> str:
    """Native reading before a counter, 1–99: 3 → "세", 20 → "스무", 21 → "스물한", 1번째 → "첫"."""
    if value == 1 and unit == "번째":
        return "첫"
    if value == 20:
        return "스무"
    return _NATIVE_TENS[value // 10] + _NATIVE_ONES[value % 10]


def _read(match: re.Match[str]) -> str:
    """One number (and its unit, if any) as Korean words; a number that is not a quantity stays as written."""
    digits, fraction, unit = match.group(1).replace(",", ""), match.group(2), match.group(3)
    value = int(digits)
    if value >= _MAX_READ or (len(digits) > 1 and digits[0] == "0"):  # 010 … is a code, not a count
        return match.group(0)
    if unit in _NATIVE_UNITS and not fraction and 1 <= value <= 99:
        number = _native(value, unit)
    else:
        number = _sino(value)
    if fraction:
        number += " 점 " + " ".join(_SINO_DIGITS[int(digit)] for digit in fraction[1:])
    return f"{number} {unit}" if unit else number


def korean_numbers(text: str) -> str:
    """The sentence with its digits spelled as spoken Korean; everything else is unchanged."""
    return _NUMBER.sub(_read, text)
