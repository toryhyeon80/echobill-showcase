"""Claude adapter with a fake HTTP transport — no API call (PM 10/22)."""

import json
import time
from types import SimpleNamespace

import httpx
import pytest

import eval.providers.claude_parse as claude
from app.services.parse_llm import LLMParseError, LLMTimeoutError
from eval.providers.claude_parse import ClaudeRateLimitError, parse_utterance_with_claude


UTTERANCE = "가나상회 무 세 개 현금 삼천 원"
SECRET = "fake-anthropic-key-should-never-appear"
CANDIDATE = {
    "counterparty": "가나상회",
    "items": [{"name": "무", "qty": 3, "unit": "개", "unit_price": None, "amount": 3000}],
    "total_amount": 3000,
    "credit": False,
}


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(claude, "_api_key", lambda: SECRET)


def _settings(timeout: float = 5.0) -> SimpleNamespace:
    return SimpleNamespace(llm_model="claude-haiku-5-5", llm_temperature=0.0, llm_timeout_seconds=timeout,
                           llm_max_completion_tokens=800, ko_numeral_normalize=True)


def _client(handler, sent: list) -> httpx.Client:
    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(record))


def _ok(text: str, stop_reason: str = "end_turn") -> httpx.Response:
    return httpx.Response(200, json={
        "model": "claude-haiku-5-5", "stop_reason": stop_reason,
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 320, "output_tokens": 50},
    })


def test_schema_valid_response_becomes_candidate_with_tokens():
    sent: list = []
    client = _client(lambda _: _ok(json.dumps(CANDIDATE, ensure_ascii=False)), sent)

    result = parse_utterance_with_claude(UTTERANCE, settings=_settings(), client=client)

    assert result.candidate.model_dump(mode="json") == CANDIDATE
    assert (result.model, result.response_model) == ("claude-haiku-5-5", "claude-haiku-5-5")
    assert (result.prompt_tokens, result.completion_tokens) == (320, 50)
    request = sent[0]
    assert request.headers["x-api-key"] == SECRET and request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    # Same input as production; thinking off; temperature not sent (Haiku 5.5 rejects non-default values).
    assert body["messages"] == [{"role": "user", "content": "가나상회 무 3개 현금 3000원"}]
    assert body["thinking"] == {"type": "disabled"}
    assert "temperature" not in body
    assert body["output_config"]["format"]["type"] == "json_schema"


def test_schema_closes_every_object_and_keeps_nulls():
    schema = claude.response_schema()
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text and '"null"' in text
    assert schema["additionalProperties"] is False
    assert schema["properties"]["items"]["items"]["additionalProperties"] is False


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_refusal_or_truncation_is_a_failure_not_a_candidate(stop_reason):
    client = _client(lambda _: _ok(json.dumps(CANDIDATE, ensure_ascii=False), stop_reason), [])
    with pytest.raises(LLMParseError, match=stop_reason):
        parse_utterance_with_claude(UTTERANCE, settings=_settings(), client=client)


def test_rate_limit_is_its_own_error():
    client = _client(lambda _: httpx.Response(429, json={"type": "error"}), [])
    with pytest.raises(ClaudeRateLimitError):
        parse_utterance_with_claude(UTTERANCE, settings=_settings(), client=client)


def test_timeout_is_over_deadline():
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(LLMTimeoutError, match="over_deadline"):
        parse_utterance_with_claude(UTTERANCE, settings=_settings(), client=_client(slow, []))



def test_answer_slower_than_the_deadline_is_over_deadline():
    """httpx timeouts are per phase; the whole call must still fit the deadline, as in the Gemini adapter."""
    def slow_but_answers(_request):
        time.sleep(0.05)
        return _ok(json.dumps(CANDIDATE, ensure_ascii=False))

    with pytest.raises(LLMTimeoutError, match="over_deadline"):
        parse_utterance_with_claude(UTTERANCE, settings=_settings(timeout=0.01), client=_client(slow_but_answers, []))


def test_refusal_without_a_text_block_is_reported_as_refusal():
    response = httpx.Response(200, json={"model": "claude-haiku-5-5", "stop_reason": "refusal", "content": []})
    with pytest.raises(LLMParseError, match="stop_reason=refusal"):
        parse_utterance_with_claude(UTTERANCE, settings=_settings(), client=_client(lambda _: response, []))

@pytest.mark.parametrize("response", [
    httpx.Response(529, text="overloaded"),
    httpx.Response(200, json={"content": []}),
    httpx.Response(200, json={"content": [{"type": "text", "text": "not json"}]}),
    httpx.Response(200, json={"content": [{"type": "text", "text": ""}]}),
])
def test_other_failures_are_parse_errors_without_input_or_key(response):
    with pytest.raises(LLMParseError) as info:
        parse_utterance_with_claude(UTTERANCE, settings=_settings(), client=_client(lambda _: response, []))
    assert UTTERANCE not in str(info.value) and SECRET not in str(info.value)


def test_missing_key_fails_before_any_call(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(claude, "_api_key", lambda: None)
    sent: list = []
    with pytest.raises(LLMParseError, match="ANTHROPIC_API_KEY"):
        parse_utterance_with_claude(UTTERANCE, settings=_settings(), client=_client(lambda _: _ok("{}"), sent))
    assert sent == []
