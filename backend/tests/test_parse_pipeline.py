"""LLM → Rule fallback wiring for /parse and voice input via /parse-audio (STT stubbed)."""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import ledger as ledger_api
from app.config import Settings
from app.main import app
from app.models import LedgerEntry
from app.services import parse_llm, parse_pipeline
from app.services.parse_llm import LedgerExtraction, LLMParseResult, LLMTimeoutError
from app.services.stt import STTError

client = TestClient(app)

UTTERANCE = "김씨에게 배추 3박스 외상 30000원"
LLM_CANDIDATE = LedgerExtraction(
    counterparty="김씨",
    items=[{"name": "배추", "qty": 2.5, "unit": "박스", "unit_price": None, "amount": 30000}],
    total_amount=30000,
    credit=True,
)


def _headers() -> dict[str, str]:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "voice-user"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())}


def _settings(**overrides) -> Settings:
    base = {"app_env": "development", "openai_api_key": "test-key", "llm_parse_enabled": True, "_env_file": None}
    return Settings(**{**base, **overrides})


def _llm_ok(utterance: str, *, settings: Settings) -> LLMParseResult:
    return LLMParseResult(candidate=LLM_CANDIDATE, model="gpt-4o-mini", processing_time_ms=900,
                          response_model="gpt-4o-mini-2024-07-18")


def _llm_timeout(utterance: str, *, settings: Settings) -> LLMParseResult:
    raise LLMTimeoutError("LLM request exceeded 5 seconds")


def test_llm_disabled_uses_rule_without_calling_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default off: a local .env key must not trigger a paid call."""
    def boom(*a, **k):
        raise AssertionError("LLM must not be called when disabled")
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", boom)
    result = parse_pipeline.structure_utterance(UTTERANCE, _settings(llm_parse_enabled=False))
    assert result.parser_type == "mock"
    assert result.is_fallback_used is False


def test_rule_reads_spaced_small_places_as_one_amount() -> None:
    """#109: the Rule path saved 500 (no follow-up) for "3천 5백원" because only the 500원 part was normalized."""
    result = parse_pipeline.rule_parse("고등어 2마리 3천 5백원 현금", _settings(llm_parse_enabled=False))
    assert result.fields["items"][0]["amount"] == 3500
    assert result.fields["total_amount"] == 3500


def test_llm_success_returns_llm_fields_with_decimal_qty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_ok)
    result = parse_pipeline.structure_utterance(UTTERANCE, _settings())
    assert result.parser_type == "llm"
    assert result.is_fallback_used is False
    assert result.fields["items"][0]["qty"] == Decimal("2.5")
    assert isinstance(result.fields["items"][0]["qty"], Decimal)
    assert result.ai_model_used == "gpt-4o-mini-2024-07-18"
    assert result.item_count == 1


def test_llm_integer_qty_is_not_shown_as_float(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real gpt-4o-mini returns 2.0 for "두판" — the candidate shows 2, not 2.0."""
    whole = LLM_CANDIDATE.model_copy(update={"items": [{**LLM_CANDIDATE.items[0].model_dump(), "qty": 2.0}]})
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm",
                        lambda u, *, settings: LLMParseResult(candidate=whole, model="m", processing_time_ms=1))
    qty = parse_pipeline.structure_utterance(UTTERANCE, _settings()).fields["items"][0]["qty"]
    assert str(qty) == "2"


def test_llm_timeout_falls_back_to_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE.md §4-3: LLM over 5s → Rule, still a usable candidate."""
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_timeout)
    result = parse_pipeline.structure_utterance(UTTERANCE, _settings())
    assert result.parser_type == "mock"
    assert result.is_fallback_used is True
    assert result.fields["counterparty"] == "김씨"


def test_llm_bug_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only LLMParseError falls back; a programming error surfaces (CLAUDE.md §3②)."""
    def bug(*a, **k):
        raise KeyError("unexpected")
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", bug)
    with pytest.raises(KeyError):
        parse_pipeline.structure_utterance(UTTERANCE, _settings())


def test_parse_api_fallback_is_200_and_stored(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: _settings())
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_timeout)
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": UTTERANCE}, headers=_headers())
    assert res.status_code == 200
    assert res.json()["is_fallback_used"] is True
    assert db.scalars(select(LedgerEntry)).one().is_fallback_used is True


def test_parse_api_llm_path_still_blocks_multiple_counterparties(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    """The LLM schema has one counterparty, so two named buyers must still be a 400 (G4)."""
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: _settings())
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_ok)
    text = "김씨에게 배추 3박스 30000원 박씨한테 무 2개 4000원 외상"
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": text}, headers=_headers())
    assert res.status_code == 400
    assert res.json()["error_code"] == "MULTIPLE_COUNTERPARTIES_DETECTED"
    assert db.scalars(select(LedgerEntry)).first() is None


def test_parse_api_llm_path_blocks_linked_counterparties(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """S-11: the LLM keeps only the first of "A랑 B한테" — the borrowed Rule count still makes it a 400."""
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: _settings())
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_ok)
    text = "김씨랑 박씨한테 배추 3박스씩 30000원씩 외상"
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": text}, headers=_headers())
    assert res.status_code == 400
    assert (res.json()["error_code"], res.json()["partial_candidate"]) == ("MULTIPLE_COUNTERPARTIES_DETECTED", None)
    assert db.scalars(select(LedgerEntry)).first() is None


def test_parse_api_llm_stores_model_name(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: _settings())
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_ok)
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": UTTERANCE}, headers=_headers())
    assert res.status_code == 200
    assert res.json()["parser_type"] == "llm"
    row = db.scalars(select(LedgerEntry)).one()
    assert row.parser_type == "llm"
    assert row.ai_model_used == "gpt-4o-mini-2024-07-18"


# ---------------- /parse-audio ----------------

def _upload(data: bytes = b"\x1aE\xdf\xa3fake-webm", mime: str = "audio/webm;codecs=opus"):
    return client.post("/api/v1/ledger/parse-audio", files={"file": ("rec.webm", data, mime)}, headers=_headers())


def test_audio_transcript_goes_through_text_pipeline(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    seen = {}

    def fake_transcribe(audio: bytes, filename: str, mime: str) -> str:
        seen.update(size=len(audio), mime=mime)
        return UTTERANCE
    monkeypatch.setattr(ledger_api, "transcribe", fake_transcribe)
    res = _upload()
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "candidate"
    assert body["utterance_text"] == UTTERANCE
    assert body["counterparty"] == "김씨"
    assert seen["mime"] == "audio/webm"  # codecs suffix stripped (iPhone sends "audio/webm; codecs=opus")
    assert db.scalars(select(LedgerEntry)).one().utterance_text == UTTERANCE


def test_audio_stt_failure_is_504_and_stores_nothing(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    def fail(*a, **k):
        raise STTError("timeout")
    monkeypatch.setattr(ledger_api, "transcribe", fail)
    res = _upload()
    assert res.status_code == 503
    assert res.json()["error_code"] == "EXTERNAL_API_TIMEOUT"
    assert db.scalars(select(LedgerEntry)).first() is None


def test_audio_unsupported_type_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ledger_api, "transcribe", lambda *a, **k: UTTERANCE)
    res = _upload(mime="video/quicktime")
    assert res.status_code == 400
    assert res.json()["error_code"] == "AUDIO_INVALID"


def test_audio_too_large_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ledger_api, "get_settings", lambda: _settings(stt_max_bytes=10))
    monkeypatch.setattr(ledger_api, "transcribe", lambda *a, **k: UTTERANCE)
    res = _upload(data=b"x" * 11)
    assert res.status_code == 400
    assert res.json()["error_code"] == "AUDIO_INVALID"


def test_audio_requires_idempotency_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ledger_api, "transcribe", lambda *a, **k: UTTERANCE)
    headers = _headers()
    headers.pop("X-Idempotency-Key")
    res = client.post("/api/v1/ledger/parse-audio",
                      files={"file": ("rec.webm", b"abc", "audio/webm")}, headers=headers)
    assert res.status_code == 400
    assert res.json()["error_code"] == "IDEMPOTENCY_KEY_REQUIRED"


# PM #39 리뷰: the Rule path reads the same normalized text as the LLM path (KO_NUMERAL_NORMALIZE)
@pytest.mark.parametrize(
    ("text", "total", "name"),
    [("솔바람한테 접시 5개 1만원 현금", 10000, "접시"), ("솔바람한테 대파 3단 3만6천원 외상", 36000, "대파"),
     ("솔바람한테 양파 2망 구천원 현금", 9000, "양파")],
)
def test_rule_path_reads_korean_amounts_when_normalization_is_on(text: str, total: int, name: str) -> None:
    from app.services.parse_mock import mock_parse_utterance

    on = parse_pipeline.structure_utterance(text, settings=_settings(llm_parse_enabled=False, ko_numeral_normalize=True))
    off = parse_pipeline.structure_utterance(text, settings=_settings(llm_parse_enabled=False, ko_numeral_normalize=False))

    assert (on.fields["total_amount"], on.fields["items"][0]["name"]) == (total, name)
    assert on.utterance_text == text  # parse input only — the original is kept (DL-05)
    assert off.fields == mock_parse_utterance(text).fields  # off: unchanged behaviour


def test_parse_api_keeps_the_original_utterance_with_normalized_rule(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: _settings(llm_parse_enabled=False, ko_numeral_normalize=True))
    text = "솔바람한테 접시 5개 1만원 현금"
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": text}, headers=_headers())
    assert res.status_code == 200
    assert (res.json()["total_amount"], res.json()["utterance_text"]) == (10000, text)
    assert db.scalars(select(LedgerEntry)).one().utterance_text == text


@pytest.mark.parametrize("normalize", [True, False])
def test_normalization_does_not_change_counterparty_count(monkeypatch: pytest.MonkeyPatch, normalize: bool) -> None:
    """Two buyers stay two (MULTIPLE_COUNTERPARTIES 400) on the Rule path and on the LLM path's borrowed count."""
    text = "솔바람한테 배추 2포기 1만원 외상이고 새벽상회한테 무 3개 9천원 현금"
    rule = parse_pipeline.structure_utterance(text, settings=_settings(llm_parse_enabled=False, ko_numeral_normalize=normalize))
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_ok)
    llm = parse_pipeline.structure_utterance(text, settings=_settings(ko_numeral_normalize=normalize))
    assert (rule.counterparty_count, llm.counterparty_count) == (2, 2)


def _llm_three_items(utterance: str, *, settings: Settings) -> LLMParseResult:
    """The LLM drops one of four spoken items and leaves item amounts empty (seed2-117 r2·r3)."""
    items = [{"name": n, "qty": q, "unit": u, "unit_price": None, "amount": None}
             for n, q, u in (("대파", 7, "근"), ("고추", 3, "근"), ("두부", 2, "통"))]
    candidate = LedgerExtraction(counterparty="솔바람", items=items, total_amount=200000, credit=True)
    return LLMParseResult(candidate=candidate, model="gpt-4o-mini", processing_time_ms=900)


def test_parse_api_llm_path_blocks_items_dropped_by_the_llm(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """#120: 4 items spoken, 3 returned → the count from the utterance still makes it 품목 초과 (no partial save)."""
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: _settings())
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_three_items)
    text = "솔바람한테 대파 7근, 고추 3근, 두부 2통, 사과 3박스 20만원 외상"
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": text}, headers=_headers())
    assert res.status_code == 400
    assert res.json()["error_code"] == "EXCEED_MAX_ITEMS"
    assert db.scalars(select(LedgerEntry)).first() is None


def _llm_one_shop_two_items(utterance: str, *, settings: Settings) -> LLMParseResult:
    """PM 10/26: the LLM puts both shops' items under the first shop and drops the quantities."""
    items = [{"name": n, "qty": None, "unit": None, "unit_price": None, "amount": None} for n in ("배추", "무")]
    candidate = LedgerExtraction(counterparty="가나상회", items=items, total_amount=None, credit=True)
    return LLMParseResult(candidate=candidate, model="gpt-4o-mini", processing_time_ms=900)


def _llm_three_of_four_listed(utterance: str, *, settings: Settings) -> LLMParseResult:
    """PM 10/26: four names sharing "한 박스씩" come back as three items (쪽파 dropped)."""
    items = [{"name": n, "qty": 1, "unit": "박스", "unit_price": None, "amount": None} for n in ("배추", "무", "대파")]
    candidate = LedgerExtraction(counterparty="가나상회", items=items, total_amount=50000, credit=True)
    return LLMParseResult(candidate=candidate, model="gpt-4o-mini", processing_time_ms=900)


@pytest.mark.parametrize(("llm", "text", "error_code"), [
    (_llm_one_shop_two_items, "가나상회 배추 삼만 원 다라식당 무 이만 원 외상", "MULTIPLE_COUNTERPARTIES_DETECTED"),
    (_llm_three_of_four_listed, "가나상회 배추 무 대파 쪽파 한 박스씩 오만 원 외상", "EXCEED_MAX_ITEMS"),
])
def test_parse_api_llm_path_blocks_run_on_sentences(
    llm, text: str, error_code: str, monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    """PM 10/26: said in one breath, the LLM answer looked like one shop / three items — the counts from the
    utterance still give the re-say notice, and nothing is stored (no question that saves 무 under 가나상회)."""
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: _settings())
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", llm)
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": text}, headers=_headers())
    assert (res.status_code, res.json()["error_code"]) == (400, error_code)
    assert db.scalars(select(LedgerEntry)).first() is None


def test_llm_path_does_not_count_a_corrected_quantity_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    """#120: "7근 아니 8근" is one item — 3 items with a correction stay 3 (the Rule quantity count says 4)."""
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_three_items)
    result = parse_pipeline.structure_utterance("솔바람한테 대파 7개 아니 8개, 고추 3박스, 두부 2팩 20만원 외상", _settings())
    assert result.item_count == 3


def _llm_credit_30000(utterance: str, *, settings: Settings) -> LLMParseResult:
    """The LLM saves the whole 30000 as 외상 for a partial payment (#115)."""
    items = [{"name": "양파", "qty": 3, "unit": "박스", "unit_price": None, "amount": 30000}]
    candidate = LedgerExtraction(counterparty="솔바람", items=items, total_amount=30000, credit=True)
    return LLMParseResult(candidate=candidate, model="gpt-4o-mini", processing_time_ms=900)


def test_parse_api_llm_path_rejects_a_clear_partial_payment(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """PR #128 PM 요청: "2만원 받고 남은 1만원 외상" → UNSUPPORTED_TRANSACTION (Rule judgement on the utterance),
    nothing stored — not 외상 30000."""
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: _settings())
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_credit_30000)
    text = "솔바람 양파 3박스 2만원 받고 남은 1만원 외상 합계 3만원"
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": text}, headers=_headers())
    assert res.status_code == 400
    assert res.json()["error_code"] == "UNSUPPORTED_TRANSACTION"
    assert db.scalars(select(LedgerEntry)).first() is None


def _llm_typed(kind: str | None):
    """LLM result with a transaction type, as with LLM_TRANSACTION_TYPE on (None = flag off)."""
    def parse(utterance: str, *, settings: Settings) -> LLMParseResult:
        return LLMParseResult(candidate=LLM_CANDIDATE, model="gpt-4o-mini", processing_time_ms=900,
                              transaction_type=kind)
    return parse


@pytest.mark.parametrize("rule_type, llm_type, expected", [
    (None, None, None),                            # flag off: main behaviour
    ("partial_payment", None, "partial_payment"),  # flag off: #128 Rule as in main
    (None, "sale", None),
    (None, "purchase", "purchase"),
    (None, "return", "return"),
    (None, "partial_payment", None),               # partial payment is the Rule's only (PM 10/14)
    ("partial_payment", "sale", "partial_payment"),
    ("partial_payment", "purchase", "partial_payment"),
])
def test_combined_transaction_type(rule_type, llm_type, expected) -> None:
    text = "솔바람에서 무 3개 3000원에 사왔는데 반품했어"  # both words, so only the types decide here
    assert parse_pipeline.combined_transaction_type(rule_type, llm_type, text) == expected


@pytest.mark.parametrize("text", ["농협마트 감자 20킬로 킬로당 1500원 현금", "솔바람 무 3개 3000원 외상",
                                  "솔바람에 무 3개 팔았어 3000원 계좌로"])
@pytest.mark.parametrize("llm_type", ["purchase", "return"])
def test_llm_type_without_its_word_is_ignored(text, llm_type) -> None:
    """2차 (PM 10/14): plain sales the model called 매입 (Dev 030 류) are not refused."""
    assert parse_pipeline.combined_transaction_type(None, llm_type, text) is None


@pytest.mark.parametrize("llm_type, text", [
    ("purchase", "새벽상회에서 고등어 10마리 7만원에 샀어"),
    ("purchase", "도매시장에서 배추 5망 사 왔어 5만원"),
    ("purchase", "배추 5망 사왔어 5만원 현금"),
    ("purchase", "오늘 매입 배추 5망 5만원"),
    ("return", "어제 판 사과 1박스 반품됐어 3만원"),
    ("return", "솔바람 귤 2박스 환불해 줬어 4만원"),
    ("return", "솔바람 주문 취소 귤 2박스 4만원"),
    ("return", "솔바람한테 판 귤 2박스 돌려받았어"),
    ("return", "솔바람 귤 2박스 물렀어 4만원"),
])
def test_llm_type_with_its_word_counts(llm_type, text) -> None:
    """131~134 류: a word for the kind is in the utterance → the model's purchase/return is used."""
    assert parse_pipeline.combined_transaction_type(None, llm_type, text) == llm_type


def test_hoesa_imnida_is_not_a_purchase_word() -> None:
    """PM 10/14: "사입" was dropped — "회사입니다" in a sale must not let the model's purchase through."""
    text = "저희 회사입니다 솔바람에 배추 5망 5만원 현금으로 팔았어요"
    assert parse_pipeline.combined_transaction_type(None, "purchase", text) is None


def test_purchase_word_does_not_make_a_return() -> None:
    assert parse_pipeline.combined_transaction_type(None, "return", "배추 5망 사왔어 5만원") is None


@pytest.mark.parametrize("kind", [None, "sale"])
def test_llm_path_without_an_unsupported_type_is_unchanged(monkeypatch: pytest.MonkeyPatch, kind) -> None:
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_typed(kind))
    assert parse_pipeline.structure_utterance(UTTERANCE, _settings()).transaction_type is None


def test_parse_api_llm_purchase_is_unsupported(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """DL-30 (PM 10/14): the model says 매입 → 400 UNSUPPORTED_TRANSACTION, nothing stored."""
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: _settings())
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_typed("purchase"))
    text = "김씨에게 배추 3박스 30000원 외상으로 사왔어"
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": text}, headers=_headers())
    assert res.status_code == 400
    assert res.json()["error_code"] == "UNSUPPORTED_TRANSACTION"
    assert db.scalars(select(LedgerEntry)).first() is None


def test_parse_api_llm_purchase_without_its_word_is_a_sale(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """2차 (PM 10/14): no 샀·사왔·매입 → the model's purchase is ignored, the sale goes on as before."""
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: _settings())
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_typed("purchase"))
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": UTTERANCE}, headers=_headers())
    assert res.json().get("error_code") != "UNSUPPORTED_TRANSACTION"


def test_rule_partial_payment_wins_over_an_llm_sale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm_typed("sale"))
    text = "솔바람 양파 3박스 2만원 받고 남은 1만원 외상 합계 3만원"
    assert parse_pipeline.structure_utterance(text, _settings()).transaction_type == "partial_payment"
