"""Popbill-shaped Mock issuer — no network, no real NTS transmission (CLAUDE.md §4-5).

Kept behind one function so a real Popbill client can replace it later (기획서 §7.3).
Referenced Popbill fields: 공급자 사업자번호·상호, 공급받는자 상호, 합계금액, 과세 구분.
Omitted (D09): 공급받는자 사업자번호, 품목 상세, 공급가액·세액 분리, 승인번호.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import Settings

FAIL_REASON = "모의 실패: 시연용 실패 모드입니다. 실제 국세청 전송과 무관합니다."


@dataclass(frozen=True)
class MockInvoice:
    """What the Mock receives — taxable e-invoice only (면세·거래명세서 제외)."""

    supplier_business_number: str
    supplier_name: str | None
    buyer_name: str
    total_amount: int
    taxation: str = "과세"


@dataclass(frozen=True)
class MockResult:
    state: str  # requested | issued | failed
    fail_reason: str | None = None


def submit(invoice: MockInvoice, settings: Settings) -> MockResult:
    """First answer for a new request. `delay` leaves it requested until `poll` finishes it."""
    if settings.tax_mock_mode == "fail":
        return MockResult("failed", FAIL_REASON)
    if settings.tax_mock_mode == "delay":
        return MockResult("requested")
    return MockResult("issued")


def poll(requested_at: datetime, settings: Settings) -> MockResult:
    """Status re-check for a `requested` invoice (delay mode completes after N seconds)."""
    ready = requested_at + timedelta(seconds=settings.tax_mock_delay_seconds)
    if datetime.now(timezone.utc) >= ready:
        return MockResult("issued")
    return MockResult("requested")
