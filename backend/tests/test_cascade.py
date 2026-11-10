"""Cascade (팀 회의 10/23): signals, comparison, clearing, the Gemini call and the /parse path."""

import json
import time
import uuid
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import app
from app.models import LedgerEntry
from app.services import cascade, parse_llm, parse_pipeline
from app.services.cascade import CascadeError
from app.services.parse_llm import LedgerExtraction, LLMParseResult

client = TestClient(app)


def _settings(**overrides) -> Settings:
    base = {"app_env": "development", "openai_api_key": "test-key", "llm_parse_enabled": True,
            "cascade_enabled": True, "gemini_api_key": "gemini-test-key", "_env_file": None}
    return Settings(**{**base, **overrides})


def _cand(counterparty="가나상회", items=None, total=30000, credit=True) -> dict:
    items = items if items is not None else [{"name": "배추", "qty": 3, "unit": "박스", "unit_price": None,
                                               "amount": 30000}]
    return {"counterparty": counterparty, "items": items, "total_amount": total, "credit": credit}


def _nums(*values) -> set[Decimal]:
    return {Decimal(str(v)) for v in values}


# ---------------- signals ----------------

def test_no_signal_when_every_number_was_said() -> None:
    assert cascade.signals(_cand(), _nums(3, 30000), []) == []


def test_invented_qty_fires_number_grounding() -> None:
    cand = _cand(items=[{"name": "배추", "qty": 2.5, "unit": "박스", "unit_price": None, "amount": 30000}])
    assert cascade.signals(cand, _nums(3, 30000), []) == ["number_grounding"]


def test_product_or_sum_of_said_numbers_is_grounded() -> None:
    """3 boxes at 10000 → 30000 is a product of two numbers said."""
    assert not cascade.number_grounding_signal(_cand(), _nums(3, 10000))


def test_total_equal_to_item_amounts_is_grounded() -> None:
    items = [{"name": "배추", "qty": 1, "unit": None, "unit_price": None, "amount": 1000},
             {"name": "무", "qty": 1, "unit": None, "unit_price": None, "amount": 2000},
             {"name": "파", "qty": 1, "unit": None, "unit_price": None, "amount": 4000}]
    assert not cascade.number_grounding_signal(_cand(items=items, total=7000), _nums(1, 1000, 2000, 4000))


def test_restored_spoken_total_fires() -> None:
    assert cascade.signals(_cand(), _nums(3, 30000), ["total_restored_spoken"]) == ["total_restored"]


def test_total_mismatch_fires_only_on_one_won_or_more() -> None:
    items = [{"name": "배추", "qty": 3, "unit": None, "unit_price": 10000, "amount": 29000}]
    assert cascade.total_mismatch_signal(_cand(items=items, total=29000))
    two = [{"name": "배추", "qty": 1, "unit": None, "unit_price": None, "amount": 1000},
           {"name": "무", "qty": 1, "unit": None, "unit_price": None, "amount": 2000}]
    assert cascade.total_mismatch_signal(_cand(items=two, total=4000))
    assert not cascade.total_mismatch_signal(_cand(items=two, total=3000))


# ---------------- comparison and clearing ----------------

def test_same_answers_change_nothing_spacing_ignored() -> None:
    primary = _cand(counterparty="가나 상회")
    fields, labels = cascade.clear_differing(primary, _cand())
    assert (fields, labels) == (primary, [])


def test_differing_counterparty_is_emptied_not_replaced() -> None:
    fields, labels = cascade.clear_differing(_cand(), _cand(counterparty="다나상회"))
    assert fields["counterparty"] is None and labels == ["cascade_counterparty"]


def test_differing_credit_including_unknown_is_emptied() -> None:
    fields, labels = cascade.clear_differing(_cand(credit=True), _cand(credit=None))
    assert fields["credit"] is None and labels == ["cascade_credit"]


def test_differing_amount_empties_total_and_item_amounts() -> None:
    second = _cand(total=35000, items=[{"name": "배추", "qty": 3, "unit": "박스", "unit_price": None, "amount": 35000}])
    fields, labels = cascade.clear_differing(_cand(), second)
    assert fields["total_amount"] is None and fields["items"][0]["amount"] is None
    assert labels == ["cascade_total_amount"]


def test_differing_qty_empties_only_that_item() -> None:
    items = [{"name": "배추", "qty": 3, "unit": None, "unit_price": None, "amount": 1000},
             {"name": "무", "qty": 2, "unit": None, "unit_price": None, "amount": 2000}]
    other = [dict(items[0], qty=Decimal("3.0")), dict(items[1], qty=4)]
    fields, labels = cascade.clear_differing(_cand(items=items, total=3000), _cand(items=other, total=3000))
    assert [i["qty"] for i in fields["items"]] == [3, None]
    assert labels == ["cascade_item_qty"]


def test_differing_names_empty_the_names_the_other_lacks() -> None:
    items = [{"name": "배추", "qty": 1, "unit": None, "unit_price": None, "amount": 1000},
             {"name": "무", "qty": 1, "unit": None, "unit_price": None, "amount": 2000}]
    other = [dict(items[0]), dict(items[1], name="무우")]
    fields, labels = cascade.clear_differing(_cand(items=items, total=3000), _cand(items=other, total=3000))
    assert [i["name"] for i in fields["items"]] == ["배추", None]
    assert labels == ["cascade_item_name"]


def test_second_answer_with_an_extra_item_empties_every_name() -> None:
    """Nothing here is missing on the other side, so all names are emptied (the full edit form)."""
    extra = _cand(items=[*_cand()["items"], {"name": "무", "qty": 1, "unit": None, "unit_price": None, "amount": None}])
    fields, _ = cascade.clear_differing(_cand(), extra)
    assert [i["name"] for i in fields["items"]] == [None]


def test_qty_is_compared_on_shared_names_even_when_another_name_differs() -> None:
    """병철 #213: [사과 3, 배 2] vs [사과 5, 귤 2] asked only 배's name and saved 사과 3 unchecked."""
    items = [{"name": "사과", "qty": 3, "unit": None, "unit_price": None, "amount": 1000},
             {"name": "배", "qty": 2, "unit": None, "unit_price": None, "amount": 2000}]
    other = [dict(items[0], qty=5), dict(items[1], name="귤")]
    fields, labels = cascade.clear_differing(_cand(items=items, total=3000), _cand(items=other, total=3000))
    assert [(i["name"], i["qty"]) for i in fields["items"]] == [("사과", None), (None, 2)]
    assert labels == ["cascade_item_name", "cascade_item_qty"]


def _items(*pairs) -> list[dict]:
    return [{"name": n, "qty": q, "unit": None, "unit_price": None, "amount": None} for n, q in pairs]


def test_extra_item_on_the_other_side_still_asks_the_shared_qty() -> None:
    """병철 #226: [사과 3] vs [사과 5, 배 2] emptied the names first and then could not find 사과's qty."""
    fields, labels = cascade.clear_differing(_cand(items=_items(("사과", 3)), total=None),
                                             _cand(items=_items(("사과", 5), ("배", 2)), total=None))
    assert [(i["name"], i["qty"]) for i in fields["items"]] == [(None, None)]
    assert labels == ["cascade_item_name", "cascade_item_qty"]


def test_nameless_items_do_not_empty_a_qty_both_read_alike() -> None:
    """병철 #226: [이름 없음 2, 사과 3] vs [이름 없음 4, 배 3] must not empty 사과 3 — the nameless 2 vs 4 is asked (#232)."""
    fields, labels = cascade.clear_differing(_cand(items=_items((None, 2), ("사과", 3)), total=None),
                                             _cand(items=_items((None, 4), ("배", 3)), total=None))
    assert [(i["name"], i["qty"]) for i in fields["items"]] == [(None, None), (None, 3)]
    assert "cascade_item_qty" in labels


def test_nameless_item_with_different_qtys_is_asked() -> None:
    """병철 #232: [이름 없음 2] vs [이름 없음 4] kept 2 — an owner answering only the name saved a qty the models split on."""
    fields, labels = cascade.clear_differing(_cand(items=_items((None, 2)), total=None),
                                             _cand(items=_items((None, 4)), total=None))
    assert fields["items"][0]["qty"] is None and labels == ["cascade_item_qty"]


def test_qty_label_only_when_a_qty_was_actually_emptied() -> None:
    """병철 #226: [사과 None, 배 2] vs [사과 3, 배 2] — 사과's qty was already empty, so no cascade label."""
    fields, labels = cascade.clear_differing(_cand(items=_items(("사과", None), ("배", 2)), total=None),
                                             _cand(items=_items(("사과", 3), ("배", 2)), total=None))
    assert [i["qty"] for i in fields["items"]] == [None, 2] and labels == []


def test_report_counts_cascade_questions_without_a_correction_rate() -> None:
    """병철 #226: the emptied value is not stored, so 고침 is shown as '-' instead of a wrong 0% / 100%."""
    from app.services.ops_metrics import PeriodMetrics, _count_rules
    m = PeriodMetrics(label="t")
    _count_rules(m, ["cascade_credit", "cascade_total_amount"], {"credit": {"before": None, "after": True},
                 "total_amount": {"before": None, "after": 3000}}, set())
    assert m.rule_decided["cascade_credit"] == 1 and m.rule_corrected["cascade_credit"] == 0
    assert m.rule_corrected["cascade_total_amount"] == 0


def test_a_field_the_operating_answer_left_empty_is_not_labelled() -> None:
    """병철 #213: an empty field would have been asked anyway — not counted as the cascade's question."""
    fields, labels = cascade.clear_differing(_cand(credit=None), _cand(credit=True))
    assert fields["credit"] is None and labels == []


def test_clearing_does_not_modify_the_input() -> None:
    primary = _cand()
    cascade.clear_differing(primary, _cand(counterparty="다나상회", total=1))
    assert primary == _cand()


# ---------------- apply ----------------

INVENTED = _cand(items=[{"name": "배추", "qty": 2.5, "unit": "박스", "unit_price": None, "amount": 30000}])
TEXT = "가나상회 배추 3박스 30000원 외상"


def _never(*a, **k):
    raise AssertionError("second model must not be called")


def test_off_never_calls_the_second_model() -> None:
    assert cascade.apply(TEXT, INVENTED, [], _settings(cascade_enabled=False), ask=_never) == (INVENTED, [])


def test_no_signal_never_calls_the_second_model() -> None:
    assert cascade.apply(TEXT, _cand(), [], _settings(), ask=_never) == (_cand(), [])


def test_signal_and_same_answer_keeps_the_operating_answer() -> None:
    assert cascade.apply(TEXT, INVENTED, [], _settings(), ask=lambda u, s: INVENTED) == (INVENTED, [])


def test_signal_and_different_answer_asks_that_field(caplog: pytest.LogCaptureFixture) -> None:
    second = dict(INVENTED, credit=None)
    with caplog.at_level("WARNING"):
        fields, labels = cascade.apply(TEXT, INVENTED, [], _settings(), ask=lambda u, s: second)
    assert fields["credit"] is None and fields["counterparty"] == "가나상회" and labels == ["cascade_credit"]
    # 병철 #213: WARNING so the line reaches Railway (call rate · added time), with no values
    assert "cascade signals=" in caplog.text and "asked=cascade_credit" in caplog.text and "가나상회" not in caplog.text


def test_failed_second_call_keeps_the_operating_answer() -> None:
    def fail(u, s):
        raise CascadeError("over_deadline (3s)")
    assert cascade.apply(TEXT, INVENTED, [], _settings(), ask=fail) == (INVENTED, [])


def test_unexpected_error_keeps_the_operating_answer_and_logs_no_values(caplog: pytest.LogCaptureFixture) -> None:
    """수경 #197 리뷰: any error inside the cascade must not fail /parse."""
    def bug(u, s):
        raise KeyError("가나상회")
    with caplog.at_level("ERROR"):
        assert cascade.apply(TEXT, INVENTED, [], _settings(), ask=bug) == (INVENTED, [])
    assert "KeyError" in caplog.text and "가나상회" not in caplog.text


def test_unexpected_error_in_comparison_keeps_the_operating_answer(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("ERROR"):
        assert cascade.apply(TEXT, INVENTED, [], _settings(), ask=lambda u, s: {"items": "not a list"}) == (INVENTED, [])
    # 병철 #213: an error must not look like "no signal" — it leaves an ERROR line with its place
    assert "cascade failed unexpectedly" in caplog.text


def test_signal_error_is_logged_not_silently_skipped(monkeypatch: pytest.MonkeyPatch,
                                                     caplog: pytest.LogCaptureFixture) -> None:
    def broken(*a, **k):
        raise ValueError("bad number")
    monkeypatch.setattr(cascade, "signals", broken)
    with caplog.at_level("ERROR"):
        assert cascade.apply(TEXT, INVENTED, [], _settings(), ask=_never) == (INVENTED, [])
    assert "cascade failed unexpectedly: ValueError" in caplog.text


def test_enabled_without_key_fails_at_startup() -> None:
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        _settings(gemini_api_key=None)


# ---------------- Gemini call ----------------

def _gemini_ok(candidate: dict, seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        text = json.dumps(candidate, ensure_ascii=False)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_second_opinion_sends_the_same_input_and_postprocesses() -> None:
    seen: list[httpx.Request] = []
    raw = _cand()
    got = cascade.second_opinion(TEXT, _settings(), client=_gemini_ok(raw, seen))
    request = seen[0]
    body = json.loads(request.content)
    assert "gemini-test-key" not in str(request.url) and request.headers["x-goog-api-key"] == "gemini-test-key"
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert body["generationConfig"]["responseSchema"] == cascade.RESPONSE_SCHEMA
    assert body["contents"][0]["parts"][0]["text"] == parse_llm.build_llm_input(TEXT, _settings())
    assert got["counterparty"] == "가나상회" and got["total_amount"] == 30000


@pytest.mark.parametrize("response", [
    httpx.Response(429, json={}),
    httpx.Response(200, json={"candidates": []}),
    httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{\"counterparty\": 1}"}]}}]}),
    httpx.Response(200, content=b"not json"),
])
def test_second_opinion_failures_are_cascade_errors(response: httpx.Response) -> None:
    http = httpx.Client(transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(CascadeError):
        cascade.second_opinion(TEXT, _settings(), client=http)


def test_second_opinion_slower_than_the_deadline_is_a_cascade_error() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        time.sleep(0.3)
        return httpx.Response(200, json={})
    http = httpx.Client(transport=httpx.MockTransport(slow))
    with pytest.raises(CascadeError, match="over_deadline"):
        cascade.second_opinion(TEXT, _settings(cascade_timeout_seconds=0.05), client=http)


# ---------------- /parse ----------------

def _headers() -> dict[str, str]:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "cascade-user"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())}


def _llm(utterance: str, *, settings: Settings) -> LLMParseResult:
    return LLMParseResult(candidate=LedgerExtraction.model_validate(INVENTED), model="gpt-4o-mini",
                          processing_time_ms=900)


def test_parse_api_asks_the_differing_field_and_stores_nothing(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: _settings())
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm)
    monkeypatch.setattr(cascade, "second_opinion", lambda u, s: dict(INVENTED, counterparty="다나상회"))
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": TEXT}, headers=_headers())
    assert res.status_code == 400
    body = res.json()
    assert body["error_code"] == "MISSING_REQUIRED_FIELD" and "counterparty" in body["problem_fields"]
    assert body["partial_candidate"]["counterparty"] is None
    assert db.scalars(select(LedgerEntry).where(LedgerEntry.status == "confirmed")).first() is None


def test_processing_time_includes_the_second_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """병철 #213: llm p50/p95 must not hide up to 3 s of the second call on doubtful sentences."""
    def slow(utterance, fields, applied, settings):
        time.sleep(0.05)
        return fields, []
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm)
    monkeypatch.setattr(cascade, "apply", slow)
    result = parse_pipeline.structure_utterance(TEXT, _settings())
    # whole wall time from the start, like the fallback path (병철 #226) — the mocked LLM returns at once
    assert result.processing_time_ms >= 50


def test_ops_report_lists_cascade_labels() -> None:
    from app.services.ops_metrics import RULE_TARGET
    assert {label for label in cascade.LABELS.values()} <= RULE_TARGET.keys()


def test_parse_api_off_is_unchanged(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    monkeypatch.setattr(parse_pipeline, "get_settings", lambda: _settings(cascade_enabled=False))
    monkeypatch.setattr(parse_llm, "parse_utterance_with_llm", _llm)
    monkeypatch.setattr(cascade, "second_opinion", _never)
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": TEXT}, headers=_headers())
    assert res.status_code == 200 and res.json()["counterparty"] == "가나상회"
