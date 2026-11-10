"""Background talk transcribed, nothing extracted (PM 11/2 유튜브 테스트) → 503 "잘 못 들었어요", not an all-empty form."""

import logging
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import app
from app.models import IdempotencyRecord, LedgerEntry
from app.services import parse_llm, parse_pipeline
from app.services.parse_llm import LedgerExtraction, LLMParseResult

client = TestClient(app)
HEARD = "오늘 영상에서는 비밀 레시피를 만들어 볼 건데요"


def _headers(key: str | None = None) -> dict[str, str]:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "user-b"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Idempotency-Key": key or str(uuid.uuid4())}


def _audio(key: str | None = None):
    return client.post("/api/v1/ledger/parse-audio", files={"file": ("a.webm", b"x" * 1000, "audio/webm")},
                       headers=_headers(key))


@pytest.fixture
def llm_answers(monkeypatch: pytest.MonkeyPatch):
    """LLM on with a chosen extraction, STT replaced — set the fields the model "heard"."""
    settings = Settings(_env_file=None, app_env="development", openai_api_key="test-key", llm_parse_enabled=True)
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr("app.api.ledger.transcribe", lambda *a, **k: HEARD)

    def set_fields(**fields: Any) -> None:
        candidate = LedgerExtraction(**{"counterparty": None, "items": [], "total_amount": None, "credit": None, **fields})
        result = LLMParseResult(candidate=candidate, model="gpt-4o-mini", processing_time_ms=900)
        monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", lambda *a, **k: result)

    return set_fields


def test_nothing_extracted_answers_like_stt_failure(llm_answers, caplog: pytest.LogCaptureFixture, db: Session) -> None:
    llm_answers()
    with caplog.at_level(logging.DEBUG):
        res = _audio()
    assert res.status_code == 503
    body = res.json()
    assert body["error_code"] == "EXTERNAL_API_TIMEOUT"
    assert body["utterance_text"] is None and body["partial_candidate"] is None  # nothing heard is handed back
    assert db.scalars(select(LedgerEntry)).first() is None
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("voice_nothing_extracted") for m in messages)
    assert any(m.startswith("voice_timing status=503") for m in messages)
    assert HEARD not in caplog.text  # never logged (CLAUDE.md §3②)


def test_nothing_extracted_keeps_no_idempotency_record(llm_answers, db: Session) -> None:
    """Like every 5xx: the same recording can be sent again."""
    llm_answers()
    key = str(uuid.uuid4())
    assert _audio(key).status_code == 503
    assert db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.key == key)) is None


@pytest.mark.parametrize("fields", [
    {"counterparty": "가나상회"},
    {"items": [{"name": "배추", "qty": None, "unit": None, "unit_price": None, "amount": None}]},
    {"total_amount": 30000},
    {"credit": True},
    {"credit": False},  # "현금" is a value too
])
def test_any_one_value_keeps_the_usual_question(llm_answers, fields: dict[str, Any]) -> None:
    llm_answers(**fields)
    res = _audio()
    assert res.status_code == 400
    assert res.json()["error_code"] == "MISSING_REQUIRED_FIELD"
    assert res.json()["utterance_text"] == HEARD


def test_item_without_any_value_counts_as_nothing(llm_answers) -> None:
    llm_answers(items=[{"name": None, "qty": None, "unit": None, "unit_price": None, "amount": None}])
    assert _audio().status_code == 503


def test_text_input_keeps_the_empty_form(llm_answers) -> None:
    """Typed text was meant as input: the form is the right next step."""
    llm_answers()
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": HEARD}, headers=_headers())
    assert (res.status_code, res.json()["error_code"]) == (400, "MISSING_REQUIRED_FIELD")
    assert res.json()["partial_candidate"]["items"] == []


@pytest.mark.parametrize(("heard", "code"), [
    ("김씨네 외상 오만 원 입금 들어왔어", "NOT_A_SALE"),  # no partial candidate, but understood
    ("가나상회 배추 2만원 받고 남은 1만원 외상", "UNSUPPORTED_TRANSACTION"),
    ("가나상회 배추 한 박스 무 두 박스 파 세 단 오이 네 개 오만 원 외상", "EXCEED_MAX_ITEMS"),
])
def test_other_400s_stay_400(monkeypatch: pytest.MonkeyPatch, heard: str, code: str) -> None:
    monkeypatch.setattr("app.api.ledger.transcribe", lambda *a, **k: heard)
    res = _audio()
    assert (res.status_code, res.json()["error_code"]) == (400, code)
