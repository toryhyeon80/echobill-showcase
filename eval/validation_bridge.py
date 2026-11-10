"""Attach the shared BE validation result to a saved candidate, the same way /parse does (SCORING.md §5-1).

`eval/scoring.py` already scores "추가 확인 적합성" from `validation_response`; this module produces it
offline (no API). `validation.py` is BE-owned and only imported here.
"""
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.parse_pipeline import combined_transaction_type, rule_parse  # noqa: E402
from app.services.validation import check_input_length, validate_candidate  # noqa: E402


def validation_response_for(candidate: dict | None, utterance_text: str, settings: Any,
                            llm_transaction_type: str | None = None) -> dict:
    """Run the shared BE validator on one candidate, the same way /parse does; shape matches SCORING.md.

    A generation failure (no candidate) stays in the denominator with status None. The counterparty
    count comes from the production Rule function on the original utterance, as on the /parse LLM path;
    so does the spoken item count, and the larger item count is judged (#120), and the transaction type (a clear
    partial payment → UNSUPPORTED_TRANSACTION, PR #128), combined with the saved LLM transaction type (매입·반품 with a
    word for it, LLM_TRANSACTION_TYPE runs only) the same way /parse combines them.
    """
    if candidate is None:
        return {"status_code": None, "error_code": None, "problem_fields": [], "partial_candidate": None}
    rule = rule_parse(utterance_text, settings)
    failure = check_input_length(utterance_text, settings) or validate_candidate(
        candidate,
        counterparty_count=rule.counterparty_count,
        item_count=max(len(candidate.get("items") or []), rule.spoken_item_count),
        transaction_type=combined_transaction_type(rule.transaction_type, llm_transaction_type, utterance_text),
        settings=settings,
    )
    if failure is None:
        return {"status_code": 200, "error_code": None, "problem_fields": [], "partial_candidate": None}
    return {"status_code": 400, "error_code": failure.error_code, "problem_fields": list(failure.problem_fields),
            "partial_candidate": failure.partial_candidate}
