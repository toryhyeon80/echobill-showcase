"""Ledger parse schemas — candidate only; confirmed requires feedback API later.

Field names follow decision log DL-01 (API 명세): `utterance_text`, `qty`, `credit`,
`entry_id`, `parser_type`. Golden `expected_json` is validated against `CandidateFields`
(tests/test_contract_golden.py), so a renamed or extra key fails the test suite.
Value ranges beyond `gt=0` are pending D04 — do not add upper bounds here yet.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, field_validator, model_validator

MAX_ITEMS = 3  # 기획서 §5.1: 1회 입력 최대 3품목


class LedgerItem(BaseModel):
    """Single line item. qty/unit/unit_price/amount may be null — never invented (SPEC §5.1)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    qty: Decimal | None = Field(default=None, gt=0)
    unit: str | None = None
    unit_price: int | None = Field(default=None, gt=0)
    amount: int | None = Field(default=None, gt=0)


class CandidateFields(BaseModel):
    """Structured ledger fields shared by the API candidate and Golden `expected_json`."""

    model_config = ConfigDict(extra="forbid")

    counterparty: str | None = None
    items: list[LedgerItem] = Field(default_factory=list, max_length=MAX_ITEMS)
    total_amount: int | None = Field(default=None, gt=0)
    credit: bool | None = None  # true=외상, false=결제 완료, null=미기재(D04)


def _strip_or_none(value: object) -> object:
    """Sanitize before validation: trim whitespace, blank → None (PM #9)."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


class CandidateItemInput(BaseModel):
    """User-edited item. Ranges and required values are validation.py's job (400);
    only format is checked here — string caps match DB columns, money must be a JSON number
    (no strings/booleans → 422). A fractional amount is a user value → 400 from validation.py."""

    name: str | None = Field(default=None, max_length=200)
    qty: Decimal | None = None
    unit: str | None = Field(default=None, max_length=50)
    unit_price: StrictInt | StrictFloat | None = None
    amount: StrictInt | StrictFloat | None = None

    _strip = field_validator("name", "unit", mode="before")(_strip_or_none)


class CandidateInput(BaseModel):
    """User-edited candidate (repair submit · feedback correct). No item-count cap: 4+ → 400."""

    counterparty: str | None = Field(default=None, max_length=200)
    items: list[CandidateItemInput] = Field(default_factory=list, max_length=50)
    total_amount: StrictInt | StrictFloat | None = None
    credit: bool | None = None

    _strip = field_validator("counterparty", mode="before")(_strip_or_none)


class ParseRequest(BaseModel):
    """Utterance (or STT text) to structure into a ledger candidate."""

    # 길이 상한은 validation.check_input_length가 400 INPUT_TOO_LONG으로 처리 (DL-03 — 422 금지)
    utterance_text: str = Field(..., min_length=1)
    # DL-03 3-3 보완 제출: 있으면 AI 파싱을 건너뛰고 검증만 (parser_type=manual).
    # 느슨한 모델 — 품목 4개·범위 밖 값도 받아서 validation.py가 400으로 돌려준다
    candidate: CandidateInput | None = None
    # PM #23: X-Idempotency-Key of the parse request whose 400 this repair answers (with candidate only).
    # Links the post-process rules that caused the 400 to what the user chose. Not found → just not linked
    repair_of: str | None = Field(default=None, max_length=128)


class ParseCandidateResponse(CandidateFields):
    """HTTP 200 candidate. status is always candidate at this stage."""

    entry_id: str
    utterance_text: str
    status: Literal["candidate"] = "candidate"
    tax_status: Literal["none", "requested", "issued", "failed"] = "none"
    parser_type: Literal["llm", "rule", "manual", "mock"] = "mock"  # mock = scaffold only
    is_fallback_used: bool = False
    processing_time_ms: int


class LedgerEntryResponse(CandidateFields):
    """One stored entry owned by the caller — used to reload a candidate on re-entry (DL-22 5-2)."""

    entry_id: str
    status: Literal["candidate", "confirmed", "rejected"]
    utterance_text: str | None = None
    created_at: datetime


class FeedbackRequest(BaseModel):
    """
    User decision on a stored candidate.

    confirm = 그대로 확정 · correct = 수정한 값으로 확정 · reject = 거절.
    `corrected` is loose like the repair path so bad values come back as DL-03 400.
    """

    action: Literal["confirm", "correct", "reject"]
    corrected: CandidateInput | None = None

    @model_validator(mode="after")
    def _corrected_only_for_correct(self) -> "FeedbackRequest":
        if (self.action == "correct") != (self.corrected is not None):
            raise ValueError("corrected is required for correct and not allowed otherwise")
        return self
