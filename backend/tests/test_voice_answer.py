"""DL-31 voice answer — interpretation rules (strict) and the API (nothing stored, field switch, caps)."""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.main import app
from app.models import FeedbackEvent, IdempotencyRecord, LedgerEntry
from app.services.stt import STTError
from app.services.voice_answer import interpret

client = TestClient(app)


# --- interpretation: correct when clear, fail when unsure (0 wrong fills) -----------------

@pytest.mark.parametrize("field, text, value, unit", [
    ("credit", "외상이요", True, None),
    ("credit", "달아 놔", True, None),
    ("credit", "현금으로 받았어요", False, None),
    ("credit", "카드요", False, None),
    ("qty", "세 박스요", "3", "박스"),
    ("qty", "두 판", "2", "판"),
    ("qty", "3박스", "3", "박스"),
    ("qty", "1.5킬로", "1.5", "킬로"),
    ("total", "삼만 원이요", 30000, None),
    ("total", "전부 사만오천원", 45000, None),
    ("counterparty", "김씨네요", "김씨네", None),
    ("counterparty", "대박마트예요", "대박마트", None),
    ("counterparty", "박사장님한테요", "박사장님", None),
    ("qty", "열 박스요", "10", "박스"),  # not "1E+1"
    ("qty", "100개", "100", "개"),
    ("counterparty", "종로상회요", "종로상회", None),  # 로 is part of the name, not stripped
    ("counterparty", "을지로식당이에요", "을지로식당", None),
    ("total", "전부 사만 원", 40000, None),  # echoing the question "전부 얼마예요?" is still exact
    ("total", "합쳐서 칠만 원이요", 70000, None),
    # PM #72 3rd review (10/8): common correct answers that used to fail — each an exact shape
    ("credit", "외상으로 해 주세요", True, None),
    ("credit", "외상 달아 주세요", True, None),
    ("credit", "장부에 달아 줘", True, None),
    ("credit", "이체해 줬어요", False, None),
    ("credit", "입금됐어요", False, None),
    ("credit", "송금 받았어요", False, None),
    ("credit", "현찰로 줬어", False, None),
    ("credit", "돈 받았어요", False, None),
    ("credit", "결제 끝났어요", False, None),
    ("qty", "세 포대", "3", "포대"),
    ("total", "팔천 오백 원", 8500, None),
    ("total", "삼 만 원", 30000, None),  # spaced numerals are joined first — read right, not 10000
    ("counterparty", "대박마트에요", "대박마트", None),
    ("counterparty", "예림상회요", "예림상회", None),  # starts with 예 but is a name
    # #109: STT writes "3천 5백원" with digits — joined like "팔천 오백 원" (it used to fail as ambiguous)
    ("total", "3천 5백원", 3500, None),
    ("total", "7천 5백원이요", 7500, None),
    ("total", "1만 2천 5백원", 12500, None),
    ("total", "3천 5백만원", 35000000, None),  # #109 2/2: it used to fail as ambiguous ("3천 5000000원")
    ("qty", "천 오백 개", "1500", "개"),  # #109 PM 결정: 1500개 (it used to fail as ambiguous)
])
def test_clear_answers(field, text, value, unit) -> None:
    r = interpret(field, text)
    assert (r.status, r.value, r.unit) == ("ok", value, unit)


# 10/12 judge set: STT punctuation, trailing hesitation and 반말 -야/-이야 are cleaned in every field
@pytest.mark.parametrize("field, text, value, unit", [
    ("credit", "어... 외상이야", True, None),
    ("credit", "그러니까 현금", False, None),
    ("credit", "외상이요 음", True, None),
    ("qty", "음, 두 판이야", "2", "판"),
    ("qty", "1.5킬로!", "1.5", "킬로"),  # a dot between digits is a decimal point
    ("total", "만 원이야.", 10000, None),
    ("total", "아… 사만 원", 40000, None),
    ("counterparty", "김씨네야", "김씨네", None),
    ("counterparty", "대박마트이야!", "대박마트", None),
])
def test_cleaned_answers(field, text, value, unit) -> None:
    r = interpret(field, text)
    assert (r.status, r.value, r.unit) == ("ok", value, unit)


@pytest.mark.parametrize("field, text", [
    ("counterparty", "어… 김씨네"),  # leading filler on a name = unsure, even behind "…"
    ("credit", "외상...?"),  # "?" is kept — a question fails
])
def test_cleanup_keeps_strict_failures(field: str, text: str) -> None:
    r = interpret(field, text)
    assert (r.status, r.value) == ("failed", None)


# PM 10/16: one trailing "?" on a short qty (with a unit) / total (with 원) answer is still an answer
@pytest.mark.parametrize("field, text, choices, value, unit", [
    ("qty", "세 단이요?", None, "3", "단"),
    ("qty", "두 박스??", None, "2", "박스"),          # "??" is still one trailing question mark
    ("qty", "세 단 반이요?", None, "3.5", "단"),
    ("total", "만 오천 원이요?", None, 15000, None),
    ("total", "만 원이야?", None, 10000, None),        # was a failure before PM 10/16
    ("total", "삼만 이천 원이요?", [30000, 32000], 32000, None),  # 합계 선택
])
def test_question_mark_single_value_ok(field, text, choices, value, unit) -> None:
    r = interpret(field, text, choices=choices)
    assert (r.status, r.value, r.unit) == ("ok", value, unit)


@pytest.mark.parametrize("field, text, choices", [
    ("qty", "세 단이요? 네 단이요?", None),          # two values
    ("qty", "몇 단이요?", None),                     # no number
    ("qty", "두 단 아니 세 단이요?", None),           # correction + "?" — out of scope, still fails
    ("qty", "하나요?", None),                        # no unit ("-나요" is also a question ending)
    ("total", "만 오천?", None),                     # no 원
    ("total", "삼만 원 맞아요?", None),               # asks back to check
    ("total", "만 원이요? 만 오천 원이요?", None),     # two values
    ("total", "얼마요?", None),                      # no number
])
def test_question_mark_still_fails(field, text, choices) -> None:
    r = interpret(field, text, choices=choices)
    assert (r.status, r.value) == ("failed", None)


@pytest.mark.parametrize("field, text", [
    ("credit", "외상이요?"), ("credit", "현금?"),
    ("counterparty", "새벽상회요?"), ("counterparty", "김씨네?"),
])
def test_question_mark_not_applied_to_credit_counterparty(field: str, text: str) -> None:
    r = interpret(field, text, item_names=["배추"])
    assert (r.status, r.value) == ("failed", None)


# 정정 (10/8 수경): exactly two values + exactly one 아니/아니요/아니고 between → the second value
@pytest.mark.parametrize("field, text, choices, value, unit", [
    ("credit", "외상 아니 현금이요", None, False, None),
    ("credit", "현금 아니고 외상", None, True, None),
    ("qty", "다섯 박스 아니 여섯 박스", None, "6", "박스"),
    ("total", "팔천 원이요, 아니 구천 원", None, 9000, None),
    ("total", "만 원 아니요 이만 원", None, 20000, None),
    ("total", "만 원 아니 삼만 원", [30000, 36000], 30000, None),  # first value need not be a choice
    ("counterparty", "김씨네 아니 대박마트", None, "대박마트", None),
    # used to fail before the 10/8 correction rule
    ("credit", "외상 아니고 현금", None, False, None),
    ("qty", "두 박스 아니 세 박스", None, "3", "박스"),
    ("total", "이만 오천 원 아니 삼만원", None, 30000, None),
    ("total", "삼만 원 아니고 삼만 오천", [30000, 35000], 35000, None),
])
def test_correction_answers(field, text, choices, value, unit) -> None:
    r = interpret(field, text, choices=choices)
    assert (r.status, r.value, r.unit) == ("ok", value, unit)


@pytest.mark.parametrize("field, text, choices", [
    ("total", "만 원 아니야", None),  # no second value
    ("credit", "외상 아니요", None),
    ("credit", "외상 아니에요", None),
    ("total", "아니 만 원", None),  # no first value
    ("total", "만 원 아니 이만 원 아니 삼만 원", None),  # two corrections
    ("credit", "외상 아니 현금 아니 외상", None),
    ("qty", "세 박스 아니고 네 박스 아니", None),
    ("credit", "외상 아니 만 원", None),  # second value belongs to another field
    ("total", "외상 아니 만 원", None),  # first value belongs to another field
    ("total", "삼만 원 아니 사만 원", [30000, 36000]),  # corrected to an amount not shown
    ("counterparty", "솔바람 아니고 새벽상회", None),  # first side is not a name
])
def test_correction_failures(field, text, choices) -> None:
    r = interpret(field, text, choices=choices)
    assert (r.status, r.value) == ("failed", None), f"{field} {text!r} filled {r.value!r}"


# "반" (10/8 수경): only "N 단위 반" → N+0.5 and "반 단위" → 0.5
@pytest.mark.parametrize("text, value, unit", [
    ("세 단 반", "3.5", "단"),
    ("한 박스 반이요", "1.5", "박스"),
    ("3단반", "3.5", "단"),
    ("반 박스", "0.5", "박스"),
    ("반 단이요", "0.5", "단"),
])
def test_half_answers(text, value, unit) -> None:
    r = interpret("qty", text)
    assert (r.status, r.value, r.unit) == ("ok", value, unit)


@pytest.mark.parametrize("field, text", [
    ("qty", "반값"),
    ("qty", "반은 외상"),
    ("credit", "반은 외상"),
    ("credit", "반은 외상이고 반은 현금"),
    ("total", "반값"),
    ("qty", "세 반"),  # no unit
    ("qty", "절반"),
    ("qty", "반"),
    ("qty", "반 원"),  # 원 is not a quantity unit
])
def test_half_failures(field, text) -> None:
    r = interpret(field, text)
    assert (r.status, r.value) == ("failed", None), f"{field} {text!r} filled {r.value!r}"


# PM #90 review (10/12): "반 단위" needs the space; "N 단위 반" stays (PM approved)
@pytest.mark.parametrize("text, value, unit", [
    ("반 근", "0.5", "근"),
    ("반 박스", "0.5", "박스"),
    ("두 박스 반", "2.5", "박스"),
])
def test_half_spaced_answers(text, value, unit) -> None:
    r = interpret("qty", text)
    assert (r.status, r.value, r.unit) == ("ok", value, unit)


@pytest.mark.parametrize("text", ["반대", "반장", "반짝", "반박스"])  # ordinary words, never 0.5대·0.5장·0.5짝
def test_half_glued_words_fail(text) -> None:
    r = interpret("qty", text)
    assert (r.status, r.value) == ("failed", None), f"{text!r} filled {r.value!r}{r.unit}"


# PM #90 review (10/12): a comma between digits is a thousands separator — kept, like main
@pytest.mark.parametrize("field, text, value, unit", [
    ("total", "15,000원", 15000, None),
    ("total", "1,234,000원", 1234000, None),
    ("qty", "1,500개", "1500", "개"),  # a unit → quantity, same as main
])
def test_thousands_comma_answers(field, text, value, unit) -> None:
    r = interpret(field, text)
    assert (r.status, r.value, r.unit) == ("ok", value, unit)


def test_comma_between_words_is_still_a_space() -> None:
    from app.services.voice_answer import _clean

    assert _clean("외상, 외상이요") == "외상 외상이요"
    assert _clean("구천 원,아니 만 원") == "구천 원 아니 만 원"
    assert interpret("credit", "외상, 외상이요").status == "failed"  # not one exact answer
    assert interpret("qty", "1,500").status == "failed"  # bare number ≥ 1000 = an amount


# 수량 칸 number only (10/8 (가)): value only, unit empty — the FE adds the question card's unit.
# Bare numbers (no 원 / no unit): below 1000 = quantity, 1000 and up = amount (PM #72 review)
@pytest.mark.parametrize("field, text, value, unit", [
    ("qty", "셋이요", "3", None),
    ("qty", "하나요", "1", None),
    ("qty", "열둘이에요", "12", None),
    ("qty", "스물다섯", "25", None),
    ("qty", "아흔아홉", "99", None),
    ("qty", "3", "3", None),
    ("qty", "999", "999", None),  # just below the split
    ("qty", "3000개", "3000", "개"),  # a unit makes it a quantity
    ("total", "천", 1000, None),  # the split itself is an amount
    ("total", "3000", 3000, None),
    ("total", "오백 원", 500, None),  # 원 said → small amounts still fill
    ("total", "500원", 500, None),
])
def test_bare_number_answers(field, text, value, unit) -> None:
    r = interpret(field, text)
    assert (r.status, r.value, r.unit) == ("ok", value, unit)


@pytest.mark.parametrize("field, text", [
    ("qty", "3000"),  # STT wrote an amount as digits
    ("qty", "1000"),
    ("qty", "12000이요"),
    ("qty", "네"),  # "yes", not 4 — determiner forms are not counting words
    ("qty", "네요"),
    ("qty", "한"),
    ("total", "3"),  # a quantity was said
    ("total", "999"),
    ("total", "오백"),
    ("total", "셋이요"),
    ("total", "오백 아니 천 원"),  # correction: the first side is not an amount either
])
def test_bare_number_failures(field, text) -> None:
    r = interpret(field, text)
    assert (r.status, r.value) == ("failed", None), f"{field} {text!r} filled {r.value!r}"


# 외상 칸 (PM 10/8, same judgment as pp-1.4): a later/promise/not-yet answer is neither credit nor paid → ask;
# "paid" only in a clear past tense, and 줬어·들어왔어 only after 돈 or a payment word
@pytest.mark.parametrize("text, value", [
    ("받았어요", False),  # PM 10/12 확정 ①: ACCEPT_PAID_WITHOUT_PAY_WORD
    ("돈 받았어", False),  # PM 10/12 확정 ①
    ("계좌로 받았어요", False),
    ("입금 들어왔어요", False),
    ("계산 끝났어요", False),
    ("돈 줬어요", False),
    ("현금으로 줬어", False),
    ("외상 장부에 달아 줘", True),  # 장부 is not a future marker
])
def test_credit_clear_past_payment(text, value) -> None:
    r = interpret("credit", text)
    assert (r.status, r.value) == ("ok", value)


@pytest.mark.parametrize("text", [
    "다음 주에 받음", "다음주에 받음", "월말에 받기로", "내일 준대요", "나중에 받기로 했어요",
    "담주에 줄게요", "이따 준다고", "아직 못 받았어", "외상 다음 주에",  # even with 외상: ask, never guess
    "외상 아니 내일 준대",  # correction to a future answer
    "들어왔어", "줬어",  # no 돈 / payment word
    "이체했대", "이체해 줬대",  # PM 10/12 확정 ②: ACCEPT_HEARSAY_PAYMENT — hearsay is not a clear payment
    "현금 아직 못 받았어요", "계좌로 아직이요", "현금 못 받았어요",  # PM #82 review 10/12: a payment word + not yet
    "현금 나중에", "현금으로 내일 준대", "계좌로 다음 주에 보낸대", "카드는 월말에",  # PM 10/12: unchanged, already asked
])
def test_credit_future_or_unclear_fails(text) -> None:
    r = interpret("credit", text)
    assert (r.status, r.value) == ("failed", None), f"{text!r} filled {r.value!r}"


def test_credit_switches_follow_pm_1012() -> None:
    """PM 10/12 decided ①② — they live in two switches in voice_answer.py; change only them later."""
    import app.services.voice_answer as va

    assert va.ACCEPT_PAID_WITHOUT_PAY_WORD is True  # ① "돈 받았어" → 현금
    assert va.ACCEPT_HEARSAY_PAYMENT is False  # ② "이체했대" → 실패


@pytest.mark.parametrize("field, text, reason", [
    ("credit", "몰라요", "ambiguous"),
    ("credit", "", "no_answer"),
    ("qty", "박스요", "no_number"),  # never guess 1
    ("qty", "0개", "out_of_range"),
    ("total", "모르겠어요", "no_number"),
    ("total", "이억 원", "out_of_range"),
    ("counterparty", "삼만 원이요", "not_a_name"),  # answer about another field
    ("counterparty", "요", "no_answer"),
    ("credit", "외상 아니에요", "ambiguous"),  # negation never becomes a value
    ("credit", "외상은 아니고요", "ambiguous"),
    ("counterparty", "외상이요", "not_a_name"),
    ("counterparty", "현금이요", "not_a_name"),
    ("counterparty", "몰라요", "not_a_name"),
    ("credit", "외상 안 해요", "ambiguous"),
    ("credit", "아뇨 현금이요", "ambiguous"),
    ("counterparty", "네 김씨네요", "not_a_name"),
    ("total", "삼만 원 정도", "ambiguous"),  # approximate ≠ value
    ("qty", "세 박스쯤", "no_number"),
])
def test_unsure_answers_fail_instead_of_guessing(field, text, reason) -> None:
    r = interpret(field, text)
    assert (r.status, r.value, r.reason) == ("failed", None, reason)


def test_arithmetic_choice_must_match_one_of_the_two() -> None:
    assert interpret("total", "삼만오천 원이요", choices=[30000, 35000]).value == 35000
    assert interpret("total", "사만 원", choices=[30000, 35000]).reason == "not_in_choices"


# --- API -----------------------------------------------------------------------------------

def _token(user_id: str = "user-b") -> str:
    return client.post("/api/v1/auth/dev-token", json={"user_id": user_id}).json()["access_token"]


def _send(field: str, key: str | None = None, choices: str | None = None, mime: str = "audio/webm"):
    data = {"field": field, **({"choices": choices} if choices else {})}
    return client.post(
        "/api/v1/ledger/voice-answer", data=data, files={"file": ("a.webm", b"x" * 500, mime)},
        headers={"Authorization": f"Bearer {_token()}", "X-Idempotency-Key": key or str(uuid.uuid4())},
    )


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch):
    def turn_on(fields: str = "credit,qty,total,counterparty", heard: str = "외상이요") -> None:
        monkeypatch.setattr(get_settings(), "voice_answer_fields", fields)
        monkeypatch.setattr("app.api.voice_answer.transcribe", lambda *a, **k: heard)

    return turn_on


def test_all_fields_off_by_default() -> None:
    res = _send("credit")
    assert res.status_code == 400
    assert res.json()["error_code"] == "VOICE_ANSWER_DISABLED"


def test_only_enabled_fields_answer(enabled) -> None:
    enabled("credit,total")
    assert _send("credit").status_code == 200
    assert _send("qty").json()["error_code"] == "VOICE_ANSWER_DISABLED"


def test_ok_answer_returns_value_and_what_was_heard(enabled, db: Session, caplog) -> None:
    enabled(heard="김씨비밀상회요")
    with caplog.at_level(logging.DEBUG):
        res = _send("counterparty")
    assert res.status_code == 200
    assert res.json() == {"field": "counterparty", "status": "ok", "value": "김씨비밀상회", "unit": None,
                          "reason": None, "utterance_text": "김씨비밀상회요"}
    assert "김씨비밀상회" not in caplog.text  # transcript never logged
    assert db.query(LedgerEntry).count() == 0 and db.query(FeedbackEvent).count() == 0  # nothing stored


def test_failed_interpretation_is_200_with_reason(enabled) -> None:
    enabled(heard="외상 아니에요")
    body = _send("credit").json()
    assert (body["status"], body["value"], body["reason"]) == ("failed", None, "ambiguous")


def test_stt_failure_is_504_for_buttons(enabled, monkeypatch: pytest.MonkeyPatch) -> None:
    enabled()

    def fail(*_a, **_k):
        raise STTError("down")

    monkeypatch.setattr("app.api.voice_answer.transcribe", fail)
    res = _send("credit")
    assert res.status_code == 503
    assert res.json()["error_code"] == "EXTERNAL_API_TIMEOUT"


def test_bad_audio_and_bad_choices(enabled) -> None:
    enabled()
    assert _send("credit", mime="text/plain").json()["error_code"] == "AUDIO_INVALID"
    res = _send("total", choices="3만,4만")
    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "choices"]  # FastAPI's 422 shape, like any form field


def test_openapi_lists_auth_and_size_errors() -> None:
    responses = app.openapi()["paths"]["/api/v1/ledger/voice-answer"]["post"]["responses"]
    assert {"401", "413", "422"} <= responses.keys()


def test_choices_reach_the_interpreter(enabled) -> None:
    enabled(heard="삼만오천 원")
    assert _send("total", choices="30000,35000").json()["value"] == 35000


def test_same_key_replays(enabled, db: Session) -> None:
    enabled()
    key = str(uuid.uuid4())
    assert _send("credit", key=key).json() == _send("credit", key=key).json()
    assert db.query(IdempotencyRecord).count() == 1


def test_shares_the_voice_cap_with_parse_audio(enabled, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every voice request costs an STT call → AUDIO_LIMIT_* covers answers too."""
    enabled()
    monkeypatch.setattr(get_settings(), "audio_limit_per_minute", 2)
    monkeypatch.setattr("app.api.ledger.transcribe", lambda *a, **k: "김씨에게 배추 3박스 외상 30000원")
    client.post("/api/v1/ledger/parse-audio", files={"file": ("a.webm", b"x" * 500, "audio/webm")},
                headers={"Authorization": f"Bearer {_token()}", "X-Idempotency-Key": str(uuid.uuid4())})
    assert _send("credit").status_code == 200
    res = _send("credit")
    assert res.status_code == 429
    assert res.json()["error_code"] == "RATE_LIMITED"


def test_size_limit_applies(enabled) -> None:
    enabled()
    res = client.post("/api/v1/ledger/voice-answer", content=b"x" * (8 * 1024 * 1024),
                      headers={"content-type": "multipart/form-data; boundary=x"})
    assert res.status_code == 413


def test_requires_auth() -> None:
    res = client.post("/api/v1/ledger/voice-answer", data={"field": "credit"},
                      files={"file": ("a.webm", b"x", "audio/webm")})
    assert res.status_code == 401



# PM #72 review: short answers that filled a wrong value — all must fail (DL-31: 0 wrong fills)
PM_72_MUST_FAIL = [
    ("credit", "외상 하지 마"), ("credit", "외상 없어요"), ("credit", "외상 아냐"), ("credit", "외상 노"),
    ("credit", "외상은 없어"),
    ("qty", "한 삼 박스?"),  # "세 단 반" · "한 박스 반" now read as N+0.5 (10/8 수경, test_half_answers)
    ("total", "삼만 원 아니에요"), ("total", "천 원짜리 세 개"), ("total", "이만 원 안 돼요"),
    ("total", "한 삼만 원"), ("total", "삼만 원 넘어요"), ("total", "아마 삼만 원"), ("total", "만 원 미만"),
    ("total", "박스당 만 원"), ("total", "천 원씩"),
    ("counterparty", "솔바람 아니고 새벽상회"), ("counterparty", "배추"), ("counterparty", "잘 모르겠는데"),
    ("counterparty", "새벽상회인가"), ("counterparty", "솔바람일걸"), ("counterparty", "음 새벽상회"),
    ("counterparty", "어 그 김씨네"),
    # PM #72 2nd review (10/8) — exact-shape whitelist: anything beyond the short shape fails
    ("credit", "외상 아닌데"), ("credit", "외상 싫어"), ("credit", "외상 빼"), ("credit", "외상 취소"),
    ("credit", "외상 없이"), ("credit", "외상 노노"), ("credit", "외상 달까"), ("credit", "현금으로 할까"),
    ("credit", "외상이었나"), ("credit", "외상 추가로"), ("credit", "외상?"),
    ("qty", "세 박스나 네 박스"), ("qty", "삼만 원"), ("qty", "세 개 더"), ("qty", "세 개 빼고"),
    ("qty", "추가로 두 개"), ("qty", "세 박스에 천 원"), ("qty", "세 박스만"), ("qty", "두 박스하고"),
    ("total", "대충 삼만 원"), ("total", "삼만 원 못 돼"), ("total", "삼만 원 남았어"), ("total", "삼만 원 할인"),
    ("total", "삼만 원 빼고"), ("total", "세 박스"), ("total", "박스에 천 원"), ("total", "선금 만 원"),
    ("total", "삼만 원이었나"),
]


@pytest.mark.parametrize("field, text", PM_72_MUST_FAIL)
def test_pm72_wrong_fills_now_fail(field: str, text: str) -> None:
    r = interpret(field, text)
    assert (r.status, r.value) == ("failed", None), f"{field} {text!r} filled {r.value!r}"


@pytest.mark.parametrize("text", ["삼만 원 말고"])  # "삼만 원 아니고 삼만 오천" = 정정 → test_correction_answers
def test_pm72_choice_with_negation_fails(text: str) -> None:
    r = interpret("total", text, choices=[30000, 35000])
    assert (r.status, r.value) == ("failed", None)


def test_auth_me_tells_the_fe_which_fields_have_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    """PM #72: the screen must know the enabled fields up front to hide 🎤 elsewhere."""
    monkeypatch.setattr(get_settings(), "voice_answer_fields", "qty, credit")
    res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {_token()}"})
    assert res.status_code == 200
    assert res.json()["voice_answer_fields"] == ["credit", "qty"]


def test_auth_me_empty_when_all_off() -> None:
    res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {_token()}"})
    assert res.json()["voice_answer_fields"] == []



@pytest.mark.parametrize("text, value", [
    ("사만 구천이요", 49000),  # no 원
    ("사만 원이 맞아요", 40000),
    ("사만 구천 원이 맞아요", 49000),
])
def test_pm72_choice_common_answers(text: str, value: int) -> None:
    r = interpret("total", text, choices=[40000, 49000])
    assert (r.status, r.value) == ("ok", value)



def test_counterparty_equal_to_an_item_name_fails() -> None:
    """수경님 제안: pass this trade's item names — "배추요" names the item, not the shop."""
    assert interpret("counterparty", "배추집이요", item_names=["배추집"]).reason == "not_a_name"
    assert interpret("counterparty", "배추 집이요", item_names=["배추집"]).reason == "not_a_name"  # spaces ignored
    assert interpret("counterparty", "배추상회요", item_names=["배추"]).value == "배추상회"  # a real shop name
    assert interpret("counterparty", "김씨네요", item_names=["배추"]).value == "김씨네"


def test_item_names_reach_the_interpreter_through_the_api(enabled) -> None:
    enabled(heard="배추집이요")
    res = client.post(
        "/api/v1/ledger/voice-answer", data={"field": "counterparty", "item_names": "배추집, 무"},
        files={"file": ("a.webm", b"x" * 500, "audio/webm")},
        headers={"Authorization": f"Bearer {_token()}", "X-Idempotency-Key": str(uuid.uuid4())},
    )
    assert res.json()["status"] == "failed"
    assert res.json()["reason"] == "not_a_name"


# PM 10/12: total prefix "다 합해서"·"합해서" restored (dropped with the revert e93d8c5) — exact shape kept
@pytest.mark.parametrize("text", ["다 합해서 삼만원", "합해서 삼만원", "다 합해서 3만 원", "합해서 30000원", "다합해서 삼만 원이요"])
def test_total_prefix_hap_haeseo(text) -> None:
    r = interpret("total", text)
    assert (r.status, r.value) == ("ok", 30000)


@pytest.mark.parametrize("field, text", [
    ("total", "합해서"),                       # prefix only
    ("total", "다 합해서"),
    ("total", "합해서 삼만원 정도 될걸요"),       # tail outside the shape
    ("total", "합해서 삼만원 맞나?"),
    ("qty", "합해서 세 박스"),                  # other fields unchanged
    ("counterparty", "합해서 솔바람"),
])
def test_total_prefix_hap_haeseo_strict(field, text) -> None:
    r = interpret(field, text)
    assert (r.status, r.value) == ("failed", None), f"{field} {text!r} filled {r.value!r}"


def test_total_prefix_hap_haeseo_with_choices() -> None:
    assert interpret("total", "합해서 삼만원", choices=[30000, 36000]).value == 30000
    assert interpret("total", "합해서 사만원", choices=[30000, 36000]).status == "failed"


# QA 10/13: total prefix "총액"·"총액은"·"총액이" (10/14 total voice answer on) — exact shape kept
@pytest.mark.parametrize("text, value", [
    ("총액 15000원이에요", 15000),
    ("총액 만 오천 원이에요", 15000),
    ("총액은 15000원", 15000),
    ("총액이 3만 원", 30000),
    ("총액 1만 5천원", 15000),
    ("총액15000원", 15000),
    ("총액은만원", 10000),
    ("총 15000원", 15000),        # existing prefixes unchanged
    ("총 이만 원", 20000),
    ("전부 육만 원이야", 60000),
])
def test_total_prefix_chongaek(text, value) -> None:
    r = interpret("total", text)
    assert (r.status, r.value) == ("ok", value)


@pytest.mark.parametrize("text", [
    "총액이만원",              # 총액이 만 원 (1만) or 총액 이만 원 (2만) — cannot tell, never guess
    "총액 세 개",              # a quantity
    "총액 아니고 5천 원",       # negation
    "총액은 만 원 정도",        # approximation
])
def test_total_prefix_chongaek_strict(text) -> None:
    r = interpret("total", text)
    assert (r.status, r.value) == ("failed", None), f"{text!r} filled {r.value!r}"


def test_total_prefix_chongaek_with_choices() -> None:
    assert interpret("total", "총액은 18000원", choices=[15000, 18000]).value == 18000
    assert interpret("total", "총액 2만 원", choices=[15000, 18000]).status == "failed"


# --- usage-test metrics (PM 10/13): one log line per answer — field · ok/failed · reason, never the transcript ---

def _voice_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("voice_answer ")]


def test_each_answer_logs_field_status_reason_only(enabled, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    enabled(heard="김씨비밀상회요")
    with caplog.at_level(logging.WARNING, logger="app.api.voice_answer"):
        _send("counterparty")
        monkeypatch.setattr("app.api.voice_answer.transcribe", lambda *a, **k: "외상 비밀 아니에요")
        _send("credit")
        _send("credit", mime="text/plain")

        def fail(*_a, **_k):
            raise STTError("down")

        monkeypatch.setattr("app.api.voice_answer.transcribe", fail)
        _send("qty")
    assert _voice_lines(caplog) == [
        "voice_answer field=counterparty status=ok reason=-",
        "voice_answer field=credit status=failed reason=ambiguous",
        "voice_answer field=credit status=failed reason=audio_invalid",
        "voice_answer field=qty status=failed reason=stt_failed",
    ]
    assert "비밀" not in caplog.text  # neither the transcript nor the filled value


def test_replay_and_disabled_field_are_not_counted(enabled, caplog) -> None:
    enabled("credit")
    key = str(uuid.uuid4())
    with caplog.at_level(logging.WARNING, logger="app.api.voice_answer"):
        _send("credit", key=key)
        _send("credit", key=key)  # idempotent replay — the same answer, not a second try
        _send("qty")  # VOICE_ANSWER_DISABLED — no voice try happened (api_error line covers it)
    assert _voice_lines(caplog) == ["voice_answer field=credit status=ok reason=-"]
