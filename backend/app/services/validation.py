"""Business validation for ledger candidates — the single module shared by API and `eval/`.

Rules: DL-03 (error codes · problem_fields · partial_candidate · priority),
DL-20 / D04 (ranges · 500 chars · ROUND_HALF_UP). Input is the parser's raw DL-01 dict,
checked *before* Pydantic so range/item-count errors become 400, not 422.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.config import Settings, get_settings
from app.schemas.ledger import MAX_ITEMS

ITEM_KEYS = ("name", "qty", "unit", "unit_price", "amount")

MESSAGES = {
    "INPUT_TOO_LONG": "입력이 너무 깁니다. 줄여서 다시 입력해 주세요.",
    "MULTIPLE_COUNTERPARTIES_DETECTED": "한 번에 거래처 한 곳만 입력해 주세요.",
    "EXCEED_MAX_ITEMS": "한 번에 품목은 3개까지 입력할 수 있습니다.",
    "MISSING_REQUIRED_FIELD": "빠진 정보를 채워 주세요.",
    "AMOUNT_OUT_OF_RANGE": "금액이나 수량이 허용 범위를 벗어났습니다.",
    "AMOUNT_MISMATCH": "금액 계산이 맞지 않습니다. 표시된 값을 확인해 주세요.",
    "UNSUPPORTED_TRANSACTION": "판매·외상·입금만 기록할 수 있어요. 판매 내용으로 다시 말씀해 주세요.",
}
# PM 10/26: a sentence that is not a sale goes to the screen that handles it (`redirect`) — 새 장부로 적지 않음
NOT_A_SALE_MESSAGES = {
    "payment": "입금은 받을 돈 탭에서 [입금 등록]으로 적어 주세요.",
    "edit": "이미 적은 장부는 장부 탭에서 그 기록을 눌러 고쳐 주세요.",
    "inquiry": "남은 외상은 받을 돈 탭에서 볼 수 있어요.",
    "moved_credit": "그동안 쌓인 외상은 받을 돈 탭의 [외상 옮기기]로 한 번에 넣어 주세요.",
}

# DL-30: purchases, returns/refunds/cancellations and mixed partial payments are not recorded
UNSUPPORTED_TRANSACTION_TYPES = frozenset({"purchase", "return", "partial_payment"})

# DL-03: these codes carry the extracted values back to the edit modal
_WITH_PARTIAL = {"MISSING_REQUIRED_FIELD", "AMOUNT_OUT_OF_RANGE", "AMOUNT_MISMATCH"}


@dataclass(frozen=True)
class ValidationFailure:
    """One DL-03 error (only the highest-priority one is returned)."""

    error_code: str
    problem_fields: list[str]
    partial_candidate: dict[str, Any] | None = None
    redirect: str | None = None  # NOT_A_SALE only: payment · edit · inquiry · moved_credit

    @property
    def message(self) -> str:
        """User-facing text (not scored, DL-03)."""
        return NOT_A_SALE_MESSAGES[self.redirect] if self.redirect else MESSAGES[self.error_code]


def _dec(value: Any) -> Decimal | None:
    """Number → Decimal (via str to avoid float noise); None stays None."""
    return None if value is None else Decimal(str(value))


def _won(value: Decimal) -> Decimal:
    """Round to whole won, 0.5 up (D04-4 — not banker's rounding)."""
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only DL-01 keys, missing keys → None."""
    return {
        "counterparty": raw.get("counterparty"),
        "items": [{k: item.get(k) for k in ITEM_KEYS} for item in raw.get("items") or []],
        "total_amount": raw.get("total_amount"),
        "credit": raw.get("credit"),
    }


def check_input_length(utterance_text: str, settings: Settings | None = None) -> ValidationFailure | None:
    """INPUT_TOO_LONG — run before parsing so an oversize input never reaches the LLM."""
    settings = settings or get_settings()
    if len(utterance_text) > settings.utterance_max_chars:
        return ValidationFailure("INPUT_TOO_LONG", ["utterance_text"])
    return None


def _missing_fields(c: dict[str, Any]) -> list[str]:
    """Required 5 (counterparty · item name · qty · total_amount · credit) that are null."""
    fields: list[str] = []
    if not c["counterparty"]:
        fields.append("counterparty")
    if not c["items"]:
        fields.append("items")
    for i, item in enumerate(c["items"]):
        if not item["name"]:
            fields.append(f"items[{i}].name")
        if item["qty"] is None:
            fields.append(f"items[{i}].qty")
    if c["total_amount"] is None:
        fields.append("total_amount")
    if c["credit"] is None:
        fields.append("credit")
    return fields


def _bad_money(value: Any, lo: int, hi: int) -> bool:
    """Outside DL-20 range, or not a whole won (30000.5 from a user-edited form)."""
    if value is None:
        return False
    if isinstance(value, float) and not value.is_integer():
        return True
    return not (lo <= value <= hi)


def _out_of_range_fields(c: dict[str, Any], settings: Settings) -> list[str]:
    """Present values outside DL-20 ranges (money 1~1억, qty 0 초과~100,000)."""
    lo, hi = settings.amount_min_krw, settings.amount_max_krw
    fields: list[str] = []
    for i, item in enumerate(c["items"]):
        qty = _dec(item["qty"])
        if qty is not None and not (0 < qty <= settings.qty_max):
            fields.append(f"items[{i}].qty")
        for key in ("unit_price", "amount"):
            if _bad_money(item[key], lo, hi):
                fields.append(f"items[{i}].{key}")
    if _bad_money(c["total_amount"], lo, hi):
        fields.append("total_amount")
    return fields


def _mismatch_fields(c: dict[str, Any]) -> list[str]:
    """Arithmetic contradictions; lists every conflicting value (DL-03 3-1)."""
    effective: list[Decimal | None] = []
    for i, item in enumerate(c["items"]):
        qty, price, amount = _dec(item["qty"]), _dec(item["unit_price"]), _dec(item["amount"])
        derived = _won(qty * price) if qty is not None and price is not None else None
        if derived is not None and amount is not None and derived != amount:
            return [f"items[{i}].qty", f"items[{i}].unit_price", f"items[{i}].amount"]
        effective.append(amount if amount is not None else derived)

    total = c["total_amount"]
    if total is None or not effective or any(v is None for v in effective):
        return []
    if sum(effective) == total:
        return []
    fields: list[str] = []
    for i, item in enumerate(c["items"]):
        if item["amount"] is not None:
            fields.append(f"items[{i}].amount")
        else:
            fields += [f"items[{i}].qty", f"items[{i}].unit_price"]
    return fields + ["total_amount"]


def validate_candidate(
    raw: dict[str, Any],
    *,
    counterparty_count: int = 1,
    item_count: int | None = None,
    transaction_type: str | None = None,
    settings: Settings | None = None,
) -> ValidationFailure | None:
    """
    Validate a parser result; return the highest-priority failure or None.

    Priority (DL-03, 2026-09-29): MULTIPLE_COUNTERPARTIES → EXCEED_MAX_ITEMS → MISSING
    → OUT_OF_RANGE → MISMATCH. The parser reports `counterparty_count` / `item_count`
    because G4 answers for these cases carry `items=[]` and a null counterparty.
    DL-30: a parser-judged unsupported `transaction_type` comes first (no candidate at all, so
    nothing to check) — i.e. right after INPUT_TOO_LONG, which runs before parsing. So does a sentence that is
    not a sale (PM 10/26: payment · edit · inquiry · moved_credit → NOT_A_SALE with `redirect`).
    Default None keeps eval/validation_bridge and stored results unchanged.
    """
    if transaction_type in UNSUPPORTED_TRANSACTION_TYPES:
        return ValidationFailure("UNSUPPORTED_TRANSACTION", [])
    if transaction_type in NOT_A_SALE_MESSAGES:
        return ValidationFailure("NOT_A_SALE", [], redirect=transaction_type)

    settings = settings or get_settings()
    c = _normalize(raw)
    n_items = len(c["items"]) if item_count is None else item_count

    if counterparty_count > 1:
        return ValidationFailure("MULTIPLE_COUNTERPARTIES_DETECTED", ["counterparty"])
    if n_items > MAX_ITEMS:
        return ValidationFailure("EXCEED_MAX_ITEMS", ["items"])

    for code, fields in (
        ("MISSING_REQUIRED_FIELD", _missing_fields(c)),
        ("AMOUNT_OUT_OF_RANGE", _out_of_range_fields(c, settings)),
        ("AMOUNT_MISMATCH", _mismatch_fields(c)),
    ):
        if fields:
            return ValidationFailure(code, fields, c if code in _WITH_PARTIAL else None)
    return None
