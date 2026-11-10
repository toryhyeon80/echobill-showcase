"""Korean numeral normalization tests (작업지시서 A-2 §4).

Inputs are general patterns, not Golden sentences (Golden few-shot / copy is forbidden).
The Golden file is only used by the counterparty-preservation guard (§4.4).
"""

import json
from pathlib import Path

import pytest

from app.services.normalize_ko import NORMALIZER_VERSION, normalize_korean_numerals

GOLDEN_DEV = Path(__file__).resolve().parents[2] / "data" / "golden" / "golden_dev.jsonl"

CONVERTED = [
    ("콜라 세 박스 삼만육천원 외상", "콜라 3박스 36000원 외상"),
    ("배 열두 개 개당 천오백원", "배 12개 개당 1500원"),
    ("소고기 일점이킬로 킬로당 사만원", "소고기 1.2킬로 킬로당 40000원"),
    ("참외 한 망 만천원 현금", "참외 1망 11000원 현금"),
    ("쌀 스무 포대", "쌀 20포대"),
    ("휴지 서른두 롤", "휴지 32롤"),
    ("식용유 3만5천원", "식용유 35000원"),
    ("식용유 3만 5천원", "식용유 35000원"),
    ("식용유 삼만 오천원", "식용유 35000원"),
    ("연필 백 자루", "연필 100자루"),
    ("냉장고 이백오십만원", "냉장고 2500000원"),
    ("십오만원 카드", "150000원 카드"),
    ("사과 45,000원", "사과 45000원"),
    ("갈치 육마리 만이천원씩", "갈치 6마리 12000원씩"),
    ("묵 열한 모에 구천구백원", "묵 11모에 9900원"),
    ("만두 두 박스", "만두 2박스"),
    ("콜라만 두 박스", "콜라만 2박스"),
    ("계란 다섯 판에 이만원", "계란 5판에 20000원"),
    # §4.2 note: names and numerals in one sentence
    ("한빛상회에 콜라 세 박스", "한빛상회에 콜라 3박스"),
    ("삼원상회 사과 삼 킬로 오천 원", "삼원상회 사과 3킬로 5000원"),
    # §3.1 rows not covered by the table above
    ("소금 삼점이오킬로", "소금 3.25킬로"),
    ("합계 1만2천", "합계 12000"),
    # B1 (v1.1): an Arabic tail after a spaced 만/억 joins the same amount
    ("식용유 3만 5000원", "식용유 35000원"),
    ("식용유 2만 500원", "식용유 20500원"),
    ("식용유 3만 5000", "식용유 35000"),
    ("총 2만 500 외상", "총 20500 외상"),
    # 2-5 (1) PM review: spaced "이 <unit>" converts only for Sino measure units; attached stays as is
    ("고기 이 kg", "고기 2kg"),
    ("식용유 이 리터", "식용유 2리터"),
    ("콜라 이박스", "콜라 2박스"),
    # 2-5 (2) forbidden words apply only when numeral and unit are attached
    ("콜라 두 통", "콜라 2통"),
    ("두부 세 모", "두부 3모"),
    ("사과 네 모", "사과 4모"),
    # 2-5 (3) a closing bracket blocks only the name right after it
    ("(주)한통에 콜라 두 박스", "(주)한통에 콜라 2박스"),
    # code-review fixes must keep these: a Korean digit followed by a place, 원 after a digit
    ("식용유 3만이천원", "식용유 32000원"),
    ("볼트 천오원", "볼트 1005원"),
    # B5 (v1.2): "이 <unit>" follows the work order like any other numeral
    ("숙주 이 킬로", "숙주 2킬로"),
    ("배추 이킬로", "배추 2킬로"),
    # B2 (v1.1): total prefixes glued to the amount count as a start boundary
    ("양파 여섯 개 합삼만이천원", "양파 6개 합32000원"),
    ("총삼만원 외상", "총30000원 외상"),
    ("합계오천원", "합계5000원"),
    ("도합 만이천원", "도합 12000원"),
    # "합이 만원" spaced is "the total is 10000"; only the 만원 part is a number
    ("합이 만원", "합이 10000원"),
    # 지시서 이탈(안전 방향) D3: spoken/STT spacing inside a native number — "열 두 개" is 12;
    # without joining it became "열 2개" (a wrong value), see review v1.1
    ("배 열 두 개", "배 12개"),
    ("휴지 서른 두 롤", "휴지 32롤"),
    # #106: places right after an Arabic number belong to it; later numerals still convert
    ("3만이천원", "32000원"),
    ("1만2천원", "12000원"),
    ("3만 2천", "32000"),
    ("30구 한 판", "30구 1판"),
    # #106 (B안): one space after an Arabic number, a chunk with a place word still converts
    # (A안 "자릿수 단어로 시작할 때만"이었다면 그대로 남았을 문장)
    ("2 오천원", "2 5000원"),
    ("10 삼천원", "10 3000원"),
    ("2 이천원", "2 2000원"),
    ("500 이천원", "500 2000원"),
    # #109 (ko-num-1.3): a spaced smaller chunk after 천·백·십 joins too, repeatedly ("3천 5백원" was 500원)
    ("고등어 마리당 3천 5백원", "고등어 마리당 3500원"),
    ("3천 5백원", "3500원"),
    ("삼천 오백 원", "3500원"),
    ("1만 2천 5백원", "12500원"),
    ("3천 5백 2십원", "3520원"),
    ("2천 3백 4십 5원", "2345원"),
    ("천 오백원", "1500원"),
    ("3백 5십원", "350원"),
    ("3천 500원", "3500원"),
    ("3천 5백", "3500"),
    ("천 오백 개", "1500개"),
    # #109 PM 결정: like after 만·억, a one-digit tail joins when 원 follows it
    ("3천 5원", "3005원"),
    # #109 2/2 (ko-num-1.4): a 천·백·십 group said apart before 만·억 is one amount ("3천 5백만원" was 3천 5000000원)
    ("3천 5백만원", "35000000원"),
    ("3백 5십만원", "3500000원"),
    ("1억 2천 5백만원", "125000000원"),
    ("2천 3백 4십 5만원", "23450000원"),
    ("삼천 오백만 원", "35000000원"),
    # PM 10/26 (ko-num-1.5): "N 단위 반" = N + 0.5 ("배추 한 박스 반" was saved as 1박스)
    ("배추 한 박스 반 이만 원 외상", "배추 1.5박스 20000원 외상"),
    ("배추 1박스 반 20000원", "배추 1.5박스 20000원"),
    ("배추 두 박스반 삼만 원", "배추 2.5박스 30000원"),
    ("계란 한 판 반이요", "계란 1.5판이요"),
    ("대파 세 단 반씩", "대파 3.5단씩"),
    ("돼지고기 한 근 반 만오천 원", "돼지고기 1.5근 15000원"),
    # PM #217 리뷰: 숫자 없는 "반 단위" = 0.5 — 모델이 0으로 답해 400 AMOUNT_OUT_OF_RANGE가 났다
    ("소나무카페 원두 반 봉지 만 원 현금", "소나무카페 원두 0.5봉지 10000원 현금"),
    ("반 박스", "0.5박스"),  # v1(0cce18c)에선 "반"을 수사로 안 봐 그대로 두던 말 — PM 리뷰로 0.5
]

UNCHANGED = [
    "한빛상회에", "이화마트", "세종식자재", "두레마트", "삼원상회", "구판장", "천일수산",
    "만물상회", "칠성슈퍼", "육일상회", "사장님 외상", "일단 외상으로", "이모 외상", "육포",
    "칠판", "공장", "사과", "오리", "한두 박스", "이번에", "오늘", "세제 하나",
    # §3.3-4: a single Korean digit + 원 (under 10 won) is not converted
    "일원", "구원",
    # 지시서 이탈(안전 방향) D2: §3.3-5 approximations also when spaced ("한 두 개" = one or two)
    "한 두 개", "두 세 박스", "세 네 개",
    # B2 regression: names starting with a total prefix stay intact
    "총각김치집", "합정상회", "총무과", "합천상회에",
    # 2-5 (1) PM review: "이 <unit>" with a native unit is usually the demonstrative ("this box")
    "콜라 이 박스", "소주 이 병", "이 세트 하나", "고기 이 근",
    # 2-5 (2) ordinary words spelled like numeral + unit (attached)
    "사망", "오판", "두통", "세모", "네모", "삼봉로",
    # 2-5 (3) a closing bracket or ㈜ is not a word start
    "(주)한통", "㈜한통",
    # code-review 1: a particle 이 after an Arabic amount is not the digit 2 ("30002")
    "총 3만이에요", "3천이고", "2만이랑",
    # code-review 2: 점 after an Arabic number is a score/point, not a decimal point
    "3점이요",
    # code-review 3: a lone "3만" with no unit may be "only 3" (만 = only); 만큼 = as much as
    "사과 3만 주세요", "3만큼",
    # #106: a Hangul syllable glued to (or one space after) an Arabic number is not a digit
    # ("30구 판당" became "309판당", "3일 박스" became "31박스")
    "계란 30구 판당 7000원", "30구판", "30 구 판당", "3일 박스", "3일 판", "30구 2판",
    # #106 한계: spaced native numerals (no place word) after an Arabic number stay in Hangul
    "30 한 판", "계란 30 두 판", "2 열두 개", "3이천원",
    # #109: a tail that is a quantity (no place word, no 원) stays separate
    "3천 5개", "천 2개", "3천 5 박스",
]
# "반" that is not half a unit stays (PM 10/26): words starting with 반, half of the payment, a time, after 원
HALF_UNCHANGED = [
    ("배추 두 박스 반품", "배추 2박스 반품"),
    ("무 한 단 반찬용", "무 1단 반찬용"),
    ("배추 두 박스 반은 외상 반은 현금", "배추 2박스 반은 외상 반은 현금"),
    ("배추 두 박스 반 외상 반 현금", "배추 2박스 반 외상 반 현금"),
    ("세 시 반에 배추 두 박스", "세 시 반에 배추 2박스"),
    ("오만 원 반", "50000원 반"),
    ("반찬 두 통", "반찬 2통"),  # 띄어 쓴 "반 단위"만
    ("반 값에 두 박스", "반 값에 2박스"),  # 값은 단위가 아님
    ("절반 박스", "절반 박스"),
]


@pytest.mark.parametrize("text, expected", CONVERTED)
def test_converts_numerals_followed_by_a_unit(text: str, expected: str) -> None:
    assert normalize_korean_numerals(text).text == expected


@pytest.mark.parametrize("text", UNCHANGED)
def test_leaves_names_and_ordinary_words_alone(text: str) -> None:
    result = normalize_korean_numerals(text)
    assert result.text == text
    assert result.replacements == []


@pytest.mark.parametrize("text, _expected", CONVERTED)
def test_is_idempotent(text: str, _expected: str) -> None:
    once = normalize_korean_numerals(text).text
    assert normalize_korean_numerals(once).text == once


@pytest.mark.parametrize("text, _expected", CONVERTED)
def test_replacement_spans_point_at_the_input(text: str, _expected: str) -> None:
    result = normalize_korean_numerals(text)
    assert result.replacements
    for rep in result.replacements:
        assert text[rep.start:rep.end] == rep.original


def test_single_digit_arabic_tail_is_not_joined() -> None:
    """A one-digit tail after 만 may be a quantity ("3만 5 박스"), so it stays separate.

    v1.4 expected "30000 5 박스"; after code-review fix 3 a lone unit-less "3만" also stays,
    so the whole phrase is unchanged — the point of the test (no join) is the same.
    """
    assert normalize_korean_numerals("3만 5 박스").text == "3만 5 박스"
    assert normalize_korean_numerals("3만 5 박스").text != "35 박스"


@pytest.mark.parametrize("text, expected", [
    ("사과 3천원 5개", "사과 3000원 5개"),  # 원 between them: two values
    ("3천 5천원", "3천 5000원"),  # the tail is not smaller than the last place
    ("사과 3천 원 오백 원", "사과 3000원 500원"),
    # #109 2/2: before 만·억 only one numeral read glued, with a 천·백·십 in the tail (or 십 before it), joins
    ("3만 5천 2만원", "35000 20000원"),  # 만 twice: may be two amounts
    ("5백 3천만원", "5백 30000000원"),
    ("3천 5천만원", "3천 50000000원"),
    ("3천 5만원", "3천 50000원"),  # conservative: not read as 3005만
    ("3백 2만원", "3백 20000원"),  # conservative: not read as 302만
])
def test_spaced_chunks_that_are_not_one_amount_stay_separate(text: str, expected: str) -> None:
    """#109: only a smaller chunk with a place word, 100+ in digits, or 원 right after it joins."""
    assert normalize_korean_numerals(text).text == expected


@pytest.mark.parametrize("text", ["", "   ", "12345", "3.5", "2~3"])
def test_trivial_inputs_do_not_raise(text: str) -> None:
    assert normalize_korean_numerals(text).text == text


@pytest.mark.parametrize("text, expected", HALF_UNCHANGED)
def test_half_that_is_not_half_a_unit_stays(text: str, expected: str) -> None:
    assert normalize_korean_numerals(text).text == expected


def test_half_unit_reaches_the_parser_as_point_five() -> None:
    """PM 10/26: "한 박스 반" was saved as 1박스 — the Rule (and the LLM input) now read 1.5박스."""
    from decimal import Decimal

    from app.config import Settings
    from app.services.parse_pipeline import rule_parse

    item = rule_parse("가나상회 배추 한 박스 반 이만 원 외상", Settings(_env_file=None, app_env="development")).fields["items"][0]
    assert (item["qty"], item["unit"]) == (Decimal("1.5"), "박스")


def test_version_is_recorded() -> None:
    # 2-5 (4): tests reference the constant instead of a literal version string
    assert NORMALIZER_VERSION.startswith("ko-num-")


def _golden_records() -> list[dict]:
    with GOLDEN_DEV.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_golden_counterparty_names_survive_normalization() -> None:
    """§4.4 regression guard: zero counterparty names damaged (not tuning — Dev set only)."""
    damaged = []
    skipped = []  # name not verbatim in the utterance (e.g. a particle was dropped) — cannot check
    checked = 0
    for record in _golden_records():
        counterparty = (record.get("expected_json") or {}).get("counterparty")
        if not counterparty:
            continue
        text = record["utterance_text"]
        if counterparty not in text:
            skipped.append(record["id"])
            continue
        checked += 1
        if counterparty not in normalize_korean_numerals(text).text:
            damaged.append((record["id"], counterparty))
    # Review v1.1 B3: skips must stay visible and few, or the guard silently stops guarding.
    print(f"counterparty guard: checked={checked} skipped={skipped} damaged={damaged}")
    assert len(skipped) <= 3, f"too many unchecked Golden records: {skipped}"
    assert checked > 0
    assert damaged == [], f"counterparty names changed: {damaged}"
