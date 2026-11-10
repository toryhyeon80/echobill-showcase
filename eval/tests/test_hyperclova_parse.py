"""HyperCLOVA X adapter with a fake HTTP transport — no API call (PM 10/22)."""

import json
import time
from types import SimpleNamespace

import httpx
import pytest

import eval.providers.hyperclova_parse as hcx
from app.services.parse_llm import LLMParseError, LLMTimeoutError
from eval.providers.hyperclova_parse import HyperClovaRateLimitError, parse_utterance_with_hyperclova


UTTERANCE = "가나상회 무 세 개 현금 삼천 원"
SECRET = "fake-clova-key-should-never-appear"
CANDIDATE = {
    "counterparty": "가나상회",
    "items": [{"name": "무", "qty": 3, "unit": "개", "unit_price": None, "amount": 3000}],
    "total_amount": 3000,
    "credit": False,
}


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(hcx, "_api_key", lambda: SECRET)


def _settings(timeout: float = 5.0) -> SimpleNamespace:
    return SimpleNamespace(llm_model="HCX-007", llm_temperature=0.0, llm_timeout_seconds=timeout,
                           llm_max_completion_tokens=800, ko_numeral_normalize=True)


def _client(handler, sent: list) -> httpx.Client:
    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(record))


def _ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={
        "status": {"code": "20000"},
        "result": {"message": {"role": "assistant", "content": content},
                   "usage": {"promptTokens": 300, "completionTokens": 45}},
    })


def test_schema_valid_response_becomes_candidate_with_tokens():
    sent: list = []
    client = _client(lambda _: _ok(json.dumps(CANDIDATE, ensure_ascii=False)), sent)

    result = parse_utterance_with_hyperclova(UTTERANCE, settings=_settings(), client=client)

    assert result.candidate.model_dump(mode="json") == CANDIDATE
    assert (result.model, result.response_model) == ("HCX-007", "HCX-007")
    assert (result.prompt_tokens, result.completion_tokens) == (300, 45)
    request = sent[0]
    assert str(request.url).endswith("/v3/chat-completions/HCX-007")
    assert request.headers["Authorization"] == f"Bearer {SECRET}"
    body = json.loads(request.content)
    # Same input as production: sanitized + numeral-normalized text; reasoning off; JSON schema output.
    assert body["messages"][1] == {"role": "user", "content": "가나상회 무 3개 현금 3000원"}
    assert body["thinking"] == {"effort": "none"}
    assert body["responseFormat"]["type"] == "json"


def test_missing_optional_fields_are_read_as_null():
    """"모르면 비워 둠": a field the model left out is null, not a guess."""
    partial = {"items": [{"name": "무", "qty": 3, "unit": "개"}]}
    client = _client(lambda _: _ok(json.dumps(partial, ensure_ascii=False)), [])

    candidate = parse_utterance_with_hyperclova(UTTERANCE, settings=_settings(), client=client).candidate

    dumped = candidate.model_dump(mode="json")
    assert (dumped["counterparty"], dumped["total_amount"], dumped["credit"]) == (None, None, None)
    assert dumped["items"][0]["unit_price"] is None and dumped["items"][0]["amount"] is None


def test_schema_keeps_production_nulls_and_required_fields():
    """Making nullable fields optional made HCX omit total_amount (37 of 42 normal Dev sentences, PM 10/22)."""
    text = json.dumps(hcx.response_schema())
    assert "$ref" not in text and "$defs" not in text
    schema = hcx.response_schema()
    assert {"counterparty", "total_amount", "credit", "items"} <= set(schema.get("required", []))
    assert {"type": "null"} in schema["properties"]["total_amount"]["anyOf"]
    assert "title" not in text and "description" not in text


def test_rate_limit_is_its_own_error():
    client = _client(lambda _: httpx.Response(429, json={"status": {"code": "42901"}}), [])
    with pytest.raises(HyperClovaRateLimitError):
        parse_utterance_with_hyperclova(UTTERANCE, settings=_settings(), client=client)


def test_timeout_is_over_deadline():
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(LLMTimeoutError, match="over_deadline"):
        parse_utterance_with_hyperclova(UTTERANCE, settings=_settings(), client=_client(slow, []))



def test_answer_slower_than_the_deadline_is_over_deadline():
    """httpx timeouts are per phase; the whole call must still fit the deadline, as in the Gemini adapter."""
    def slow_but_answers(_request):
        time.sleep(0.05)
        return _ok(json.dumps(CANDIDATE, ensure_ascii=False))

    with pytest.raises(LLMTimeoutError, match="over_deadline"):
        parse_utterance_with_hyperclova(UTTERANCE, settings=_settings(timeout=0.01), client=_client(slow_but_answers, []))

@pytest.mark.parametrize("response", [
    httpx.Response(500, text="server error"),
    httpx.Response(200, json={"result": {}}),
    httpx.Response(200, json={"result": {"message": {"content": "not json"}}}),
    httpx.Response(200, json={"result": {"message": {"content": ""}}}),
])
def test_other_failures_are_parse_errors_without_input_or_key(response):
    with pytest.raises(LLMParseError) as info:
        parse_utterance_with_hyperclova(UTTERANCE, settings=_settings(), client=_client(lambda _: response, []))
    assert UTTERANCE not in str(info.value) and SECRET not in str(info.value)


def test_missing_key_fails_before_any_call(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(hcx, "_api_key", lambda: None)
    sent: list = []
    with pytest.raises(LLMParseError, match="CLOVASTUDIO_API_KEY"):
        parse_utterance_with_hyperclova(UTTERANCE, settings=_settings(), client=_client(lambda _: _ok("{}"), sent))
    assert sent == []
