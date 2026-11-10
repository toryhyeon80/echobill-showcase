"""Tax invoice Mock schemas (SPEC §5.4). Every invoice response says it is a mock."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MOCK_NOTICE = "모의 발행입니다. 실제 국세청 전송이 아닙니다."
STATE_LABELS = {"none": "미발행", "requested": "요청됨", "issued": "모의 완료", "failed": "실패"}


class BusinessInfoRequest(BaseModel):
    """1회 등록 모달. Checksum is verified in code → 400 (not 422)."""

    business_number: str = Field(..., max_length=20)
    business_name: str | None = Field(default=None, max_length=200)


class BusinessInfoResponse(BaseModel):
    """Never contains the full number."""

    registered: bool
    business_number_masked: str | None = None
    business_name: str | None = None


class TaxIssueRequest(BaseModel):
    entry_id: str = Field(..., min_length=1, max_length=36)


class TaxInvoiceResponse(BaseModel):
    """Latest mock invoice state for one confirmed entry. `none` = never requested."""

    entry_id: str
    counterparty: str | None
    total_amount: int | None
    state: Literal["none", "requested", "issued", "failed"]
    state_label: str
    fail_reason: str | None = None
    is_mock: Literal[True] = True
    notice: str = MOCK_NOTICE
    requested_at: datetime | None = None
    updated_at: datetime | None = None


class TaxInvoiceListResponse(BaseModel):
    invoices: list[TaxInvoiceResponse]
