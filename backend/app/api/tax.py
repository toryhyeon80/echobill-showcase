"""Mock tax invoice routes (SPEC §5.4, CLAUDE.md §4-5) — issued = 모의 완료, never a real NTS filing."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import CurrentUserId
from app.models import LedgerEntry, Merchant, TaxInvoiceEvent
from app.schemas.errors import ErrorResponse
from app.schemas.tax import (
    STATE_LABELS,
    BusinessInfoRequest,
    BusinessInfoResponse,
    TaxInvoiceListResponse,
    TaxInvoiceResponse,
    TaxIssueRequest,
)
from app.services import business_info
from app.services.idempotency import error_json, run_idempotent
from app.services.tax_invoice import (
    TaxRejected,
    invoice_status,
    list_invoices,
    request_issue,
    save_business_info,
)

router = APIRouter()


def _rejected(r: TaxRejected) -> tuple[int, dict[str, Any]]:
    return r.status_code, ErrorResponse(
        error_code=r.error_code, message=r.message, problem_fields=list(r.problem_fields)
    ).model_dump(mode="json")


def _invoice(entry: LedgerEntry, event: TaxInvoiceEvent | None) -> TaxInvoiceResponse:
    state = event.nts_state if event is not None else "none"
    return TaxInvoiceResponse(
        entry_id=entry.entry_id,
        counterparty=entry.counterparty,
        total_amount=entry.total_amount,
        state=state,
        state_label=STATE_LABELS[state],
        fail_reason=event.fail_reason if event is not None else None,
        requested_at=event.created_at if event is not None else None,
        updated_at=event.updated_at if event is not None else None,
    )


def _business_view(merchant: Merchant | None) -> BusinessInfoResponse:
    if merchant is None or not merchant.business_number_enc:
        return BusinessInfoResponse(registered=False)
    return BusinessInfoResponse(
        registered=True,
        business_number_masked=business_info.mask(business_info.decrypt(merchant.business_number_enc)),
        business_name=merchant.business_name,
    )


# /business must be declared before /{entry_id}
@router.get("/business", response_model=BusinessInfoResponse)
def get_business_info(user_id: CurrentUserId, db: Annotated[Session, Depends(get_db)]) -> BusinessInfoResponse:
    """Whether the 1회 등록 모달 is still needed. Masked number only."""
    return _business_view(db.get(Merchant, user_id))


@router.post(
    "/business",
    response_model=BusinessInfoResponse,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def register_business_info(
    body: BusinessInfoRequest,
    user_id: CurrentUserId,
    db: Annotated[Session, Depends(get_db)],
    x_idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Register (or correct) the supplier business number — stored encrypted (SPEC §5.4)."""
    number = business_info.normalize(body.business_number)

    def handler() -> tuple[int, dict[str, Any]]:
        if not business_info.is_valid(number):
            return 400, ErrorResponse(
                error_code="INVALID_BUSINESS_NUMBER",
                message="사업자번호 10자리를 확인해 주세요.",
                problem_fields=["business_number"],
            ).model_dump(mode="json")
        merchant = save_business_info(db, user_id, number, body.business_name)
        return 200, _business_view(merchant).model_dump(mode="json")

    # keyed fingerprint of the normalized number: retries match, and the idempotency table holds no raw number
    payload = {"business_number": business_info.fingerprint(number), "business_name": body.business_name}
    return run_idempotent(db, user_id, x_idempotency_key, "tax:business", payload, handler)


@router.post(
    "/issue",
    response_model=TaxInvoiceResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def issue_invoice(
    body: TaxIssueRequest,
    user_id: CurrentUserId,
    db: Annotated[Session, Depends(get_db)],
    x_idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """
    Mock-issue one confirmed entry. 404 not found/not yours · 409 ENTRY_NOT_CONFIRMED ·
    400 BUSINESS_INFO_REQUIRED (→ 1회 등록 모달). An active invoice is returned, never duplicated.

    FE contract: reuse the same X-Idempotency-Key only for a network retransmit (it replays the
    first response). After registering the business number, send a new key. Poll state with
    GET /tax/{entry_id} — a replayed `requested` is the state at the time of the first call.
    """

    def handler() -> tuple[int, dict[str, Any]]:
        outcome = request_issue(db, user_id, body.entry_id)
        if isinstance(outcome, TaxRejected):
            return _rejected(outcome)
        return 200, _invoice(*outcome).model_dump(mode="json")

    return run_idempotent(db, user_id, x_idempotency_key, "tax:issue", body.model_dump(mode="json"), handler)


@router.get("", response_model=TaxInvoiceListResponse)
def get_invoices(user_id: CurrentUserId, db: Annotated[Session, Depends(get_db)]) -> TaxInvoiceListResponse:
    """Latest mock invoice per entry (Tab3 list)."""
    return TaxInvoiceListResponse(invoices=[_invoice(e, ev) for e, ev in list_invoices(db, user_id)])


@router.get("/{entry_id}", response_model=TaxInvoiceResponse, responses={404: {"model": ErrorResponse}})
def get_invoice(
    entry_id: str, user_id: CurrentUserId, db: Annotated[Session, Depends(get_db)]
) -> TaxInvoiceResponse | JSONResponse:
    """State after re-entry or a timeout; `none` if never requested."""
    outcome = invoice_status(db, user_id, entry_id)
    if isinstance(outcome, TaxRejected):
        return error_json(404, outcome.error_code, outcome.message)
    return _invoice(*outcome)
