"""Claude (Haiku) ledger extraction for model comparison (measurement only, not production — PM 10/22).

Same input as production (`build_llm_input`, normalization on) and the same system prompt file. Messages API
with structured outputs (`output_config.format` json_schema), thinking disabled, one whole-call deadline (the
measurement setting, enforced here) and no retries — the same meaning as `parse_llm` and the other adapters.
Errors carry a kind only: no utterance, no key.

Haiku 5.5 rejects a non-default temperature (400), so temperature is not sent: the model default is used and
the run records it next to the thinking setting. The production schema keeps its nulls (the API supports
`anyOf [X, null]`); every object gets `additionalProperties: false` as the API requires.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import httpx
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
from eval.providers.http_common import env_key, inline_refs  # noqa: E402

logger = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
# Recorded in run summaries next to the other providers' thinking setting.
THINKING_SETTING = "thinking=disabled, temperature not sent (model default)"


class ClaudeRateLimitError(LLMParseError):
    """HTTP 429: recorded as a failure, never retried; the run stops so the caller can report."""


def _api_key() -> str | None:
    """ANTHROPIC_API_KEY from the environment, else backend/.env. Kept out of backend Settings."""
    return env_key("ANTHROPIC_API_KEY")


def _close_objects(node: Any) -> Any:
    """Add `additionalProperties: false` to every object and drop title/description (not needed by the API)."""
    if isinstance(node, list):
        return [_close_objects(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {k: _close_objects(v) for k, v in node.items() if k not in ("title", "description")}
    if out.get("type") == "object":
        out["additionalProperties"] = False
    return out


def response_schema() -> dict[str, Any]:
    """`LedgerExtraction` as an inline structured-outputs schema."""
    return _close_objects(inline_refs(LedgerExtraction.model_json_schema()))


def parse_utterance_with_claude(
    utterance: str,
    *,
    settings: Any,
    client: httpx.Client | None = None,
) -> LLMParseResult:
    """Extract a `LedgerExtraction` candidate with Claude; record tokens, latency and the served model.

    Raises LLMTimeoutError (over_deadline), ClaudeRateLimitError (429) or LLMParseError (other).
    No post-processing here: raw output is stored and scored offline like the other conditions.
    """
    key = _api_key()
    if not key:
        raise LLMParseError("ANTHROPIC_API_KEY is not configured")
    deadline = settings.llm_timeout_seconds
    body = {
        "model": settings.llm_model,
        "max_tokens": settings.llm_max_completion_tokens,
        "system": load_system_prompt(settings),
        "messages": [{"role": "user", "content": build_llm_input(utterance, settings)}],
        "thinking": {"type": "disabled"},
        "output_config": {"format": {"type": "json_schema", "schema": response_schema()}},
    }
    headers = {"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"}
    http = client or httpx.Client(timeout=deadline)
    started = time.perf_counter()
    try:
        response = http.post(API_URL, json=body, headers=headers, timeout=deadline)
    except httpx.TimeoutException as exc:
        raise LLMTimeoutError(f"over_deadline ({deadline:g}s)") from exc
    except httpx.HTTPError as exc:
        logger.warning("Claude parse failed: %s", type(exc).__name__)
        raise LLMParseError(f"api_error {type(exc).__name__}") from exc
    finally:
        if client is None:
            http.close()
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if elapsed_ms > deadline * 1000:
        # httpx timeouts apply per phase (connect, read, …), so a slower answer can still arrive. Same whole-call
        # deadline as the Gemini adapter: a 6 s answer is a timeout for every model (#188 리뷰).
        raise LLMTimeoutError(f"over_deadline ({deadline:g}s)")

    if response.status_code == 429:
        raise ClaudeRateLimitError("rate_limited status=429")
    if response.status_code != 200:
        logger.warning("Claude parse failed: status=%s", response.status_code)
        raise LLMParseError(f"api_error status={response.status_code}")
    try:
        payload = response.json()
        stop_reason = payload.get("stop_reason")
    except (ValueError, AttributeError) as exc:
        raise LLMParseError("unexpected_response") from exc
    if stop_reason in ("refusal", "max_tokens"):
        # Checked before reading text: a refusal may carry no text block (#188 리뷰). The output may not follow
        # the schema in these cases (API docs) — a failure, not a candidate.
        raise LLMParseError(f"stop_reason={stop_reason}")
    try:
        text = next(block["text"] for block in payload["content"] if block.get("type") == "text")
    except (KeyError, TypeError, AttributeError, StopIteration) as exc:
        raise LLMParseError("unexpected_response") from exc
    if not text:
        raise LLMParseError("empty_response")
    try:
        candidate = LedgerExtraction.model_validate(json.loads(text))
    except (ValueError, ValidationError) as exc:
        raise LLMParseError("schema_invalid") from exc

    usage = payload.get("usage") or {}
    return LLMParseResult(
        candidate=candidate,
        model=settings.llm_model,
        processing_time_ms=elapsed_ms,
        prompt_tokens=usage.get("input_tokens"),
        completion_tokens=usage.get("output_tokens"),
        response_model=payload.get("model") or settings.llm_model,
    )
