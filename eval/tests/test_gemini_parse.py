"""DL-11 Gemini adapter with a fake client — no SDK, no API call."""

import json
import time
from types import SimpleNamespace

import pytest

import eval.providers.gemini_parse as gemini
from app.services.parse_llm import LLMParseError, LLMTimeoutError
from eval.providers.gemini_parse import GeminiRateLimitError, parse_utterance_with_gemini


UTTERANCE = "가나상회 무 세 개 현금 삼천 원"
SECRET = "fake-gemini-key-should-never-appear"
CANDIDATE = {
    "counterparty": "가나상회",
    "items": [{"name": "무", "qty": 3, "unit": "개", "unit_price": None, "amount": 3000}],
    "total_amount": 3000,
    "credit": False,
}


def _settings(timeout: float = 5.0) -> SimpleNamespace:
    return SimpleNamespace(llm_model="gemini-3.1-flash-lite", llm_temperature=0.0, llm_timeout_seconds=timeout,
                           llm_max_completion_tokens=800, ko_numeral_normalize=True)


class FakeClient:
    """Mimics `client.models.generate_content`; records what was sent."""

    def __init__(self, respond):
        self.sent = []
        self.models = SimpleNamespace(generate_content=self._generate)
        self._respond = respond

    def _generate(self, *, model, contents, config):
        self.sent.append({"model": model, "contents": contents, "config": config})
        return self._respond()


def _response(text: str, thoughts=None) -> SimpleNamespace:
    usage = SimpleNamespace(prompt_token_count=210, candidates_token_count=40, thoughts_token_count=thoughts)
    return SimpleNamespace(text=text, usage_metadata=usage, model_version="gemini-3.1-flash-lite-05-2026")


class ProviderError(Exception):
    """Shape of google.genai.errors.APIError: an HTTP code and a message that may echo input."""

    def __init__(self, code: int):
        super().__init__(f"{code} error for '{UTTERANCE}' key={SECRET}")
        self.code = code


def test_schema_valid_response_becomes_candidate_with_tokens_and_version():
    client = FakeClient(lambda: _response(json.dumps(CANDIDATE, ensure_ascii=False)))

    result = parse_utterance_with_gemini(UTTERANCE, settings=_settings(), client=client, config="cfg")

    assert result.candidate.model_dump(mode="json") == CANDIDATE
    assert result.model == "gemini-3.1-flash-lite"
    assert result.response_model == "gemini-3.1-flash-lite-05-2026"
    assert (result.prompt_tokens, result.completion_tokens, result.thoughts_tokens) == (210, 40, None)
    assert result.processing_time_ms >= 0
    # Same input as production: sanitized + numeral-normalized text, not the raw utterance.
    assert client.sent[0]["contents"] == "가나상회 무 3개 현금 3000원"
    assert client.sent[0]["model"] == "gemini-3.1-flash-lite"


def test_thinking_tokens_count_as_billed_output():
    client = FakeClient(lambda: _response(json.dumps(CANDIDATE, ensure_ascii=False), thoughts=12))

    result = parse_utterance_with_gemini(UTTERANCE, settings=_settings(), client=client, config="cfg")

    assert result.thoughts_tokens == 12
    assert result.completion_tokens == 52


def test_call_over_the_deadline_is_over_deadline():
    def slow():
        time.sleep(0.5)
        return _response(json.dumps(CANDIDATE))

    with pytest.raises(LLMTimeoutError, match="over_deadline"):
        parse_utterance_with_gemini(UTTERANCE, settings=_settings(timeout=0.05), client=FakeClient(slow), config="c")


def test_whole_call_deadline_stays_at_settings_even_with_the_10s_sdk_timeout(monkeypatch: pytest.MonkeyPatch):
    """S-17: the SDK client gets Gemini's 10 s minimum, but the adapter still cuts the call at settings' deadline."""
    def slow():
        time.sleep(0.5)
        return _response(json.dumps(CANDIDATE))

    made = []
    monkeypatch.setattr(gemini, "_make_client", lambda *_: made.append(True) or FakeClient(slow))

    started = time.perf_counter()
    with pytest.raises(LLMTimeoutError, match=r"over_deadline \(0.05s\)"):
        parse_utterance_with_gemini(UTTERANCE, settings=_settings(timeout=0.05), config="c")
    assert made == [True]
    assert time.perf_counter() - started < 0.4  # returned at the adapter deadline, not after the slow call


def test_sdk_client_uses_the_10s_http_timeout_and_no_retry(monkeypatch: pytest.MonkeyPatch):
    """Gemini rejects server deadlines under 10 s (400 INVALID_ARGUMENT), so 5 s must not reach the SDK."""
    pytest.importorskip("google.genai")
    from google import genai as sdk

    captured = {}
    monkeypatch.setattr(gemini, "_api_key", lambda: "fake")
    monkeypatch.setattr(sdk, "Client", lambda **kwargs: captured.update(kwargs) or "client")

    assert gemini._make_client() == "client"
    assert captured["http_options"].timeout == 10_000
    assert captured["http_options"].retry_options.attempts == 1
    gemini._make_client(5.0)  # a 5 s deadline still gets the 10 s minimum
    assert captured["http_options"].timeout == 10_000


def test_sdk_http_timeout_follows_a_longer_deadline(monkeypatch: pytest.MonkeyPatch):
    """30 s deadline measurement: the SDK must not cut the call at 10 s."""
    pytest.importorskip("google.genai")
    from google import genai as sdk

    captured = {}
    monkeypatch.setattr(gemini, "_api_key", lambda: "fake")
    monkeypatch.setattr(sdk, "Client", lambda **kwargs: captured.update(kwargs) or "client")

    gemini._make_client(30.0)
    assert captured["http_options"].timeout == 30_000
    assert captured["http_options"].retry_options.attempts == 1


@pytest.mark.parametrize(("code", "error", "kind"), [
    (429, GeminiRateLimitError, "rate_limited status=429"),
    (500, LLMParseError, "api_error ProviderError status=500"),
])
def test_provider_errors_keep_kind_only(code, error, kind, caplog):
    def fail():
        raise ProviderError(code)

    with pytest.raises(error) as caught:
        parse_utterance_with_gemini(UTTERANCE, settings=_settings(), client=FakeClient(fail), config="c")

    assert str(caught.value) == kind
    for text in (str(caught.value), caplog.text):
        assert UTTERANCE not in text and "가나상회" not in text and SECRET not in text


def test_response_outside_the_schema_is_a_failure_without_its_text():
    client = FakeClient(lambda: _response('{"counterparty": "가나상회", "items": "무"}'))

    with pytest.raises(LLMParseError, match="schema_invalid") as caught:
        parse_utterance_with_gemini(UTTERANCE, settings=_settings(), client=client, config="c")
    assert "가나상회" not in str(caught.value)


def test_empty_response_is_a_failure():
    with pytest.raises(LLMParseError, match="empty_response"):
        parse_utterance_with_gemini(UTTERANCE, settings=_settings(), client=FakeClient(lambda: _response("")),
                                    config="c")


def test_missing_key_fails_before_any_call(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gemini, "_api_key", lambda: None)

    with pytest.raises(LLMParseError, match="GEMINI_API_KEY is not configured"):
        parse_utterance_with_gemini(UTTERANCE, settings=_settings())


def test_generation_config_forces_schema_thinking_off_and_temperature():
    """Runs only where the SDK is installed (measurement venv); CI skips it."""
    pytest.importorskip("google.genai")
    from app.services.parse_llm import LedgerExtraction

    config = gemini._generation_config(_settings(), "시스템 프롬프트")

    assert config.thinking_config.thinking_budget == 0
    assert config.temperature == 0.0
    assert config.max_output_tokens == 800
    assert config.response_mime_type == "application/json"
    assert config.response_schema is LedgerExtraction
    assert config.system_instruction == "시스템 프롬프트"
