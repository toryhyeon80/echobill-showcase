"""GPT-4o-mini ledger extraction service."""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from openai import APITimeoutError, OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings
from app.services.normalize_ko import NORMALIZER_VERSION, normalize_korean_numerals
from app.services.postprocess import (
    postprocess_active,
    postprocess_candidate,
    postprocess_label,
    unit_price_rules_active,
)


logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT_FILE = (
    Path(__file__).resolve().parents[3] / "configs" / "prompts" / "parse_llm_system.txt"
)


class LedgerExtractionItem(BaseModel):
    """One item extracted from a transaction utterance."""

    name: str | None
    qty: float | None
    unit: str | None
    unit_price: int | None
    amount: int | None


class LedgerExtraction(BaseModel):
    """Structured candidate fields produced by the language model."""

    counterparty: str | None
    items: list[LedgerExtractionItem]
    total_amount: int | None
    credit: bool | None


class LedgerExtractionWithType(LedgerExtraction):
    """LedgerExtraction plus the model's transaction kind (DL-30), sent only with LLM_TRANSACTION_TYPE on.

    No partial_payment: a clear partial payment is the Rule's call (PR #128). Kept out of the candidate.
    """

    transaction_type: Literal["sale", "purchase", "return"]


class LLMParseResult(BaseModel):
    """Parsed candidate plus measurements needed by later evaluation."""

    candidate: LedgerExtraction
    model: str
    processing_time_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    response_model: str | None = None
    system_fingerprint: str | None = None
    normalizer_version: str | None = None
    postprocess_version: str | None = None
    postprocess_applied: list[str] = []
    # DL-30: the model's transaction kind; None unless LLM_TRANSACTION_TYPE is on
    transaction_type: str | None = None


class LLMParseError(RuntimeError):
    """Raised when an LLM call cannot yield a usable candidate."""


class LLMTimeoutError(LLMParseError):
    """Raised when the configured LLM deadline is exceeded."""


def load_system_prompt(settings: Any) -> str:
    """Read the system prompt from configs; the file's trailing newline is not part of it."""
    configured = getattr(settings, "llm_system_prompt_file", None)
    path = Path(configured) if configured else DEFAULT_SYSTEM_PROMPT_FILE
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LLMParseError(f"system prompt file is not readable: {path.name}") from exc


def parse_utterance_with_llm(
    utterance: str,
    *,
    settings: Settings | None = None,
    client: Any = None,
) -> LLMParseResult:
    """Extract a typed ledger candidate and record latency and token usage."""
    settings = settings or get_settings()
    if not settings.openai_api_key or not settings.openai_api_key.get_secret_value():
        raise LLMParseError("OPENAI_API_KEY is not configured")

    system_prompt = load_system_prompt(settings)
    # getattr: eval tests pass lightweight settings objects without this field (flag off = main schema)
    with_type = bool(getattr(settings, "llm_transaction_type", False))
    cleaned = build_llm_input(utterance, settings)
    api_client = client or OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
    started = time.perf_counter()
    # The SDK timeout applies per network phase, so a slow connect plus a slow reply can
    # exceed it. Wait on a worker thread with a whole-call deadline instead (rule 3: LLM ≤5s → Rule).
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(
            api_client.beta.chat.completions.parse,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout_seconds,
            max_tokens=settings.llm_max_completion_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": cleaned},
            ],
            response_format=LedgerExtractionWithType if with_type else LedgerExtraction,
        )
        completion = future.result(timeout=settings.llm_timeout_seconds)
    except (APITimeoutError, TimeoutError) as exc:
        raise LLMTimeoutError(
            f"LLM request exceeded {settings.llm_timeout_seconds:g} seconds"
        ) from exc
    except (OpenAIError, ValidationError) as exc:
        # Provider and schema failures fall back to Rule; log type/status only (no utterance).
        logger.warning(
            "LLM parse failed: %s status=%s",
            type(exc).__name__,
            getattr(exc, "status_code", None),
        )
        raise LLMParseError(f"OpenAI request failed: {type(exc).__name__}") from exc
    finally:
        # Do not wait for an abandoned call; the SDK's own per-phase timeout ends that thread.
        executor.shutdown(wait=False, cancel_futures=True)

    message = completion.choices[0].message
    if message.refusal:
        # The refusal text may quote the utterance, so it is not copied into the error.
        raise LLMParseError("OpenAI model refused the request")
    if message.parsed is None:
        raise LLMParseError("OpenAI response did not contain structured output")

    candidate = message.parsed
    transaction_type = None
    if with_type:
        transaction_type = candidate.transaction_type
        candidate = LedgerExtraction.model_validate(candidate.model_dump(exclude={"transaction_type"}))
    postprocess_applied: list[str] = []
    postprocess_on = postprocess_active(settings.llm_postprocess, settings.ko_numeral_normalize)
    if postprocess_on:
        # Rule post-processing on the text actually sent (A-4c); result is re-validated by the schema
        data, postprocess_applied = postprocess_candidate(
            candidate.model_dump(), cleaned,
            unit_price_rules=unit_price_rules_active(settings.llm_postprocess, settings.ko_numeral_normalize),
        )
        candidate = LedgerExtraction.model_validate(data)

    usage = completion.usage
    return LLMParseResult(
        candidate=candidate,
        model=settings.llm_model,
        processing_time_ms=int((time.perf_counter() - started) * 1000),
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        response_model=getattr(completion, "model", None),
        system_fingerprint=getattr(completion, "system_fingerprint", None),
        normalizer_version=NORMALIZER_VERSION if settings.ko_numeral_normalize else None,
        postprocess_version=postprocess_label(settings.llm_postprocess, settings.ko_numeral_normalize),
        postprocess_applied=postprocess_applied,
        transaction_type=transaction_type,
    )


def build_llm_input(utterance: str, settings: Settings) -> str:
    """Text actually sent to the LLM: sanitize, then (optionally) normalize Korean numerals."""
    cleaned = _sanitize_utterance(utterance)
    # getattr: eval tests pass lightweight settings objects without this field (flag off).
    if getattr(settings, "ko_numeral_normalize", False):
        cleaned = normalize_korean_numerals(cleaned).text
    return cleaned


def _sanitize_utterance(raw: str) -> str:
    """Remove lightweight markup and emoji before sending text externally."""
    text = raw.strip()
    # Replace with a space, not "", so "2~3박스" does not become the quantity 23.
    text = re.sub(r"[*_`#>~]", " ", text)
    text = re.sub(r"[\U0001F300-\U0001FAFF]", " ", text)
    return " ".join(text.split())
