"""Error response schemas — DL-03 `{error_code, message, problem_fields, partial_candidate}`."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class PartialItem(BaseModel):
    """Extracted item values as-is (no range checks — may hold the out-of-range value)."""

    name: str | None = None
    qty: Decimal | None = None
    unit: str | None = None
    unit_price: int | float | None = None  # as entered — 30000.5 is echoed back for the edit modal
    amount: int | float | None = None


class PartialCandidate(BaseModel):
    """DL-01 fields extracted so far. Not stored, no entry_id."""

    counterparty: str | None = None
    items: list[PartialItem] = []
    total_amount: int | float | None = None
    credit: bool | None = None


class ErrorResponse(BaseModel):
    """4xx/5xx body shared by all ledger APIs (DL-03)."""

    error_code: str
    message: str
    problem_fields: list[str] = []
    partial_candidate: PartialCandidate | None = None
    # DL-29: only on a /parse-audio 400 after STT succeeded — "이렇게 들렸어요" + prefill. null elsewhere.
    utterance_text: str | None = None
    # PM 10/26: only on NOT_A_SALE — the screen that handles this sentence (payment → 받을 돈 [입금 등록] ·
    # edit → 장부에서 고치기 · inquiry → 받을 돈 · moved_credit → 받을 돈 [외상 옮기기]). null elsewhere.
    redirect: Literal["payment", "edit", "inquiry", "moved_credit"] | None = None
