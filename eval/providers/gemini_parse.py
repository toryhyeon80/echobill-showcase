"""Gemini ledger extraction for DL-11 model comparison (measurement only, not production).

Same input as production (`build_llm_input`, normalization on), same system prompt file and the
same `LedgerExtraction` schema forced as structured output. Thinking off (budget 0), temperature 0,
one whole-call deadline (5 s, enforced here; the SDK HTTP timeout is Gemini's 10 s minimum) and no
retries — the same meaning as `parse_llm`. The SDK is imported inside
the call so eval tests run without it (CI). Errors carry a kind only: no utterance, no key.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any

from pydantic import ValidationError

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.parse_llm import (  # noqa: E402
    LedgerExtraction,
    LLMParseError,
    LLMParseResult,
    LLMTimeoutError,
    build_llm_input,
    load_system_prompt,
)


logger = logging.getLogger(__name__)

BACKEND_ENV_FILE = BACKEND_ROOT / ".env"
# Recorded in run summaries so the comparison shows how thinking was configured.
THINKING_SETTING = "thinking_budget=0"
# The SDK also sends its HTTP timeout as the server deadline, and Gemini rejects deadlines under 10 s
# (400 INVALID_ARGUMENT). The 5 s measurement deadline is enforced in the adapter instead.
SDK_HTTP_TIMEOUT_SECONDS = 10


class GeminiParseResult(LLMParseResult):
    """LLMParseResult plus thinking tokens (billed as output; 0/None expected with budget 0)."""

    thoughts_tokens: int | None = None


class GeminiRateLimitError(LLMParseError):
    """HTTP 429: recorded as a failure, never retried; the run stops so the caller can report."""


def _api_key() -> str | None:
    """GEMINI_API_KEY from the environment, else backend/.env. Kept out of backend Settings."""
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    from dotenv import dotenv_values

    return dotenv_values(BACKEND_ENV_FILE).get("GEMINI_API_KEY") if BACKEND_ENV_FILE.exists() else None


def _make_client(timeout_seconds: float = SDK_HTTP_TIMEOUT_SECONDS) -> Any:
    """Real SDK client, single attempt (no SDK retry). HTTP timeout = max(deadline, 10 s Gemini minimum)."""
    key = _api_key()
    if not key:
        raise LLMParseError("GEMINI_API_KEY is not configured")
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=key,
        http_options=types.HttpOptions(
            timeout=int(max(timeout_seconds, SDK_HTTP_TIMEOUT_SECONDS) * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def _generation_config(settings: Any, system_prompt: str) -> Any:
    """Temperature, token cap, thinking off and the production schema as response schema."""
    from google.genai import types

    return types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=settings.llm_temperature,
        max_output_tokens=settings.llm_max_completion_tokens,
        response_mime_type="application/json",
        response_schema=LedgerExtraction,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )


def parse_utterance_with_gemini(
    utterance: str,
    *,
    settings: Any,
    client: Any = None,
    config: Any = None,
) -> GeminiParseResult:
    """Extract a `LedgerExtraction` candidate with Gemini; record tokens, latency and model version.

    Raises LLMTimeoutError (over_deadline), GeminiRateLimitError (429) or LLMParseError (other).
    No post-processing here: raw output is stored and scored offline like the OpenAI conditions.
    """
    system_prompt = load_system_prompt(settings)
    cleaned = build_llm_input(utterance, settings)
    deadline = settings.llm_timeout_seconds
    api_client = client or _make_client(deadline)
    config = config if config is not None else _generation_config(settings, system_prompt)

    started = time.perf_counter()
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(
            api_client.models.generate_content,
            model=settings.llm_model,
            contents=cleaned,
            config=config,
        )
        response = future.result(timeout=deadline)
    except (FutureTimeoutError, TimeoutError) as exc:
        raise LLMTimeoutError(f"over_deadline ({deadline:g}s)") from exc
    except Exception as exc:  # SDK error classes are only importable when the SDK is installed.
        status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        logger.warning("Gemini parse failed: %s status=%s", type(exc).__name__, status)
        if status == 429:
            raise GeminiRateLimitError("rate_limited status=429") from exc
        raise LLMParseError(f"api_error {type(exc).__name__} status={status}") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    text = getattr(response, "text", None)
    if not text:
        raise LLMParseError("empty_response")
    try:
        candidate = LedgerExtraction.model_validate_json(text)
    except ValidationError as exc:
        raise LLMParseError(f"schema_invalid errors={exc.error_count()}") from exc

    usage = getattr(response, "usage_metadata", None)
    candidates_tokens = getattr(usage, "candidates_token_count", None)
    thoughts_tokens = getattr(usage, "thoughts_token_count", None)
    return GeminiParseResult(
        candidate=candidate,
        model=settings.llm_model,
        processing_time_ms=elapsed_ms,
        prompt_tokens=getattr(usage, "prompt_token_count", None),
        # Billed output = answer + thinking tokens, so cost estimates stay comparable.
        completion_tokens=None if candidates_tokens is None else candidates_tokens + (thoughts_tokens or 0),
        response_model=getattr(response, "model_version", None),
        thoughts_tokens=thoughts_tokens,
    )
