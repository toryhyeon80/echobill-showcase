"""Parse API tests — candidate invariant, persistence, DL-03 400 responses."""

import uuid

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models import LedgerEntry

client = TestClient(app)

VALID_UTTERANCE = "김씨에게 배추 3박스 외상 30000원"


def _auth_header(user_id: str = "dev-user-1") -> dict[str, str]:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": user_id}).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())}


def _parse(text: str, user_id: str = "dev-user-1"):
    return client.post(
        "/api/v1/ledger/parse", json={"utterance_text": text}, headers=_auth_header(user_id)
    )


def test_parse_requires_auth() -> None:
    """Unauthenticated parse → 401."""
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": VALID_UTTERANCE})
    assert res.status_code == 401


def test_parse_returns_candidate_only() -> None:
    """Mock parse always returns status=candidate (never confirmed)."""
    res = _parse(VALID_UTTERANCE)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "candidate"
    assert body["parser_type"] == "mock"
    assert body["counterparty"] == "김씨"
    assert body["items"][0]["name"] == "배추"
    assert body["items"][0]["unit"] == "박스"
    assert body["total_amount"] == 30000
    assert body["credit"] is True


def test_parse_stores_candidate_for_jwt_user(db: Session) -> None:
    """200 stores one candidate row owned by the token user, keyed by public entry_id."""
    body = _parse(VALID_UTTERANCE, user_id="owner-a").json()
    rows = db.scalars(select(LedgerEntry)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.entry_id == body["entry_id"]
    assert row.user_id == "owner-a"
    assert row.status == "candidate"
    assert row.total_amount == 30000
    assert row.items[0]["name"] == "배추"
    assert str(row.id) != body["entry_id"]  # 정수 PK는 노출하지 않는다 (DL-23)


def test_parse_missing_qty_returns_400_with_partial(db: Session) -> None:
    """Qty is never invented as 1 → 400 MISSING_REQUIRED_FIELD, nothing stored."""
    res = _parse("김씨에게 배추 외상 30000원")
    assert res.status_code == 400
    body = res.json()
    assert body["error_code"] == "MISSING_REQUIRED_FIELD"
    assert body["problem_fields"] == ["items[0].qty"]
    assert body["partial_candidate"]["items"][0]["qty"] is None
    assert body["partial_candidate"]["total_amount"] == 30000
    assert db.scalars(select(LedgerEntry)).all() == []


def test_parse_multiple_counterparties_returns_400_without_partial(db: Session) -> None:
    """Two counterparties → 400, no candidate (기획서 §5.1 후보 생성 보류)."""
    res = _parse("김씨한테 배추 3박스 영수한테 사과 2박스 외상 30000원")
    assert res.status_code == 400
    body = res.json()
    assert body["error_code"] == "MULTIPLE_COUNTERPARTIES_DETECTED"
    assert body["problem_fields"] == ["counterparty"]
    assert body["partial_candidate"] is None
    assert db.scalars(select(LedgerEntry)).all() == []


def test_parse_input_too_long_returns_400() -> None:
    """Over 500 chars → 400 INPUT_TOO_LONG (not 422), also far past the old 2000 schema cap."""
    for length in (501, 2001):
        res = _parse("가" * length)
        assert res.status_code == 400
        assert res.json()["error_code"] == "INPUT_TOO_LONG"
        assert res.json()["problem_fields"] == ["utterance_text"]


def test_parse_unit_price_is_not_taken_as_total() -> None:
    """Several 원 amounts → item amount stays null, last one is the total (no invented values)."""
    body = _parse("김씨에게 배추 3박스 박스당 10000원 총 30000원 외상").json()
    assert body["total_amount"] == 30000
    assert body["items"][0]["amount"] is None


def test_parse_missing_item_name_returns_400() -> None:
    """A unit word is not an item name → 400 with items[0].name."""
    res = _parse("김씨에게 3박스 외상 30000원")
    assert res.status_code == 400
    assert res.json()["problem_fields"] == ["items[0].name"]


def test_parse_rejects_legacy_field_name() -> None:
    """Old scaffold key `utterance` is not accepted (DL-01) → 422."""
    res = client.post(
        "/api/v1/ledger/parse",
        json={"utterance": VALID_UTTERANCE},
        headers=_auth_header(),
    )
    assert res.status_code == 422


MANUAL_CANDIDATE = {
    "counterparty": "김씨",
    "items": [{"name": "배추", "qty": 3, "unit": "박스", "unit_price": 10000, "amount": 30000}],
    "total_amount": 30000,
    "credit": True,
}


def _manual(candidate: dict):
    return client.post(
        "/api/v1/ledger/parse",
        json={"utterance_text": "김씨 배추", "candidate": candidate},
        headers=_auth_header(),
    )


def test_manual_candidate_is_validated_and_stored(db: Session) -> None:
    """DL-03 3-3: corrected values skip the parser and are stored as parser_type=manual."""
    res = _manual(MANUAL_CANDIDATE)
    assert res.status_code == 200
    body = res.json()
    assert body["parser_type"] == "manual"
    assert body["status"] == "candidate"
    assert body["counterparty"] == "김씨"
    stored = db.scalars(select(LedgerEntry)).one()
    assert stored.parser_type == "manual"
    assert stored.status == "candidate"


def test_manual_candidate_still_gets_dl03_400(db: Session) -> None:
    bad = {**MANUAL_CANDIDATE, "total_amount": 12345}
    res = _manual(bad)
    assert res.status_code == 400
    assert res.json()["error_code"] == "AMOUNT_MISMATCH"
    assert db.scalars(select(LedgerEntry)).first() is None


def test_manual_candidate_with_four_items_is_400_not_422() -> None:
    item = MANUAL_CANDIDATE["items"][0]
    res = _manual({**MANUAL_CANDIDATE, "items": [item] * 4, "total_amount": 120000})
    assert res.status_code == 400
    assert res.json()["error_code"] == "EXCEED_MAX_ITEMS"


def test_mock_sanitize_does_not_merge_digits_around_removed_symbols() -> None:
    """Removing "~" or emoji between digits must not turn "2~3" into 23 (same rule as parse_llm)."""
    from app.services.parse_mock import _sanitize_utterance

    assert _sanitize_utterance("사과 2~3박스") == "사과 2 3박스"
    assert _sanitize_utterance("무 2\U0001F34E3개") == "무 2 3개"


def test_parse_reads_mang_as_a_unit() -> None:
    """PM 10/2: "1망" is a quantity + unit, so the Rule parser no longer answers 400 (missing qty)."""
    res = _parse("솔바람한테 양파 1망 5000원 외상")
    assert res.status_code == 200
    item = res.json()["items"][0]
    assert (item["name"], float(item["qty"]), item["unit"]) == ("양파", 1.0, "망")


def test_mock_keeps_item_names_that_start_with_mang() -> None:
    """Adding "망" must not drop "망고" from the item name candidates."""
    from app.services.parse_mock import mock_parse_utterance

    item = mock_parse_utterance("솔바람한테 망고 2박스 3만원 현금").fields["items"][0]
    assert (item["name"], item["unit"]) == ("망고", "박스")


def test_mock_counterparty_count_is_unchanged_by_mang() -> None:
    """The LLM path borrows the Rule counterparty_count (#28) — still 1 for these sentences."""
    from app.services.parse_mock import mock_parse_utterance

    for text in ("솔바람한테 양파 1망 5000원 외상", "솔바람한테 망고 2박스 3만원 현금"):
        assert mock_parse_utterance(text).counterparty_count == 1


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("솔바람한테 단무지 3팩 6000원 현금", "단무지"),
        ("솔바람한테 접시 5개 1만원 현금", "접시"),
        ("솔바람한테 롤케이크 2개 1만2천원 현금", "롤케이크"),
        ("솔바람한테 개떡 10개 1만원 현금", "개떡"),
        ("솔바람한테 병아리콩 2봉지 8000원 현금", "병아리콩"),
        ("솔바람한테 사과 3박스 박스당 2만원 현금", "사과"),
    ],
)
def test_mock_keeps_item_names_that_start_with_a_unit(text: str, name: str) -> None:
    """PM #38 리뷰: only a unit itself or "unit+당" is dropped from item name candidates."""
    from app.services.parse_mock import mock_parse_utterance

    assert mock_parse_utterance(text).fields["items"][0]["name"] == name


@pytest.mark.parametrize(
    "text",
    ["솔바람한테 양파 2망 9000원 계좌로 받았어", "솔바람한테 양파 2망 9000원 이체했어", "솔바람한테 양파 2망 9000원 계좌이체"],
)
def test_mock_reads_account_transfer_as_paid(text: str) -> None:
    """PM 승인 10/2: 계좌·이체 are immediate payment words (credit false)."""
    from app.services.parse_mock import mock_parse_utterance

    assert mock_parse_utterance(text).fields["credit"] is False


@pytest.mark.parametrize(
    ("text", "credit"),
    # both words → null. pp-1.3.1: "외상 아니다 현금" became payment only ("아니다" negates, PM 10/8) → "외상 반 현금 반"
    [("솔바람한테 카드지갑 2개 1만원", None), ("솔바람한테 양파 2망 9천원 외상 반 현금 반", None),
     ("솔바람한테 양파 2망 9천원 외상", True), ("솔바람한테 양파 2망 9천원 계좌이체", False),
     # pp-1.3.1: a negated word is not counted — never filled from it, the other side alone decides
     ("솔바람한테 양파 2망 9천원 외상 아니고 현금", False), ("솔바람한테 양파 2망 9천원 외상 아니다 현금", False), ("솔바람한테 양파 2망 9천원 외상 안 해", None),
     ("솔바람한테 양파 2망 9천원 달아놓지 마", None), ("솔바람한테 양파 2망 9천원 현금 없어서 달아 줘", True),
     # pp-1.4 (PM 10/8): paid phrase = payment word, but not with a credit word or a later form; glued 외상 counts
     ("솔바람한테 양파 2망 9천원 받았어", False), ("솔바람한테 양파 2망 9천원 결제 완료", False),
     ("솔바람한테 양파 2망 9천원 외상으로 받았어", True), ("솔바람한테 양파 2망 9천원 나중에 받기로", None),
     ("솔바람한테양파두망구천원외상", True),
     # pp-1.4 부정 보완 (PM 10/8): 아니라 · 떨어진 "안 해" · 노노, 이름 속 "아닌"은 부정 아님
     ("솔바람한테 양파 2망 9천원 외상이 아니라 현금", False), ("솔바람한테 양파 2망 9천원 외상은 다음에 안 해 현금", False),
     ("솔바람한테 양파 2망 9천원 외상 노노", None), ("아닌상회 배추 3포기 9천원 외상", True),
     # 부분 결제 → 확인 (DL-30, PM 10/8)
     ("솔바람한테 양파 2망 9천원 중에 3천원 받았어", None), ("솔바람한테 양파 2망 9천원 3천원 받았고 나머지는 외상", None),
     # #115: 받고 … 남은/나머지
     ("솔바람 양파 3박스 2만원 받고 남은 건 외상 합계 3만원", None), ("솔바람 양파 3박스 2만원 받고 나머지 외상 합계 3만원", None),
     ("솔바람 양파 3박스 2만원 받고 남은 1만원 외상 합계 3만원", None),
     ("솔바람 양파 3박스 2만원 받고 남은 건 다음에 외상 합계 3만원", None),
     # PM #82 리뷰 (10/12): 아직·안/못 받 → 결제 단어가 있어도 현금 아님 (확인), 외상은 그대로
     ("솔바람 배추 3박스 3만원 현금 아직 못 받았어", None), ("솔바람 배추 3박스 3만원 현금 아직 안 받았어", None),
     ("솔바람 배추 3박스 3만원 계좌로 아직 못받음", None), ("솔바람 배추 3박스 3만원 카드는 아직이요", None),
     ("솔바람 배추 3박스 3만원 현금 못 받았어", None), ("솔바람 배추 3박스 3만원 현금 받았어", False),
     ("솔바람 배추 3박스 3만원 계좌로 받았어요", False), ("솔바람 배추 3박스 3만원 외상이요", True),
     # PM #82 2차 리뷰 (10/12): "돈"은 낱말일 때만, 금액은 숫자+"원" 뒤만
     ("솔바람 배추 3박스 용돈 줬어", None), ("솔바람 배추 3박스 잔돈 줬어", None), ("솔바람 배추 3 받았어", None),
     ("솔바람 배추 3박스 돈 줬어", False), ("솔바람 배추 3박스 돈 받음", False),
     ("솔바람 배추 3박스 3만원 받았어", False), ("솔바람 배추 3박스 삼만 원 받았어", False),
     # PM 10/12 확정: 결제 단어 + 미래·약속 표지 → 현금으로 안 채움 (확인), 이미 받은 동사면 그대로
     ("솔바람 배추 3박스 3만원 현금 나중에", None), ("솔바람 배추 3박스 3만원 현금으로 내일 준대", None),
     ("솔바람 배추 3박스 3만원 계좌로 다음 주에 보낸대", None), ("솔바람 배추 3박스 3만원 카드는 월말에", None),
     ("솔바람 배추 3박스 3만원 외상 다음 주에 받기로", True), ("솔바람 배추 3박스 3만원 주말에 현금으로 받았어", False)],
)
def test_mock_credit_uses_the_postprocess_word_rules(text: str, credit) -> None:
    """Same word judgement as post-processing; both credit and payment words → null (user confirms)."""
    from app.services.parse_mock import mock_parse_utterance

    assert mock_parse_utterance(text).fields["credit"] is credit


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("솔바람한테 원두 2kg 3만원 현금", "원두"),
        ("솔바람한테 총각김치 3팩 2만원 현금", "총각김치"),
        ("솔바람한테 합판 5장 5만원 현금", "합판"),  # "장" is not a unit — only the name is checked
        ("솔바람한테 카드지갑 2개 1만원 현금", "카드지갑"),
        ("솔바람한테 외상으로 양파 2망 9천원", "양파"),  # "외상으로" (word + allowed suffix) is dropped
        ("솔바람한테 총액 30000원 배추 2포기", "배추"),  # amount word "총액" is dropped
    ],
)
def test_mock_drops_non_item_words_only_as_whole_words(text: str, name: str) -> None:
    """Cowork 검토(S-8): 외상·현금·원·총·합… are dropped only as the word itself or word + allowed suffix."""
    from app.services.parse_mock import mock_parse_utterance

    assert mock_parse_utterance(text).fields["items"][0]["name"] == name


@pytest.mark.parametrize(
    ("text", "counterparty", "count"),
    [
        ("솔바람에 대파 3단 9000원 현금", "솔바람", 1),
        ("새벽상회로 양파 2망 1만원 외상", "새벽상회", 1),
        ("솔바람으로 무 3개 6천원 현금", "솔바람", 1),
        ("대파 3단에 9000원 솔바람", None, 1),  # the first word is an item
        ("현금으로 대파 3단 9000원", None, 1),  # payment word
        ("아침에 솔바람한테 대파 3단 9000원", "솔바람", 1),  # marker wins
        ("오전에 대파 3단 9000원 현금", None, 1),  # time
        # marker wins for the value; each "~에/한테" has its own item → two places (다중 거래처 2차, PM 10/8)
        ("솔바람에 대파 3단 9000원, 새벽상회한테 무 3개 6천원", "새벽상회", 2),
        ("계란 한 판에 6000원", None, 1),  # "~에" in the middle is not read
    ],
)
def test_mock_reads_first_word_counterparty_only_without_a_marker(text: str, counterparty, count: int) -> None:
    """PM #41 리뷰: first word "~에/~로/으로" is the counterparty only when no 에게·한테·거래처 marker exists."""
    from app.services.parse_mock import mock_parse_utterance

    result = mock_parse_utterance(text)
    assert result.fields["counterparty"] == counterparty
    assert result.counterparty_count == count


def test_first_word_counterparty_is_not_taken_as_the_item_name() -> None:
    from app.services.parse_mock import mock_parse_utterance

    assert mock_parse_utterance("솔바람에 대파 3단 9000원 현금").fields["items"][0]["name"] == "대파"


@pytest.mark.parametrize(
    ("text", "name", "qty", "unit"),
    [
        ("솔바람한테 오징어 6마리 3만원 현금", "오징어", 6, "마리"),
        ("솔바람한테 계란 2판 1만2천원 현금", "계란", 2, "판"),
        ("솔바람한테 두부 3모 4500원 현금", "두부", 3, "모"),
        ("솔바람한테 판두부 2개 4천원 현금", "판두부", 2, "개"),  # item names starting with a unit are kept (#39)
        ("솔바람한테 모과 3개 6천원 현금", "모과", 3, "개"),
    ],
)
def test_mock_reads_mari_pan_mo_as_units(text: str, name: str, qty: int, unit: str) -> None:
    """Dev 기준 미등록 단위(마리·판·모) 추가 — PM 순서. "포대"는 Dev 0건이라 이번엔 넣지 않음."""
    from app.services.parse_mock import mock_parse_utterance

    item = mock_parse_utterance(text).fields["items"][0]
    assert (item["name"], item["qty"], item["unit"]) == (name, qty, unit)


@pytest.mark.parametrize(
    "text",
    [
        "바로 대파 3단 9000원 현금",  # 1 letter after the particle
        "새로 양파 2망 1만원",
        "추가로 대파 3단 9000원 현금",  # adverb
        "반품으로 양파 2망 1만원",
        "새벽에 대파 3단 9000원 현금",  # time
        "주말에 양파 2망 1만원 외상",
        "지난번에 무 3개 6천원 현금",
        "어제저녁에 대파 3단 9000원 현금",  # time compound
        "시장에 대파 3단 9000원 현금",  # place
        # PM #46 2차 리뷰: 2 letters or fewer, 서비스·어젯밤
        "이번에 대파 3단 9000원 현금",
        "저번에 무 3개 6천원 외상",
        "다음에 양파 2망 1만원 현금",
        "요번에 배추 2포기 8천원 외상",
        "월말에 대파 2단 6000원 외상",
        "도매로 양파 5망 2만5천원 현금",
        "택배로 양파 2망 1만원",
        "배달로 무 4개 8천원 현금",
        "일단으로 무 3개 6천원 현금",
        "전부로 배추 3포기 1만2천원 외상",
        "서비스로 대파 1단 드렸어",
        "어젯밤에 양파 1망 5천원 현금",
        # 병철님 #46 리뷰: time·delivery patterns (whole name only), 한꺼번
        "지난달에 배추 3포기 1만2천원 외상",
        "이번달에 무 5개 1만원 현금",
        "다음달에 대파 4단 1만2천원 외상",
        "작년말에 양파 3망 1만5천원 외상",
        "오전중에 계란 2판 1만4천원 현금",
        "한꺼번에 무 6개 1만2천원 현금",
        "퀵서비스로 원두 2봉지 4만원 현금",
        "택배로 배추 4포기 1만6천원 외상",
        "화물로 양파 10망 5만원 외상",
    ],
)
def test_first_word_that_is_not_a_name_is_not_a_counterparty(text: str) -> None:
    """PM #46 리뷰: 2 letters or fewer, time, adverb and place first words are not counterparties."""
    from app.services.parse_mock import mock_parse_utterance

    result = mock_parse_utterance(text)
    assert result.fields["counterparty"] is None
    assert result.counterparty_count == 1


def test_two_letter_first_word_name_is_asked_back_but_marker_name_is_kept() -> None:
    """PM #46 2차 리뷰: a real 2-letter name as the first word is None (400 되묻기); the marker path is unchanged."""
    from app.services.parse_mock import mock_parse_utterance

    assert mock_parse_utterance("한솔에 대파 3단 9000원 현금").fields["counterparty"] is None
    assert mock_parse_utterance("한솔한테 대파 3단 9000원 현금").fields["counterparty"] == "한솔"


def test_counterparty_that_starts_with_a_time_word_is_kept() -> None:
    """"새벽상회" starts with 새벽 but is a name; only words made of time words alone are dropped."""
    from app.services.parse_mock import mock_parse_utterance

    assert mock_parse_utterance("새벽상회로 양파 2망 1만원 외상").fields["counterparty"] == "새벽상회"


def test_two_quantity_expressions_ask_for_the_second_item_name() -> None:
    """PM #46: 2 quantity expressions = 2 items → Rule does not settle on 1 item; unread names are asked (400)."""
    from app.services.parse_mock import mock_parse_utterance

    text = "솔바람한테 대파 3단 9000원 양파 2망 1만원 외상"
    items = mock_parse_utterance(text).fields["items"]
    assert [(item["qty"], item["unit"]) for item in items] == [(3, "단"), (2, "망")]
    assert items[0]["name"] == "대파"
    assert (items[1]["name"], items[1]["unit_price"], items[1]["amount"]) == (None, None, None)

    res = _parse(text)
    assert res.status_code == 400
    assert res.json()["error_code"] == "MISSING_REQUIRED_FIELD"
    assert res.json()["problem_fields"] == ["items[1].name"]


def test_three_quantity_expressions_ask_for_the_unread_item_names() -> None:
    res = _parse("새벽상회한테 무 4개 8천원 대파 2단 6천원 배추 3포기 1만2천원 현금")
    assert res.status_code == 400
    assert res.json()["error_code"] == "MISSING_REQUIRED_FIELD"
    assert res.json()["problem_fields"] == ["items[1].name", "items[2].name"]


def test_four_quantity_expressions_exceed_max_items() -> None:
    # "줄" is not a Rule unit, so the quantity expressions here use registered units only
    res = _parse("솔바람한테 원두 2봉지 4만원 우유 10팩 2만원 시럽 3병 1만5천원 컵 20개 8천원 외상")
    assert res.status_code == 400
    assert res.json()["error_code"] == "EXCEED_MAX_ITEMS"
    assert res.json()["problem_fields"] == ["items"]


def test_one_quantity_expression_keeps_a_single_item() -> None:
    from app.services.parse_mock import mock_parse_utterance

    result = mock_parse_utterance("솔바람한테 배추 2포기 8000원 현금")
    assert result.item_count is None
    assert result.fields["items"] == [{"name": "배추", "qty": 2, "unit": "포기", "unit_price": None, "amount": 8000}]
    assert _parse("솔바람한테 배추 2포기 8000원 현금").status_code == 200


@pytest.mark.parametrize(("text", "expected"), [
    ("대파 7근, 고추 3근, 두부 2통, 사과 3박스", 4),  # 근·통은 _UNITS에 없어 2로 셌다 (#120)
    ("쌀 1포, 콩 2봉지, 깨 1병, 팥 3봉지", 4),  # 포 = 포대
    ("사과 2박스 아니 3박스, 배 1박스, 귤 2봉지", 3),  # 정정 뒤 덩어리가 앞 덩어리를 대체
    ("사과 1박스에 15000원짜리 2박스", 1),  # 단가
    ("생수 24개입 3박스", 1),  # 입수
    ("고구마 1kg 4봉지", 1),  # 규격 + 수량
    ("사과 2개에 3000원씩 6개, 배 1박스, 귤 2봉지", 3),  # 수량 2 이상의 단가
    ("사과 2개에 3000원 6개, 배 1박스, 귤 2봉지", 3),  # "씩" 없는 단가도 뒤에 수량이 오면 단가
    # 뒤에 같은 품목 수량이 없는 "N박스에 M원"은 합계 — 품목으로 센다 (적게 세면 부분 저장)
    ("사과 2박스에 40000원, 배 3박스에 60000원, 귤 1박스에 10000원, 감 2박스에 20000원", 4),
    ("사과 2박스에 40000원 배 1박스 귤 3봉지 감 2개", 4),
    ("사과 1박스 10000원씩", 1),
    ("고구마 1kg짜리 4봉지, 감자 2박스, 양파 1망", 3),  # "짜리" 규격 + 수량
    # 몰아서 말한 품목 (PM 10/26): 이름 여럿이 "N단위씩" 수량 하나를 나눠 씀 — 이름마다 한 품목
    ("가나상회 배추 무 대파 쪽파 1박스씩 50000원 외상", 4),
    ("배추, 무, 대파, 쪽파 1박스씩", 4),
    ("배추랑 무랑 대파랑 쪽파 1박스씩", 4),
    ("가나상회 배추 무 대파 1박스씩 40000원 외상", 3),
    ("가나상회 국산 배추 무 대파 1박스씩", 3),  # 꾸밈말은 다음 이름과 한 품목
    ("배추 무 각각 1박스씩", 2),
    ("김씨네 배추 1박스씩", 1),  # 거래처(~네)에서 멈춤
    ("오늘 배추 1박스씩 외상", 1),
    ("가나상회에 배추 무 대파 쪽파 1박스씩", 4),
    ("배추 1박스 무 1박스 대파 1박스 쪽파 1박스", 4),  # 끊어 말한 그대로
])
def test_count_spoken_items(text: str, expected: int) -> None:
    """#120: item count read from the (normalized) utterance, compared with the LLM's item count."""
    from app.services.parse_mock import _count_spoken_items

    assert _count_spoken_items(text) == expected


def test_four_items_with_counters_outside_rule_units_exceed_max_items() -> None:
    """#120: 근·통 items were not counted, so the Rule path asked for missing values instead of 품목 초과."""
    from app.services.parse_mock import mock_parse_utterance

    text = "솔바람한테 대파 7근, 고추 3근, 두부 2통, 사과 3박스 20만원 외상"
    assert mock_parse_utterance(text).item_count == 4
    res = _parse(text)
    assert res.status_code == 400
    assert res.json()["error_code"] == "EXCEED_MAX_ITEMS"


def test_items_listed_before_one_shared_quantity_exceed_max_items(db: Session) -> None:
    """PM 10/26: four names sharing "한 박스씩" were saved as 3 items (쪽파 dropped) — now 품목 초과, nothing stored."""
    res = _parse("가나상회 배추 무 대파 쪽파 한 박스씩 오만 원 외상")
    assert (res.status_code, res.json()["error_code"]) == (400, "EXCEED_MAX_ITEMS")
    assert db.scalars(select(LedgerEntry)).first() is None


@pytest.mark.parametrize(("text", "transaction_type"), [
    # PR #128 PM 요청: a clear partial payment → DL-30 unsupported (asked to say it again)
    ("솔바람 양파 3박스 2만원 받고 남은 1만원 외상 합계 3만원", "partial_payment"),
    ("솔바람 양파 3박스 3만원 2만원 받았고 나머지 1만원은 다음 달에 외상", "partial_payment"),
    # PM 10/14: two different amounts, one received → clear too (was only the credit question)
    ("솔바람 양파 3박스 3만원 2만원 받았어 나머지는 외상", "partial_payment"),
    # ambiguous → only the credit question (transaction type not judged)
    ("솔바람 양파 3박스 3만원 현금 받고 나머지 외상", None),
    ("솔바람 양파 3박스 3만원 외상", None),
])
def test_mock_judges_a_clear_partial_payment_unsupported(text: str, transaction_type) -> None:
    from app.services.parse_mock import mock_parse_utterance

    assert mock_parse_utterance(text).transaction_type == transaction_type
