"""GPT-4o-mini ledger extraction tests."""

import hashlib
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.config import Settings
from app.services.normalize_ko import NORMALIZER_VERSION
from app.services.postprocess import POSTPROCESS_VERSION
from app.services.parse_llm import (
    LLMParseError,
    LLMTimeoutError,
    LedgerExtraction,
    LedgerExtractionWithType,
    _sanitize_utterance,
    load_system_prompt,
    parse_utterance_with_llm,
)


class FakeCompletions:
    """Record the SDK request and return a parsed structured response."""

    def __init__(self, response: object, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.request: dict[str, object] | None = None

    def parse(self, **kwargs: object) -> object:
        self.request = kwargs
        if self.error:
            raise self.error
        return self.response


def _client_with(response: object) -> tuple[object, FakeCompletions]:
    completions = FakeCompletions(response)
    client = SimpleNamespace(
        beta=SimpleNamespace(
            chat=SimpleNamespace(completions=completions),
        )
    )
    return client, completions


def test_parse_utterance_uses_gpt4o_mini_structured_output() -> None:
    """The parser requests the configured model and returns typed ledger fields."""
    parsed = LedgerExtraction.model_validate(
        {
            "counterparty": "대박마트",
            "items": [
                {
                    "name": "계란",
                    "qty": 2,
                    "unit": "판",
                    "unit_price": None,
                    "amount": 45000,
                }
            ],
            "total_amount": 45000,
            "credit": True,
        }
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=40),
        model="gpt-4o-mini-2024-07-18",
        system_fingerprint="fp_test",
    )
    client, completions = _client_with(response)
    settings = Settings(
        openai_api_key="test-key",
        llm_model="gpt-4o-mini",
        llm_timeout_seconds=5.0,
        llm_max_completion_tokens=800,
        _env_file=None,
    )

    result = parse_utterance_with_llm(
        "대박마트에 계란 두 판 사만오천 원 외상",
        settings=settings,
        client=client,
    )

    assert result.candidate == parsed
    assert result.model == "gpt-4o-mini"
    assert result.prompt_tokens == 120
    assert result.completion_tokens == 40
    assert result.response_model == "gpt-4o-mini-2024-07-18"
    assert result.system_fingerprint == "fp_test"
    assert result.processing_time_ms >= 0
    assert completions.request is not None
    assert completions.request["model"] == "gpt-4o-mini"
    assert completions.request["temperature"] == 0
    assert completions.request["timeout"] == 5.0
    assert completions.request["max_tokens"] == 800
    assert completions.request["response_format"] is LedgerExtraction


def test_quantity_schema_accepts_json_number_or_null_only() -> None:
    """Structured Outputs must not receive Decimal's string alternative."""
    schema = LedgerExtraction.model_json_schema()
    qty_schema = schema["$defs"]["LedgerExtractionItem"]["properties"]["qty"]

    assert qty_schema["anyOf"] == [{"type": "number"}, {"type": "null"}]


def test_parse_utterance_rejects_model_refusal() -> None:
    """A refusal is surfaced as a parser failure instead of an empty candidate."""
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=None, refusal="refused"))],
        usage=None,
    )
    client, _ = _client_with(response)
    settings = Settings(openai_api_key="test-key", _env_file=None)

    with pytest.raises(LLMParseError, match="refused"):
        parse_utterance_with_llm("거래 입력", settings=settings, client=client)


def test_parse_utterance_requires_api_key_before_calling_openai() -> None:
    """Missing credentials fail locally without making an external request."""
    client, completions = _client_with(object())
    settings = Settings(openai_api_key=None, _env_file=None)

    with pytest.raises(LLMParseError, match="OPENAI_API_KEY"):
        parse_utterance_with_llm("거래 입력", settings=settings, client=client)

    assert completions.request is None


def test_parse_utterance_maps_timeout_to_domain_error() -> None:
    """The five-second provider timeout is exposed distinctly for Rule fallback."""
    completions = FakeCompletions(object(), error=TimeoutError("timed out"))
    client = SimpleNamespace(
        beta=SimpleNamespace(chat=SimpleNamespace(completions=completions))
    )
    settings = Settings(openai_api_key="test-key", llm_timeout_seconds=5, _env_file=None)

    with pytest.raises(LLMTimeoutError, match="5"):
        parse_utterance_with_llm("거래 입력", settings=settings, client=client)


def test_default_client_disables_sdk_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK retries would restart the 5s timeout, so the default client must not retry."""
    captured: dict[str, object] = {}

    def fake_openai(**kwargs: object) -> object:
        captured.update(kwargs)
        raise RuntimeError("stop before network")

    monkeypatch.setattr("app.services.parse_llm.OpenAI", fake_openai)
    settings = Settings(openai_api_key="test-key", _env_file=None)

    with pytest.raises(RuntimeError):
        parse_utterance_with_llm("가나상회 무 세 개", settings=settings)

    assert captured["max_retries"] == 0
    assert captured["timeout"] == 5.0


def test_default_system_prompt_is_the_configs_file_unchanged() -> None:
    """Moving the prompt to configs/ must not change its text (baseline comparability)."""
    prompt = load_system_prompt(Settings(_env_file=None))
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == (
        "43ecf4886a074cf20e389a40f2da0369cf05126a9f9414ed3fa381368903ba4d"
    )


def test_prompt_file_and_temperature_come_from_settings(tmp_path: Path) -> None:
    """Prompt text and temperature are configuration, not code (CLAUDE.md rule 13)."""
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("테스트 지시\n", encoding="utf-8")
    parsed = LedgerExtraction.model_validate(
        {"counterparty": None, "items": [], "total_amount": None, "credit": None}
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))],
        usage=None,
    )
    client, completions = _client_with(response)
    settings = Settings(
        openai_api_key="test-key",
        llm_temperature=0.3,
        llm_system_prompt_file=str(prompt_file),
        _env_file=None,
    )

    parse_utterance_with_llm("가나상회 무 세 개", settings=settings, client=client)

    assert completions.request["temperature"] == 0.3
    assert completions.request["messages"][0] == {"role": "system", "content": "테스트 지시"}


def test_missing_prompt_file_is_a_parse_error_before_calling(tmp_path: Path) -> None:
    """A missing prompt file must fail as LLMParseError so the caller can fall back to Rule."""
    client, completions = _client_with(object())
    settings = Settings(
        openai_api_key="test-key",
        llm_system_prompt_file=str(tmp_path / "missing.txt"),
        _env_file=None,
    )
    with pytest.raises(LLMParseError, match="system prompt"):
        parse_utterance_with_llm("가나상회 무 세 개", settings=settings, client=client)
    assert completions.request is None


def _client_raising(error: Exception) -> object:
    completions = FakeCompletions(object(), error=error)
    return SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(completions=completions)))


def test_provider_error_becomes_parse_error_and_is_logged_without_utterance(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SDK errors fall back to Rule, but leave a masked log so a bad key is not hidden."""
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    error = openai.APIStatusError(
        "invalid key", response=httpx.Response(401, request=request), body=None
    )
    settings = Settings(openai_api_key="test-key", _env_file=None)

    with caplog.at_level(logging.WARNING, logger="app.services.parse_llm"):
        with pytest.raises(LLMParseError, match="APIStatusError"):
            parse_utterance_with_llm("가나상회 무 세 개", settings=settings, client=_client_raising(error))

    assert "APIStatusError" in caplog.text
    assert "401" in caplog.text
    assert "가나상회" not in caplog.text


def test_programming_error_is_not_disguised_as_parse_error() -> None:
    """A bug in our code must surface as a server error, not a silent Rule fallback."""
    settings = Settings(openai_api_key="test-key", _env_file=None)
    with pytest.raises(TypeError):
        parse_utterance_with_llm(
            "가나상회 무 세 개", settings=settings, client=_client_raising(TypeError("bug"))
        )


def test_refusal_text_is_not_copied_into_the_error() -> None:
    """Refusal text may quote the utterance, so it stays out of errors and result files."""
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=None, refusal="가나상회 관련 거절"))],
        usage=None,
    )
    client, _ = _client_with(response)
    settings = Settings(openai_api_key="test-key", _env_file=None)

    with pytest.raises(LLMParseError) as info:
        parse_utterance_with_llm("가나상회 무 세 개", settings=settings, client=client)
    assert "가나상회" not in str(info.value)


@pytest.mark.parametrize("raw, expected", [
    ("사과 2~3박스", "사과 2 3박스"),
    ("무 2🍎3개", "무 2 3개"),
    ("**가나상회** 무 세 개", "가나상회 무 세 개"),
])
def test_sanitize_replaces_symbols_with_space_so_numbers_do_not_merge(raw: str, expected: str) -> None:
    """Removing a symbol between digits must not turn "2~3" into the quantity 23."""
    assert _sanitize_utterance(raw) == expected


class SlowCompletions(FakeCompletions):
    """Simulate a provider that keeps each network phase under the SDK timeout but is slow overall."""

    def __init__(self, delay_seconds: float) -> None:
        super().__init__(object())
        self.delay_seconds = delay_seconds

    def parse(self, **kwargs: object) -> object:
        self.request = kwargs
        time.sleep(self.delay_seconds)
        return self.response


def test_whole_call_is_cut_at_the_llm_deadline() -> None:
    """The SDK timeout is per phase, so the parser itself must stop waiting at the deadline (rule 3)."""
    client = SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(completions=SlowCompletions(1.5))))
    settings = Settings(openai_api_key="test-key", llm_timeout_seconds=0.2, _env_file=None)

    started = time.perf_counter()
    with pytest.raises(LLMTimeoutError):
        parse_utterance_with_llm("가나상회 무 세 개", settings=settings, client=client)
    assert time.perf_counter() - started < 1.0


def _empty_candidate_response() -> object:
    parsed = LedgerExtraction.model_validate(
        {"counterparty": None, "items": [], "total_amount": None, "credit": None}
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))],
        usage=None,
    )


def test_ko_numeral_flag_off_sends_korean_numerals_unchanged() -> None:
    """A-2 Step 2: with the flag off the model sees the sanitized utterance as before."""
    client, completions = _client_with(_empty_candidate_response())
    settings = Settings(openai_api_key="test-key", ko_numeral_normalize=False, _env_file=None)

    result = parse_utterance_with_llm("배추 이만이천원 외상", settings=settings, client=client)

    assert completions.request["messages"][1]["content"] == "배추 이만이천원 외상"
    assert result.normalizer_version is None


def test_ko_numeral_flag_on_sends_arabic_numerals_and_records_version() -> None:
    """A-2 Step 2: with the flag on the model sees "22000원" and the version is recorded."""
    client, completions = _client_with(_empty_candidate_response())
    settings = Settings(openai_api_key="test-key", ko_numeral_normalize=True, _env_file=None)

    result = parse_utterance_with_llm("배추 이만이천원 외상", settings=settings, client=client)

    assert completions.request["messages"][1]["content"] == "배추 22000원 외상"
    assert result.normalizer_version == NORMALIZER_VERSION


def _null_credit_response() -> object:
    parsed = LedgerExtraction.model_validate(
        {"counterparty": "솔바람", "items": [{"name": "대파", "qty": None, "unit": None, "unit_price": 6000, "amount": None}],
         "total_amount": None, "credit": None}
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))],
        usage=None,
    )


def test_postprocess_flag_on_applies_rules_and_records_version() -> None:
    """A-4c 2장: LLM_POSTPROCESS on (with normalization on) → credit/unit-price rules run on the LLM output."""
    client, _ = _client_with(_null_credit_response())
    settings = Settings(openai_api_key="test-key", llm_postprocess=True, ko_numeral_normalize=True,
                        _env_file=None, app_env="development")

    result = parse_utterance_with_llm("솔바람 대파 6000원 달아놔", settings=settings, client=client)

    assert result.candidate.credit is True
    assert result.candidate.items[0].unit_price is None
    assert result.candidate.items[0].amount == 6000
    assert result.postprocess_version == POSTPROCESS_VERSION
    assert result.candidate.total_amount == 6000  # 규칙 5 (PM 10/16)
    assert result.postprocess_applied == ["credit_from_text", "unit_price_to_amount", "unit_price_cleared",
                                          "total_restored_spoken"]


def test_postprocess_flag_off_leaves_llm_output_unchanged() -> None:
    client, _ = _client_with(_null_credit_response())
    settings = Settings(openai_api_key="test-key", llm_postprocess=False, _env_file=None, app_env="development")

    result = parse_utterance_with_llm("솔바람 대파 6000원 달아놔", settings=settings, client=client)

    assert result.candidate.credit is None
    assert result.candidate.items[0].unit_price == 6000
    assert result.postprocess_version is None
    assert result.postprocess_applied == []


def _unit_price_only_response() -> object:
    parsed = LedgerExtraction.model_validate(
        {"counterparty": "솔바람", "items": [{"name": "배추", "qty": 3, "unit": "박스", "unit_price": 10000, "amount": None}],
         "total_amount": None, "credit": None}
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))],
        usage=None,
    )


def test_unit_price_rule_is_skipped_when_normalization_is_off() -> None:
    """PM #35 리뷰: without normalization "박스당 만원" has no digit, so the unit-price rule is skipped.
    PM 10/5: the credit rules still run (word-based) → version is recorded with the "-credit" suffix (credit rules only)."""
    client, _ = _client_with(_unit_price_only_response())
    settings = Settings(openai_api_key="test-key", llm_postprocess=True, ko_numeral_normalize=False,
                        _env_file=None, app_env="development")

    result = parse_utterance_with_llm("솔바람 배추 3박스 박스당 만원", settings=settings, client=client)

    assert result.candidate.items[0].unit_price == 10000
    assert result.candidate.items[0].amount is None
    assert result.postprocess_version == f"{POSTPROCESS_VERSION}-credit"
    assert result.postprocess_applied == []


def test_postprocess_keeps_a_normalized_unit_price_marker() -> None:
    """Same candidate with normalization on: "박스당 10000원" is a marker, so unit_price stays."""
    client, _ = _client_with(_unit_price_only_response())
    settings = Settings(openai_api_key="test-key", llm_postprocess=True, ko_numeral_normalize=True,
                        _env_file=None, app_env="development")

    result = parse_utterance_with_llm("솔바람 배추 3박스 박스당 만원", settings=settings, client=client)

    assert result.candidate.items[0].unit_price == 10000
    assert result.postprocess_version == POSTPROCESS_VERSION
    assert result.postprocess_applied == []


def test_startup_warns_when_postprocess_is_ignored(caplog: pytest.LogCaptureFixture) -> None:
    from app.main import warn_inactive_postprocess

    with caplog.at_level(logging.WARNING, logger="app.main"):
        warn_inactive_postprocess(Settings(llm_postprocess=True, ko_numeral_normalize=False,
                                           _env_file=None, app_env="development"))
        warn_inactive_postprocess(Settings(llm_postprocess=True, ko_numeral_normalize=True,
                                           _env_file=None, app_env="development"))

    assert [r.getMessage() for r in caplog.records] == ["KO_NUMERAL_NORMALIZE=false — 후처리 단가 규칙은 꺼지고 외상 규칙만 적용됨"]


# --- DL-30 transaction_type (LLM_TRANSACTION_TYPE, PM 10/14) ---

_CANDIDATE = {"counterparty": "솔바람", "items": [{"name": "무", "qty": 3, "unit": "개", "unit_price": None,
                                                "amount": 3000}], "total_amount": 3000, "credit": True}


def _response_with(parsed: object) -> object:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))], usage=None)


def test_transaction_type_flag_is_off_by_default_and_main_schema_has_no_such_field() -> None:
    """Flag off = main: same schema (four fields), nothing else sent or returned."""
    assert Settings(_env_file=None).llm_transaction_type is False
    assert set(LedgerExtraction.model_json_schema()["properties"]) == {"counterparty", "items", "total_amount", "credit"}


def test_flag_off_request_and_result_are_the_same_as_main() -> None:
    """An explicit false sends the very same request as the default settings (main) and returns no type."""
    requests, results = [], []
    for overrides in ({}, {"llm_transaction_type": False}):
        client, completions = _client_with(_response_with(LedgerExtraction.model_validate(_CANDIDATE)))
        settings = Settings(openai_api_key="test-key", _env_file=None, app_env="development", **overrides)
        results.append(parse_utterance_with_llm("솔바람 무 3개 3000원 외상", settings=settings, client=client))
        requests.append(dict(completions.request))
    assert requests[0] == requests[1]
    assert requests[0]["response_format"] is LedgerExtraction
    assert results[0].model_dump(exclude={"processing_time_ms"}) == results[1].model_dump(exclude={"processing_time_ms"})
    assert results[0].transaction_type is None


def test_flag_on_asks_for_the_type_and_keeps_it_out_of_the_candidate() -> None:
    parsed = LedgerExtractionWithType.model_validate({**_CANDIDATE, "transaction_type": "purchase"})
    client, completions = _client_with(_response_with(parsed))
    settings = Settings(openai_api_key="test-key", llm_transaction_type=True, _env_file=None, app_env="development")

    result = parse_utterance_with_llm("솔바람 무 3개 3000원 외상", settings=settings, client=client)

    assert completions.request["response_format"] is LedgerExtractionWithType
    assert result.transaction_type == "purchase"
    assert type(result.candidate) is LedgerExtraction
    assert "transaction_type" not in result.candidate.model_dump()


def test_type_schema_has_no_partial_payment() -> None:
    """A clear partial payment is the Rule's call (PR #128); the model cannot answer it."""
    with pytest.raises(ValueError):
        LedgerExtractionWithType.model_validate({**_CANDIDATE, "transaction_type": "partial_payment"})
