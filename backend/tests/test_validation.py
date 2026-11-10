"""validation.py — Golden answers must yield their expected DL-03 code and problem_fields."""

import json
from pathlib import Path

import pytest

from app.services.validation import check_input_length, validate_candidate

GOLDEN_DEV = Path(__file__).resolve().parents[2] / "data" / "golden" / "golden_dev.jsonl"

with GOLDEN_DEV.open(encoding="utf-8") as f:
    GOLDEN = [json.loads(line) for line in f if line.strip()]

# G4 answers carry items=[] / counterparty=null, so the parser reports these counts
_SIGNALS = {
    "MULTIPLE_COUNTERPARTIES_DETECTED": {"counterparty_count": 2},
    "EXCEED_MAX_ITEMS": {"item_count": 4},
}


def _item(**kw):
    return {"name": "배추", "qty": 1, "unit": None, "unit_price": None, "amount": None} | kw


def _cand(items, total, credit=True):
    return {"counterparty": "김씨네", "items": items, "total_amount": total, "credit": credit}


@pytest.mark.parametrize("rec", GOLDEN, ids=[r["id"] for r in GOLDEN])
def test_golden_expected_outcome(rec: dict) -> None:
    """Normal answers pass; 추가 확인 answers give the Golden code + clarification paths."""
    code = rec.get("expected_error_code")
    failure = validate_candidate(rec["expected_json"], **_SIGNALS.get(code, {}))
    if code is None:
        assert failure is None, f"{rec['id']}: unexpected {failure}"
        return
    assert failure is not None, f"{rec['id']}: expected {code}"
    assert failure.error_code == code
    assert failure.problem_fields == rec["required_clarifications"]


def test_priority_missing_before_mismatch() -> None:
    """Null values cannot be checked arithmetically → MISSING wins (DL-03 priority)."""
    raw = _cand([_item(qty=2, unit_price=15000)], 20000, credit=None)
    assert validate_candidate(raw).error_code == "MISSING_REQUIRED_FIELD"


def test_item_line_mismatch_lists_all_three() -> None:
    """qty × unit_price ≠ amount → all three item fields (server can't tell which is wrong)."""
    raw = _cand([_item(qty=3, unit_price=1000, amount=2500)], 2500)
    failure = validate_candidate(raw)
    assert failure.error_code == "AMOUNT_MISMATCH"
    assert failure.problem_fields == ["items[0].qty", "items[0].unit_price", "items[0].amount"]


def test_sum_mismatch_lists_item_amounts_and_total() -> None:
    """Σ item amounts ≠ total_amount."""
    raw = _cand([_item(amount=1000), _item(name="무", amount=2000)], 4000)
    failure = validate_candidate(raw)
    assert failure.problem_fields == ["items[0].amount", "items[1].amount", "total_amount"]


def test_half_won_rounds_up() -> None:
    """1.5 × 3원 = 4.5원 → 5원 (ROUND_HALF_UP, D04-4 — not banker's 4)."""
    raw = _cand([_item(qty=1.5, unit_price=3, amount=5)], 5)
    assert validate_candidate(raw) is None


def test_two_and_half_won_rounds_to_three() -> None:
    """2.5원 → 3원. Python round(2.5) gives 2 (banker's), so it must not be used."""
    raw = _cand([_item(qty="0.5", unit_price=5, amount=3)], 3)
    assert validate_candidate(raw) is None


def test_decimal_not_float() -> None:
    """0.29 × 50 = 14.5 → 15원. float gives 14.4999… → 14, so qty goes through Decimal(str())."""
    assert validate_candidate(_cand([_item(qty=0.29, unit_price=50, amount=15)], 15)) is None
    assert validate_candidate(_cand([_item(qty=0.29, unit_price=50, amount=14)], 14)).error_code == (
        "AMOUNT_MISMATCH"
    )


def test_null_unit_price_is_allowed() -> None:
    """unit_price null just skips the multiplication check (SPEC §5.1)."""
    assert validate_candidate(_cand([_item(qty=2, amount=5000)], 5000)) is None


def test_out_of_range_keeps_partial() -> None:
    """Over 1억 → AMOUNT_OUT_OF_RANGE with every out-of-range path and the extracted values."""
    raw = _cand([_item(amount=200_000_000)], 200_000_000)
    failure = validate_candidate(raw)
    assert failure.error_code == "AMOUNT_OUT_OF_RANGE"
    assert failure.problem_fields == ["items[0].amount", "total_amount"]
    assert failure.partial_candidate["total_amount"] == 200_000_000


def test_qty_range() -> None:
    """Qty must be > 0 and ≤ 100,000."""
    raw = _cand([_item(qty=100_001, amount=5000)], 5000)
    assert validate_candidate(raw).problem_fields == ["items[0].qty"]


def test_input_length_limit() -> None:
    """500 chars ok, 501 → INPUT_TOO_LONG."""
    assert check_input_length("가" * 500) is None
    assert check_input_length("가" * 501).error_code == "INPUT_TOO_LONG"
