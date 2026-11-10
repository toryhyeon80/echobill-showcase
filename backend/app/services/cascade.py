"""Second opinion (cascade, 팀 회의 10/23): on a doubt signal, ask a second model and ask the owner again only
where the two answers differ.

The stored values stay the operating model's (GPT-4o-mini). The second model only decides which fields to ask:
on its own it filled values it should have asked and confirmed credit wrongly (Seed v2 010·061, 수경 10/22).
Signals and the comparison are 수경's measurement code (count_signals.py · cascade_ask.py signature; signals fixed
by PM 10/21): Seed v2 140 sentences, wrong values saved 8 → 4, all 4 new questions real errors, second call 16%.
A failed or slow second call changes nothing — the operating answer goes on as before (PM 10/23).
"""

from __future__ import annotations

import itertools
import logging
import time
import traceback
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.services.parse_llm import LedgerExtraction, build_llm_input, load_system_prompt
from app.services.postprocess import (
    _numbers,
    postprocess_active,
    postprocess_candidate,
    unit_price_rules_active,
)

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Labels stored in postprocess_applied for each field emptied, so 400 logs, repair_of and ops metrics count them
# like a post-process rule (same as #172).
LABELS = {
    "counterparty": "cascade_counterparty",
    "item_name": "cascade_item_name",
    "item_qty": "cascade_item_qty",
    "amount": "cascade_total_amount",
    "credit": "cascade_credit",
}

_NULLABLE = {"nullable": True}
# LedgerExtraction as a Gemini response schema (OpenAPI subset), every key required like the OpenAI strict schema.
_ITEM_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "name": {"type": "STRING", **_NULLABLE},
        "qty": {"type": "NUMBER", **_NULLABLE},
        "unit": {"type": "STRING", **_NULLABLE},
        "unit_price": {"type": "INTEGER", **_NULLABLE},
        "amount": {"type": "INTEGER", **_NULLABLE},
    },
    "required": ["name", "qty", "unit", "unit_price", "amount"],
    "propertyOrdering": ["name", "qty", "unit", "unit_price", "amount"],
}
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "counterparty": {"type": "STRING", **_NULLABLE},
        "items": {"type": "ARRAY", "items": _ITEM_SCHEMA},
        "total_amount": {"type": "INTEGER", **_NULLABLE},
        "credit": {"type": "BOOLEAN", **_NULLABLE},
    },
    "required": ["counterparty", "items", "total_amount", "credit"],
    "propertyOrdering": ["counterparty", "items", "total_amount", "credit"],
}


class CascadeError(RuntimeError):
    """The second model gave no usable answer (not configured, timeout, HTTP or schema failure)."""


# ---------------- signals (count_signals.py) ----------------

def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _derived(numbers: set[Decimal]) -> set[Decimal]:
    """Products and sums of two numbers said (the same number twice included)."""
    out: set[Decimal] = set()
    for a, b in itertools.combinations_with_replacement(sorted(numbers), 2):
        out.add(a * b)
        out.add(a + b)
    return out


def sentence_numbers(sent_text: str, original: str) -> set[Decimal]:
    """Numbers in the text sent to the model and in the original utterance."""
    return _numbers(sent_text) | _numbers(original)


def number_grounding_signal(candidate: dict[str, Any], numbers: set[Decimal]) -> bool:
    """A qty, item amount or total that is no number said and no product/sum of two (qty×unit price counts)."""
    derived = _derived(numbers)
    items = candidate.get("items") or []
    for item in items:
        qty, price, amount = _dec(item.get("qty")), _dec(item.get("unit_price")), _dec(item.get("amount"))
        if qty is not None and qty not in numbers and qty not in derived:
            return True
        if amount is not None and not (amount in numbers or amount in derived
                                       or (qty is not None and price is not None and qty * price == amount)):
            return True
    total = _dec(candidate.get("total_amount"))
    if total is not None and total not in numbers and total not in derived:
        has_all = bool(items) and all(i.get("amount") is not None for i in items)
        item_sum = sum((_dec(i.get("amount")) or Decimal(0) for i in items), Decimal(0))
        if not (has_all and item_sum == total):  # the item amounts adding up to the total is a basis
            return True
    return False


def total_mismatch_signal(candidate: dict[str, Any]) -> bool:
    """qty×unit price ≠ item amount, or (two items or more) the item amounts ≠ the total, by 1 won or more."""
    items = candidate.get("items") or []
    for item in items:
        qty, price, amount = _dec(item.get("qty")), _dec(item.get("unit_price")), _dec(item.get("amount"))
        if qty is not None and price is not None and amount is not None and abs(qty * price - amount) >= 1:
            return True
    total = _dec(candidate.get("total_amount"))
    return (len(items) > 1 and total is not None and all(i.get("amount") is not None for i in items)
            and abs(sum(_dec(i["amount"]) or Decimal(0) for i in items) - total) >= 1)


def signals(candidate: dict[str, Any], numbers: set[Decimal], applied: list[str]) -> list[str]:
    """Names of the doubt signals that fired (three, PM 10/21). "목록 밖 이름" is left out (회의 10/23)."""
    fired = []
    if number_grounding_signal(candidate, numbers):
        fired.append("number_grounding")
    if "total_restored_spoken" in applied:
        fired.append("total_restored")
    if total_mismatch_signal(candidate):
        fired.append("total_mismatch")
    return fired


# ---------------- comparison (cascade_ask.py signature) ----------------

def _no_spaces(value: Any) -> str:
    return "".join(str(value or "").split())


def _qty_key(value: Any) -> Decimal | None:
    qty = _dec(value)
    return None if qty is None else qty.normalize()


def _names(candidate: dict[str, Any]) -> list[str]:
    return sorted(_no_spaces(i.get("name")) for i in candidate.get("items") or [])


def _name_qty(candidate: dict[str, Any]) -> list[tuple[str, str]]:  # majority vote key for eval/run_modes.py (PR #205, not on main yet)
    return sorted((_no_spaces(i.get("name")), str(_qty_key(i.get("qty")))) for i in candidate.get("items") or [])


def _amounts(candidate: dict[str, Any]) -> tuple[str, list[str]]:
    items = candidate.get("items") or []
    return str(_dec(candidate.get("total_amount"))), sorted(str(_dec(i.get("amount"))) for i in items)


def _qty_by_name(candidate: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for item in candidate.get("items") or []:
        out.setdefault(_no_spaces(item.get("name")), []).append(str(_qty_key(item.get("qty"))))
    return {name: sorted(qtys) for name, qtys in out.items()}


def _qty_differs_on(primary: dict[str, Any], second: dict[str, Any]) -> set[str]:
    """Item names both answers have whose quantities differ — compared even when other names differ (#213)."""
    p, s = _qty_by_name(primary), _qty_by_name(second)
    # nameless items compare with each other as one group: 2 vs 4 must be asked, not kept (병철 #232). Emptying names
    # after quantities (clear_differing) already keeps a named item's qty out of this (병철 #226)
    return {name for name in p.keys() & s.keys() if p[name] != s[name]}


def differing_parts(primary: dict[str, Any], second: dict[str, Any]) -> list[str]:
    """Signature parts (거래처 · 품목 이름 · 품목별 수량 · 금액 · 외상) where the two answers differ; spacing ignored.
    Unit and unit price are not compared. Quantities are compared on the names both answers share, so a different
    name elsewhere does not hide a different quantity (병철 #213)."""
    parts = []
    if _no_spaces(primary.get("counterparty")) != _no_spaces(second.get("counterparty")):
        parts.append("counterparty")
    if _names(primary) != _names(second):
        parts.append("item_name")
    if _qty_differs_on(primary, second):
        parts.append("item_qty")
    if _amounts(primary) != _amounts(second):
        parts.append("amount")
    if primary.get("credit") != second.get("credit"):
        parts.append("credit")
    return parts


def clear_differing(primary: dict[str, Any], second: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Empty the operating answer's fields where the two answers differ, so validation asks them (400).

    Never copies a value from `second`. 거래처·외상 → that field; 금액 → the total and the item amounts; 품목별 수량 →
    the qty of each item whose name has different quantities; 품목 이름 → the names the second answer lacks (all
    names when none is missing on this side), which opens the full edit form.
    """
    parts = differing_parts(primary, second)
    if not parts:
        return primary, []
    originals = primary.get("items") or []
    fields = {**primary, "items": [dict(i) for i in originals]}
    cleared: set[str] = set()  # parts where a value the operating answer had was actually emptied
    if "counterparty" in parts:
        if primary.get("counterparty"):
            cleared.add("counterparty")
        fields["counterparty"] = None
    if "credit" in parts:
        if primary.get("credit") is not None:
            cleared.add("credit")
        fields["credit"] = None
    if "amount" in parts:
        if primary.get("total_amount") is not None or any(i.get("amount") is not None for i in originals):
            cleared.add("amount")
        fields["total_amount"] = None
        for item in fields["items"]:
            item["amount"] = None
    # Quantities first, matched by the operating answer's own names — emptying names first hid them (병철 #226)
    if "item_qty" in parts:
        differ = _qty_differs_on(primary, second)
        for item, original in zip(fields["items"], originals, strict=True):
            if _no_spaces(original.get("name")) in differ:
                if original.get("qty") not in (None, ""):
                    cleared.add("item_qty")
                item["qty"] = None
    if "item_name" in parts:
        other = Counter(_names(second))
        missing = [i for i in fields["items"] if other[_no_spaces(i.get("name"))] == 0]
        for item in missing or fields["items"]:
            if item.get("name") not in (None, ""):
                cleared.add("item_name")
            item["name"] = None
    # Label only what was emptied: a field the operating answer left empty would have been asked anyway (#213·#226)
    return fields, [LABELS[p] for p in parts if p in cleared]


# ---------------- second model ----------------

def _call_gemini(body: dict[str, Any], settings: Settings, client: httpx.Client) -> dict[str, Any]:
    key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""
    response = client.post(
        GEMINI_URL.format(model=settings.cascade_model), json=body,
        headers={"x-goog-api-key": key},  # header, not ?key= — the key stays out of URLs and access logs
        timeout=settings.cascade_timeout_seconds,
    )
    if response.status_code != 200:
        raise CascadeError(f"api_error status={response.status_code}")
    return response.json()


def second_opinion(utterance: str, settings: Settings, client: httpx.Client | None = None) -> dict[str, Any]:
    """The second model's candidate, after the same post-processing as the operating answer.

    Same input text, system prompt and schema as the operating call; thinking off, temperature 0. One whole-call
    deadline (CASCADE_TIMEOUT_SECONDS) — httpx timeouts apply per network phase. Raises CascadeError.
    """
    if not settings.gemini_api_key or not settings.gemini_api_key.get_secret_value():
        raise CascadeError("GEMINI_API_KEY is not configured")
    try:
        system_prompt = load_system_prompt(settings)
    except RuntimeError as exc:
        raise CascadeError("system prompt is not readable") from exc
    cleaned = build_llm_input(utterance, settings)
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": cleaned}]}],
        "generationConfig": {
            "temperature": settings.llm_temperature,
            "maxOutputTokens": settings.llm_max_completion_tokens,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    http = client or httpx.Client()
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        payload = executor.submit(_call_gemini, body, settings, http).result(timeout=settings.cascade_timeout_seconds)
    except TimeoutError as exc:
        raise CascadeError(f"over_deadline ({settings.cascade_timeout_seconds:g}s)") from exc
    except httpx.HTTPError as exc:
        raise CascadeError(f"api_error {type(exc).__name__}") from exc
    except ValueError as exc:  # body is not JSON
        raise CascadeError("unexpected_response") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        if client is None:
            http.close()
    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        candidate = LedgerExtraction.model_validate_json(text).model_dump()
    except (KeyError, IndexError, TypeError, ValidationError) as exc:
        raise CascadeError("unexpected_response") from exc
    if postprocess_active(settings.llm_postprocess, settings.ko_numeral_normalize):
        candidate, _ = postprocess_candidate(
            candidate, cleaned,
            unit_price_rules=unit_price_rules_active(settings.llm_postprocess, settings.ko_numeral_normalize),
        )
    return candidate


def apply(
    utterance: str,
    fields: dict[str, Any],
    applied: list[str],
    settings: Settings,
    *,
    ask: Callable[[str, Settings], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Cascade step after the operating model: (fields, labels). Off, no signal or any failure → as is.

    The cascade only adds questions, so no error in it may fail /parse (수경 #197 리뷰): an expected failure of the
    second call is a warning, anything else an error log with the place it came from, and the operating answer
    goes on as without the cascade. Logs carry no utterance, value or key.
    """
    ask = ask or second_opinion
    if not settings.cascade_enabled:
        return fields, []
    started = time.perf_counter()
    fired: list[str] = []
    try:
        fired = signals(fields, sentence_numbers(build_llm_input(utterance, settings), utterance), applied)
        if not fired:
            return fields, []
        cleared, labels = clear_differing(fields, ask(utterance, settings))
    except CascadeError as exc:
        logger.warning("cascade second opinion failed: %s signals=%s", exc, ",".join(fired))
        return fields, []
    except Exception as exc:  # noqa: BLE001 — optional step; the bug is logged with its place, /parse goes on
        where = traceback.extract_tb(exc.__traceback__)[-1]
        logger.error("cascade failed unexpectedly: %s at %s:%d signals=%s", type(exc).__name__,
                     where.filename.rsplit("/", 1)[-1], where.lineno, ",".join(fired))
        return fields, []
    # WARNING like postprocess_400 / voice_answer: the app has no log handler and only WARNING+ reaches Railway (#213),
    # so the call rate and added time can be counted in production
    logger.warning("cascade signals=%s asked=%s ms=%d", ",".join(fired), ",".join(labels) or "-",
                int((time.perf_counter() - started) * 1000))
    return cleared, labels
