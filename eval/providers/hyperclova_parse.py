"""HyperCLOVA X ledger extraction for model comparison (measurement only, not production — PM 10/22).

Same input as production (`build_llm_input`, normalization on) and the same system prompt file. CLOVA Studio
Chat Completions v3 with Structured Outputs (HCX-007 is the model that supports it), reasoning off, one
whole-call deadline (the measurement setting, enforced here) and no retries — the same meaning as `parse_llm`
and the Gemini adapter. Errors carry a kind only: no utterance, no key.

Structured Outputs documents no "null" type, but the API accepts `anyOf [X, null]`. Sending the nullable fields
as optional made HCX leave total_amount out in 37 of 42 normal Dev sentences (PM 10/22), so the production schema
is sent as is (required list and nulls kept, only title/description removed). A field the model still leaves out is
read as null before validating against `LedgerExtraction` ("모르면 비워 둠" keeps its meaning).
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
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

API_BASE = "https://clovastudio.stream.ntruss.com/v3/chat-completions/"
# Recorded in run summaries next to Gemini's thinking setting.
THINKING_SETTING = "thinking.effort=none"


class HyperClovaRateLimitError(LLMParseError):
    """HTTP 429: recorded as a failure, never retried; the run stops so the caller can report."""


def _api_key() -> str | None:
    """CLOVASTUDIO_API_KEY from the environment, else backend/.env. Kept out of backend Settings."""
    return env_key("CLOVASTUDIO_API_KEY")


def _strip_nulls(node: Any) -> Any:
    """Production schema → Structured Outputs schema: `anyOf [X, null]` becomes X and leaves `required`."""
    if isinstance(node, list):
        return [_strip_nulls(item) for item in node]
    if not isinstance(node, dict):
        return node
    options = node.get("anyOf")
    if isinstance(options, list):
        kept = [option for option in options if option.get("type") != "null"]
        if len(kept) == 1:
            merged = {**{k: v for k, v in node.items() if k != "anyOf"}, **kept[0]}
            return _strip_nulls(merged)
    out = {k: _strip_nulls(v) for k, v in node.items() if k not in ("title", "description")}
    properties = node.get("properties")
    if isinstance(properties, dict) and isinstance(node.get("required"), list):
        nullable = {name for name, prop in properties.items()
                    if isinstance(prop, dict) and any(o.get("type") == "null" for o in prop.get("anyOf", []))}
        out["required"] = [name for name in node["required"] if name not in nullable]
    return out


def _drop_meta(node: Any) -> Any:
    """Remove title/description only. Required fields and `anyOf [X, null]` stay as in the production schema."""
    if isinstance(node, list):
        return [_drop_meta(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {k: _drop_meta(v) for k, v in node.items() if k not in ("title", "description")}


def response_schema() -> dict[str, Any]:
    """`LedgerExtraction` as an inline (no $ref) Structured Outputs schema."""
    return _drop_meta(inline_refs(LedgerExtraction.model_json_schema()))


def _with_nulls(data: Any) -> Any:
    """Missing optional fields → null, so `LedgerExtraction` validates the same way as other models."""
    if not isinstance(data, dict):
        return data
    data = {"counterparty": None, "total_amount": None, "credit": None, "items": [], **data}
    if isinstance(data.get("items"), list):
        blank = {"name": None, "qty": None, "unit": None, "unit_price": None, "amount": None}
        data["items"] = [{**blank, **item} if isinstance(item, dict) else item for item in data["items"]]
    return data


def parse_utterance_with_hyperclova(
    utterance: str,
    *,
    settings: Any,
    client: httpx.Client | None = None,
) -> LLMParseResult:
    """Extract a `LedgerExtraction` candidate with HyperCLOVA X; record tokens, latency and the model.

    Raises LLMTimeoutError (over_deadline), HyperClovaRateLimitError (429) or LLMParseError (other).
    No post-processing here: raw output is stored and scored offline like the other conditions.
    """
    key = _api_key()
    if not key:
        raise LLMParseError("CLOVASTUDIO_API_KEY is not configured")
    deadline = settings.llm_timeout_seconds
    body = {
        "messages": [
            {"role": "system", "content": load_system_prompt(settings)},
            {"role": "user", "content": build_llm_input(utterance, settings)},
        ],
        "temperature": settings.llm_temperature,
        "maxCompletionTokens": settings.llm_max_completion_tokens,
        "thinking": {"effort": "none"},
        "responseFormat": {"type": "json", "schema": response_schema()},
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "X-NCP-CLOVASTUDIO-REQUEST-ID": uuid.uuid4().hex,
        "Content-Type": "application/json",
    }
    http = client or httpx.Client(timeout=deadline)
    started = time.perf_counter()
    try:
        response = http.post(API_BASE + settings.llm_model, json=body, headers=headers, timeout=deadline)
    except httpx.TimeoutException as exc:
        raise LLMTimeoutError(f"over_deadline ({deadline:g}s)") from exc
    except httpx.HTTPError as exc:
        logger.warning("HyperCLOVA X parse failed: %s", type(exc).__name__)
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
        raise HyperClovaRateLimitError("rate_limited status=429")
    if response.status_code != 200:
        logger.warning("HyperCLOVA X parse failed: status=%s", response.status_code)
        raise LLMParseError(f"api_error status={response.status_code}")
    try:
        payload = response.json()
        result = payload["result"]
        text = result["message"]["content"]
    except (ValueError, KeyError, TypeError) as exc:
        raise LLMParseError("unexpected_response") from exc
    if not text:
        raise LLMParseError("empty_response")
    try:
        candidate = LedgerExtraction.model_validate(_with_nulls(json.loads(text)))
    except (ValueError, ValidationError) as exc:
        raise LLMParseError("schema_invalid") from exc

    usage = result.get("usage") or {}
    return LLMParseResult(
        candidate=candidate,
        model=settings.llm_model,
        processing_time_ms=elapsed_ms,
        prompt_tokens=usage.get("promptTokens"),
        completion_tokens=usage.get("completionTokens"),
        response_model=settings.llm_model,
    )
