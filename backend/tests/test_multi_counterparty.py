"""S-11: two counterparties joined by 랑·이랑 are counted as two (MULTIPLE 400, no partial save)."""

import pytest

from app.config import Settings
from app.services.parse_mock import mock_parse_utterance
from app.services.parse_pipeline import rule_parse
from app.services.validation import validate_candidate


@pytest.mark.parametrize(
    "text",
    [
        "영수네랑 철수네한테 배추 2포기 6천원 외상",
        "김씨네랑 영수네 배추 2포기씩 외상",  # "~네" names without a marker
        "솔바람이랑 새벽상회에게 양파 3망 1만5천원 현금",
        "김씨랑 박씨에 사과 2박스 4만원 외상",  # "B에" followed by an item, not a price
        "김씨한테 배추 2포기 6천원 외상 박씨한테 무 3개 3천원 현금",  # existing marker count, unchanged
    ],
)
def test_two_counterparties_are_counted(text: str) -> None:
    assert mock_parse_utterance(text).counterparty_count == 2


@pytest.mark.parametrize(
    "text",
    [
        "솔바람 2호점에 대파 3단 9000원 현금",
        "김 사장님네 가게에 사과 1박스 2만원 외상",
        "두부랑 콩나물에 3천원 현금",  # "에" + price
        "두부랑 콩나물에 삼천원 현금",  # "에" + Korean-numeral price
        "주문하고 새솔마트에 사과 2박스 4만원 외상",
        "계산하고 솔바람한테 대파 3단 9000원 현금",
        "정리하고 김씨한테 사과 2박스 4만원 외상",
        # 하고 = 동사 연결 오탐 위험으로 제외(PM 확인) — 진짜 두 곳이어도 아직 1곳
        "김씨하고 박씨한테 사과 1박스씩 2만원씩 외상",
        "떡집사랑에 인절미 3팩 1만원 외상",  # 랑 inside the name (no space after)
        "하랑식품한테 설탕 1포대 2만원 외상",
        "사과 3킬로랑 배 2킬로 합쳐서 4만원 현금",  # item list
        "솔바람에 대파 한 단이랑 쪽파 두 단에 5천원 현금",  # unit words are not names
        "현금이랑 카드에 2만원",
        "오늘이랑 내일에 사과 2박스",
    ],
)
def test_one_counterparty_that_looks_like_two_stays_one(text: str) -> None:
    assert mock_parse_utterance(text).counterparty_count == 1


@pytest.mark.parametrize("normalize", [True, False])
def test_linked_counterparties_survive_numeral_normalization(normalize: bool) -> None:
    """The count runs on the normalized Rule input (/parse and eval), so both settings must agree."""
    settings = Settings(_env_file=None, app_env="development", ko_numeral_normalize=normalize)
    assert rule_parse("영수네랑 철수네한테 배추 두 포기씩 육천원씩 외상", settings).counterparty_count == 2
    assert rule_parse("두부랑 콩나물에 삼천원 현금", settings).counterparty_count == 1


def test_multiple_comes_before_missing_when_both_apply() -> None:
    """DL-03 order: a candidate missing the total still gets MULTIPLE when two counterparties are counted."""
    text = "솔바람이랑 새벽상회에 양파 3망씩 외상"
    candidate = {"counterparty": "솔바람", "items": [{"name": "양파", "qty": 3, "unit": "망", "unit_price": None,
                                                    "amount": None}], "total_amount": None, "credit": True}
    failure = validate_candidate(candidate, counterparty_count=mock_parse_utterance(text).counterparty_count)
    assert failure is not None
    assert (failure.error_code, failure.problem_fields, failure.partial_candidate) == (
        "MULTIPLE_COUNTERPARTIES_DETECTED", ["counterparty"], None)


# 다중 거래처 2차 (PM 10/8): "X에" 두 번 · 쉼표 이름 · "두 군데". 정규화 on/off 둘 다 같은 개수여야 한다
@pytest.mark.parametrize("normalize", [True, False])
@pytest.mark.parametrize(
    "text",
    [
        "솔바람에 배추 3박스 새벽상회에 무 2개",  # PM 1
        "솔바람, 새벽상회에 배추 3박스씩",  # PM 2
        "솔바람이랑 새벽상회 두 군데 배추 3박스씩",  # PM 3
        "솔바람한테 배추 3박스 새벽상회한테 무 2개",
        "솔바람에게 배추 세 박스 새벽상회에게 무 두 개",
        "솔바람에 배추 세 박스 새벽상회에 무 두 개 외상",
        "솔바람에 배추 3박스 새벽상회한테 무 2개",  # 에 + 한테
        "솔바람, 새벽상회한테 배추 3박스씩",
        "솔바람, 새벽상회에 각각 배추 3박스",
        "솔바람이랑 새벽상회 두 곳에 배추 3박스씩",
        "솔바람이랑 새벽상회 양쪽에 배추 3박스씩",
        "솔바람, 새벽상회 두 군데 배추 세 박스씩 외상",
        "김씨네랑 영수네 두 집에 사과 한 박스씩",
        "솔바람랑 새벽상회 2군데 배추 3박스씩",
    ],
)
def test_second_round_two_counterparties_are_counted(text: str, normalize: bool) -> None:
    settings = Settings(_env_file=None, app_env="development", ko_numeral_normalize=normalize)
    assert rule_parse(text, settings).counterparty_count == 2


@pytest.mark.parametrize("normalize", [True, False])
@pytest.mark.parametrize(
    "text",
    [
        # "에" 두 번
        "솔바람에 계란 한 판에 오천 원에 줬어",
        "솔바람에 계란 2판 한 판에 5000원",
        "시장에 갔다가 솔바람에 배추 3박스 외상",
        "도매시장에 갔다가 솔바람에 배추 3박스 외상",
        "도매시장에 들러서 3시에 솔바람에 배추 3박스",  # 시각은 수량이 아님
        "솔바람에 오전에 배추 3박스 오후에 무 2개",
        "솔바람에 배추 3박스 지난번에 무 2개 더",
        "솔바람에 배추 3박스 한번에 무 2개 줬어",
        "솔바람에 작은거에 3개 큰거에 2개",  # "에" 바로 뒤 수량 = 품목
        "솔바람한테 검은봉지에 사과 3개 외상",
        "솔바람에 검은봉지에 사과 3개 외상",
        "솔바람에 배추 삼킬로에 킬로당 만이천원",
        "솔바람에 배추 3박스 장부에 무 2개 달아줘",
        "솔바람에 배추 3박스 네 시에 무 2개",
        # 쉼표 = 품목 나열
        "대파, 쪽파에 만 원 솔바람",
        "대파, 쪽파에 만원 솔바람",
        "대파, 쪽파에 각각 2단씩 솔바람에 외상",  # 나누는 말은 있지만 "에" 뒤가 수량
        "배추, 무 솔바람에 3만원 외상",
        "배추, 무 솔바람한테 각각 2개씩",
        "양파, 마늘에 고추까지 3만원 솔바람에",  # 나누는 말 없음
        "그래서, 솔바람에 배추 3박스씩 외상",
        "네, 솔바람에 배추 3박스 외상",
        "그 솔바람 있잖아, 거기에 배추 3박스씩 외상",
        # 개수 표현이 품목 수량
        "배추랑 무 두 박스씩 솔바람에 외상",
        "배추랑 무 두 개씩 솔바람한테",
        "배추랑 무 둘 다 3천원씩 솔바람",
        "솔바람에 배추 두 박스 무 두 박스",
        "솔바람 2호점에 배추 3박스 외상",
    ],
)
def test_second_round_one_counterparty_stays_one(text: str, normalize: bool) -> None:
    settings = Settings(_env_file=None, app_env="development", ko_numeral_normalize=normalize)
    assert rule_parse(text, settings).counterparty_count == 1


@pytest.mark.parametrize(
    "text",
    [
        # 알려진 한계 (되묻기 쪽 오탐): 쉼표 품목 + 각각 + "에" 뒤에 다른 품목이 오면 이름 둘로 읽음
        "대파, 쪽파에 각각 고추 2단씩",
        # 알려진 한계: 사물 "~에" 뒤에 품목 수량이 따로 오면 거래처 둘로 읽음
        "솔바람한테 배추 3박스 검은봉지에 무 2개",
    ],
)
def test_second_round_known_limits_ask_again(text: str) -> None:
    """Current behavior, kept on purpose: two names → MULTIPLE 400 (asking again beats saving a wrong value)."""
    assert mock_parse_utterance(text).counterparty_count == 2


# 셀프 점검 후 (PM 10/8): 수사로 시작하는 상호·부름말·금액 낱말·사물 "~에"
@pytest.mark.parametrize("normalize", [True, False])
@pytest.mark.parametrize(
    "text",
    [
        # 이름 "전체"가 수량일 때만 뺀다 — 수사 + 세는 말로 시작하는 상호는 이름 (#77 "A랑 B" 규칙도)
        "한줄김밥이랑 솔바람한테 배추 3박스씩 3만원씩 외상",
        "두손푸드랑 새벽상회에 배추 3박스씩 3만원씩 외상",
        "삼봉상회랑 새벽상회네 배추 3박스씩 3만원씩 외상",
        "오장동냉면이랑 솔바람에 배추 3박스씩",
        "세봉식품이랑 솔바람한테 양파 1망씩",
        "한통상회, 솔바람한테 배추 3박스씩",
    ],
)
def test_names_starting_with_a_numeral_are_still_names(text: str, normalize: bool) -> None:
    settings = Settings(_env_file=None, app_env="development", ko_numeral_normalize=normalize)
    assert rule_parse(text, settings).counterparty_count == 2


@pytest.mark.parametrize("normalize", [True, False])
@pytest.mark.parametrize(
    "text",
    [
        # 쉼표 앞 부름말은 이름이 아님 (STT가 쉼표를 넣는 음성 입력)
        "여보, 솔바람네 배추 3박스 3만원 외상",
        "어머님, 솔바람에 배추 3박스씩 3만원 외상",
        "사장님, 솔바람한테 배추 3박스 3만원 외상",
        "저기요, 새벽상회에 무 3개씩 6천원 외상",
        "아저씨, 솔바람한테 배추 3박스 외상",
        "이모, 솔바람네 사과 2박스씩 현금",
        # 금액 낱말은 정규화 off에서도 이름이 아님
        "솔바람에 배추 3박스 만원에 무 2개 오천원에 외상",
        "솔바람에 배추 3박스 삼천원에 무 2개 줬어",
        # "X에" 두 번: 둘 다 가게 이름·한테·에게가 아니면 세지 않음 (사물·용도 "~에")
        "솔바람에 배추 3박스 상자에 무 2개 담아서 3만원 외상",
        "솔바람에 배추 3박스 트럭에 무 2개 실었어",
        "솔바람에 배추 3박스 냉장고에 무 2개 넣어놨어",
        "솔바람에 배추 3박스 김치용에 무 2개 3만원 외상",
        # 한 글자 끝말 집·점·네는 가게 이름으로 보지 않음
        "솔바람에 배추 3박스 우리집에 무 2개 가져갔어",
        "솔바람에 배추 3박스 옆집에 무 2개",
        "솔바람에 배추 3박스 편의점에 무 2개 맡겨놨어",
        "솔바람에 배추 3박스 창고점에 무 2개",
        "솔바람에 배추 3박스 언니네에 무 2개 줬어",
        # "양쪽·두 곳"은 A·B 중 가게 이름이 있을 때만 — 품목 둘이면 1곳
        "대파랑 쪽파 양쪽 다 2단씩 솔바람에 3만원 외상",
        "사과랑 배추 두 곳에 나눠 담아서 솔바람한테",
        # 쉼표 앞 수량·금액 낱말은 이름이 아님 (정규화 off)
        "배추 세 박스 만원, 솔바람한테 외상",
        "사과 다섯개, 솔바람한테 외상",
        "삼만원, 솔바람네 외상",
        # 쉼표 = 품목 나열 — 나누는 말(씩·각각·두 군데) 없으면 한테·에게·네여도 1곳
        "양파, 솔바람한테 3망 외상",
        "양파, 솔바람네 2망 현금",
        # 끝말과 같은 일반 낱말("마트"·"반찬" 단독)은 가게 이름이 아님
        "솔바람에 배추 3박스 반찬에 쓸 두부 3모 외상",
        "솔바람에 배추 3박스 마트에 무 2개 더 샀어",
        "솔바람에 밀가루 2포대 제과에 쓸 설탕 1포대",
        "솔바람에 배추 3박스 정육에 삼겹살 2근",
        "마트랑 정육점 두 군데 들렀다가 솔바람에 배추 3박스 외상",
    ],
)
def test_call_words_amount_words_and_object_places_are_not_names(text: str, normalize: bool) -> None:
    settings = Settings(_env_file=None, app_env="development", ko_numeral_normalize=normalize)
    assert rule_parse(text, settings).counterparty_count == 1


@pytest.mark.parametrize(
    ("text", "count"),
    [
        # 알려진 한계 (되묻기 쪽 오탐): 가게 이름 + 사물 "~에" + 품목 수량
        ("새벽상회에 배추 3박스 상자에 무 2개", 2),
        # 알려진 한계 (놓침, main과 같음): 가게 이름(두 글자 이상 끝말)·한테·에게 없는 두 이름
        ("솔바람에 배추 3박스 한빛에 무 2개", 1),
        ("솔바람에 배추 3박스 영수네에 무 2개", 1),  # 한 글자 끝말 "네"는 가게 이름으로 안 봄
        ("솔바람이랑 한빛 두 군데 배추 3박스씩", 1),
        # 알려진 한계 (놓침): 쉼표 이름에 나누는 말이 없으면 1곳 — "양파, 솔바람한테 3망" 품목 나열 오탐 방지의 대가
        ("솔바람, 새벽상회에게 배추 3박스 외상", 1),
        # 알려진 한계 (놓침, main과 같음): "수사 + 세는 말" 상호는 새 규칙에서 이름으로 안 봄
        ("세모랑 새벽상회 두 군데 배추 3박스씩", 1),
        ("한판, 새벽상회한테 계란 3판씩", 1),
        # 알려진 한계 (되묻기 쪽 오탐): 끝말 앞에 말이 붙은 일반 낱말 ("밑반찬")
        ("솔바람에 배추 3박스 밑반찬에 두부 3모", 2),
    ],
)
def test_self_check_known_limits(text: str, count: int) -> None:
    """Current behavior, kept on purpose so a change is noticed (PR 한계)."""
    assert mock_parse_utterance(text).counterparty_count == count


@pytest.mark.parametrize(
    ("text", "on", "off"),
    [
        # 이름 전체가 "수사 + 세는 말"인 상호도 #77 "A랑 B"에서는 이름 그대로 (main과 같음)
        ("세모랑 새벽상회에 배추 3박스씩", 2, 2),
        # 정규화 on은 이름을 "1판"·"3통"으로 바꿔 1곳 — main과 같음, 정규화 쪽 문제 (이 PR 범위 밖)
        ("한판이랑 솔바람한테 계란 3판씩 외상", 1, 2),
        ("삼통이랑 솔바람한테 김치 2통씩 현금", 1, 2),
    ],
)
def test_linked_names_that_look_like_quantities_stay_names(text: str, on: int, off: int) -> None:
    for normalize, expected in ((True, on), (False, off)):
        settings = Settings(_env_file=None, app_env="development", ko_numeral_normalize=normalize)
        assert rule_parse(text, settings).counterparty_count == expected


@pytest.mark.parametrize(
    ("text", "on", "off"),
    [
        # #77 "A랑 B에" 가격 판정은 main 그대로 — "B에 + 띄어 쓴 금액씩" 두 곳을 놓쳐 한 곳만 저장(200)하지 않게
        ("솔바람이랑 새벽상회에 오천 원씩 외상", 1, 2),
        ("솔바람이랑 새벽상회에 만 원씩 현금", 1, 2),
        ("김씨랑 박씨에 삼천 원씩 외상", 1, 2),
        # 알려진 한계 (main과 같음): 정규화 off 품목 "고기랑 상추에 오만 원"은 2곳 — 운영은 정규화 on이라 해당 없음
        ("고기랑 상추에 오만 원 외상 솔바람", 1, 2),
        ("고기랑 상추에 삼천 원 외상 솔바람", 1, 2),
    ],
)
def test_linked_price_check_stays_as_on_main(text: str, on: int, off: int) -> None:
    """정규화 on은 금액이 "5000원"이 되어 main도 1곳 (숫자 금액 "A랑 B에 5천원씩"은 main부터 놓침)."""
    for normalize, expected in ((True, on), (False, off)):
        settings = Settings(_env_file=None, app_env="development", ko_numeral_normalize=normalize)
        assert rule_parse(text, settings).counterparty_count == expected


# 표지(에·한테·랑·쉼표) 없이 가게 이름으로 거래를 이어 말한 두 곳 (PM 10/16). 운영과 같은 정규화 on Rule 입력으로 센다
_NORMALIZED = Settings(_env_file=None, app_env="development")


@pytest.mark.parametrize(
    "text",
    [
        "가나상회 배추 두 박스 만 원 외상 다라식당 대파 한 단 삼천 원 외상",  # 실기기 재현 문장
        "다라식당 대파 한 단 삼천 원 현금 가나상회 배추 두 박스 만 원 외상",
        "가나상회 배추 두 박스 만 원 외상 그리고 다라분식 대파 한 단 삼천 원 외상",
        "가나상회에 배추 두 박스 만 원 외상 다라식당 대파 한 단 삼천 원",  # 한 곳만 "에"
        "오늘 가나마트 우유 두 팩 오천 원 현금 다라상회 계란 한 판 육천 원 현금",
        "가나상회 배추 두 박스 만 원, 다라식당 대파 한 단 삼천 원 둘 다 외상",
        "가나상회 배추 세 박스 만 오천 원 외상 다라식당 대파 한 단 삼천 원 현금",  # STT형: 쉼표·"에" 없음
        "가나상회엔 배추 두 박스 만 원 외상 다라식당엔 대파 한 단 삼천 원 외상",
        "가나상회 배추 두 박스 만 원 외상 내일 다라식당 대파 한 단 삼천 원",  # 시간어 뒤
        "가나상회 배추 두 박스 만원 현금 또 다라마트 계란 한 판 육천원 현금",  # "또" 뒤
        # 수량 없이 금액만 말한 두 곳 (PM 10/26) — 가나상회 한 곳에 무까지 들어가 다라식당 무가 가나상회 외상으로 저장됐다
        "가나상회 배추 삼만 원 다라식당 무 이만 원 외상",
        "가나상회에 배추 삼만 원, 다라식당에 무 이만 원 외상",
        "가나상회 배추 삼만 원 외상 다라식당 무 이만 원 현금",
        "소나무카페 빨대 이만 원 별빛꽃집 장미 삼만 원 외상",  # 카페·꽃집 (PM 10/26 새 문장)
    ],
)
def test_two_shops_without_marker_are_counted(text: str) -> None:
    assert rule_parse(text, _NORMALIZED).counterparty_count == 2


@pytest.mark.xfail(strict=True, reason="알려진 한계: 수량 바로 뒤의 가게 이름은 세지 않는다 — 세면 "
                   "'두부 3모 바다푸드 어묵' 같은 브랜드 문장이 다중 거래처로 막혀 입력 불가가 된다")
def test_shop_right_after_a_quantity_is_a_known_gap() -> None:
    assert rule_parse("가나상회 배추 2박스 다라식당 대파 1단 합쳐서 13000원 외상", _NORMALIZED).counterparty_count == 2


@pytest.mark.xfail(strict=True, reason="알려진 한계: 표지 없는 이름엔 좁은 꼬리말(상회·식당·마트 등)만 쓴다 — 식품·푸드까지 "
                   "세면 '육천 원 바다푸드 어묵' 같은 금액 뒤 브랜드 문장이 다중 거래처로 막힌다")
def test_two_brand_like_shops_without_marker_are_a_known_gap() -> None:
    text = "다라식품 두부 세 모 육천 원 외상 바다푸드 어묵 두 봉지 사천 원 외상"
    assert rule_parse(text, _NORMALIZED).counterparty_count == 2


@pytest.mark.parametrize(
    "text",
    [
        "가나상회 배추 두 박스 만 원 외상 가나상회 대파 한 단 삼천 원 외상",  # 같은 이름 반복
        "가나상회 사장님한테 배추 두 박스 만 원 외상",
        "가나상회에 다라식품 두부 세 모 육천 원 외상",  # 브랜드
        "가나상회에 다라식품 두부 세 모 바다푸드 어묵 두 봉지 만 원 외상",  # 브랜드 두 개, 각자 수량
        "가나상회에 마트 봉투 열 장 이천 원 현금",  # 꼬리말만
        "가나상회 아니 다라식당에 배추 두 박스 만 원 외상",  # 정정
        "가나상회 배추 두 박스 만 원 외상 다라식당 거랑 같은 가격으로",
        "다라식품 두부 세 모 바다푸드 어묵 두 봉지 가나상회에 만 원 외상",
        "가나상회 분식용 떡 두 봉지 사천 원 외상",
        "가나상회 배추 두 박스 만 원 외상 다라식당 사장님이 가져감",  # "사장" ≠ 사(4)+장
        # 금액·쉼표 뒤 브랜드·품목 이름 (리뷰 10/16) — 표지 없는 이름엔 좁은 꼬리말만 쓴다
        "가나상회 두부 세 모 육천 원 바다푸드 어묵 두 봉지 사천 원 외상",
        "가나상회 배추 두 박스 만 원, 다라식품 두부 세 모 육천 원 외상",
        "가나상회 두부 세 모 육천 원 밑반찬 두 팩 팔천 원 외상",
        "가나상회에 두부 세 모 육천 원 멸치반찬 두 팩 팔천 원 현금",
        "가나상회한테 두부 세 모 육천 원 바다푸드 어묵 두 봉지 사천 원 외상",
        "바다푸드 어묵 두 봉지 사천 원 다라식품 두부 세 모 육천 원 가나상회에 외상",
        "어제 가나상회 배추 두 박스 만 원 오늘 가나상회 대파 한 단 삼천 원 외상",  # 같은 이름, 시간어 뒤
        "가나상회 배추 두 박스 만 원 외상 다라식당 갈 거",  # 수량 없음
        "가나상회 배추 삼만 원 외상",  # 금액만, 한 곳
        "가나상회 배추 삼만 원 외상 다라식당 거랑 같은 가격으로",  # 다라식당 뒤에 금액 없음
        "가나상회 두부 육천 원 바다푸드 어묵 사천 원 외상",  # 금액 뒤 브랜드 (좁은 꼬리말만)
        "소나무카페 원두 두 봉지 이만 원 외상",
        "가나상회 커피 두 박스 이만 원 외상 소나무카페 거랑 같은 거",  # 카페 뒤에 수량·금액 없음
    ],
)
def test_one_shop_with_shop_like_words_stays_one(text: str) -> None:
    assert rule_parse(text, _NORMALIZED).counterparty_count == 1


def test_two_shops_without_marker_get_multiple_400() -> None:
    """The count reaches validation: MULTIPLE first, so 가나상회 is never saved with 다라식당's 대파."""
    text = "가나상회 배추 두 박스 만 원 외상 다라식당 대파 한 단 삼천 원 외상"
    result = rule_parse(text, _NORMALIZED)
    failure = validate_candidate(result.fields, counterparty_count=result.counterparty_count,
                                 item_count=result.item_count)
    assert failure is not None and failure.error_code == "MULTIPLE_COUNTERPARTIES_DETECTED"
