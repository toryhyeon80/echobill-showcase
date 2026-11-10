"""CS Soft Delete / restore — the only deletion path (DL-06, 기획서 CS 정책, CLAUDE.md §4-7).

Run by the BE owner through scripts/cs_soft_delete.py after identity check + PM approval.
Never a physical delete, never an API, never an Admin UI. Every executed change writes audit_log.

Lock (발행 완료 여부와 무관하게 연결 이력으로 차단):
- any TaxInvoiceEvent for the entry → refused
- FIFO currently allocates any payment to it → refused (deleting would move money between entries)
- restore that would pull payment allocation onto it → refused (same reason)

Bulk delete (soft_delete_user_entries, 사용자 장부 전체 — 팀 테스트 장부·중복 입력 정리용):
- 대상: 그 사용자의 삭제되지 않은 모든 거래 (candidate·confirmed·rejected). 이미 삭제된 건은 건너뜀.
- 건마다 soft_delete()를 그대로 호출 — 같은 Lock·감사 기록·잠금 순서, 거래 하나 = 트랜잭션 하나.
- FIFO 규칙: 각 건은 지우기 직전의 실제 상태로 Lock을 판정하고, 배분액 0인 건만 지운다.
  배분액 0인 건을 빼면 그보다 앞 건은 FIFO가 먼저 소진하므로 그대로, 뒤 건은 이 건이 쓰던 돈이
  0이었으므로 그대로다 → 남는 거래의 입금 배분은 한 원도 옮겨지지 않는다 (순서와 무관한 불변식).
  입금(PaymentRecord)은 지우지 않지만, 남는 거래의 배분 합계 = 기존 입금 합계라
  미수(credit − paid)가 음수가 되지 않는다 (초과 입금 상태를 만들지 않음).
- 처리 순서는 최신 → 과거 (FIFO 역순, 결정적). dry run은 건마다 롤백하므로 모든 건을
  삭제 전 상태로 판정하고, 위 불변식 때문에 실행 결과와 같다 (동시 입금이 끼면 실행 쪽 판정이 우선).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, LedgerEntry, TaxInvoiceEvent
from app.services.fifo import allocate, credit_entries, paid_total
from app.services.idempotency import as_utc

logger = logging.getLogger(__name__)

SNAPSHOT_FIELDS = ("entry_id", "status", "counterparty", "items", "total_amount", "credit", "tax_status")


@dataclass
class CSResult:
    """Outcome printed by the script. `code` is OK, DRY_RUN or a refusal reason."""

    ok: bool
    code: str
    message: str
    audit_id: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


def _snapshot(entry: LedgerEntry) -> dict[str, Any]:
    """Before/after data for audit_log — utterance text (원문) is deliberately left out."""
    data = {k: getattr(entry, k) for k in SNAPSHOT_FIELDS}
    data["deleted_at"] = entry.deleted_at.isoformat() if entry.deleted_at else None
    return data


def _refuse(code: str, message: str, **details: Any) -> CSResult:
    return CSResult(ok=False, code=code, message=message, details=details)


def _check_request(reason: str, actor: str, approved_by: str) -> CSResult | None:
    if not reason.strip():
        return _refuse("REASON_REQUIRED", "변경 사유를 입력하세요.")
    if not actor.strip() or not approved_by.strip():
        return _refuse("APPROVAL_REQUIRED", "실행자와 PM 승인자를 모두 입력하세요.")
    if actor.strip() == approved_by.strip():
        return _refuse("APPROVAL_REQUIRED", "실행자 본인이 승인자가 될 수 없습니다 (PM 승인 필요).")
    return None


def _lock_target(db: Session, user_id: str, entry_id: str) -> LedgerEntry | CSResult:
    """
    Lock in the same order as POST /payments to avoid deadlocks: the counterparty's credit rows
    first (oldest first), then the target row itself (already locked if it is one of them).
    """
    entry = db.scalar(select(LedgerEntry).where(LedgerEntry.entry_id == entry_id))
    if entry is None:
        return _refuse("NOT_FOUND", "해당 거래가 없습니다.")
    if entry.user_id != user_id:
        # 본인 확인한 사용자와 거래 소유자가 다르면 중단 — 다른 사업자 데이터는 건드리지 않는다
        return _refuse("OWNER_MISMATCH", "본인 확인한 사용자의 거래가 아닙니다.")
    if entry.counterparty:
        credit_entries(db, user_id, entry.counterparty, for_update=True)
    # re-read under lock so checks see the committed state (populate_existing refreshes the object)
    return db.scalar(
        select(LedgerEntry)
        .where(LedgerEntry.id == entry.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _tax_linked(db: Session, entry: LedgerEntry) -> bool:
    return db.scalar(
        select(TaxInvoiceEvent.id).where(TaxInvoiceEvent.ledger_entry_id == entry.id).limit(1)
    ) is not None


def _allocated_to(db: Session, entry: LedgerEntry, *, include: bool) -> int:
    """Payment amount FIFO assigns to `entry` (as if live when include=True)."""
    if not (entry.credit and entry.status == "confirmed" and entry.counterparty):
        return 0
    entries = credit_entries(db, entry.user_id, entry.counterparty)
    if include and entry not in entries:
        entries = sorted([*entries, entry], key=lambda e: (as_utc(e.created_at), e.id))
    paid = paid_total(db, entry.user_id, entry.counterparty)
    return next((a.allocated for a in allocate(entries, paid) if a.entry is entry), 0)


def _apply(
    db: Session,
    entry: LedgerEntry,
    *,
    action: str,
    new_deleted_at: datetime | None,
    reason: str,
    actor: str,
    approved_by: str,
    dry_run: bool,
) -> CSResult:
    """Write deleted_at + audit_log in one transaction, verify, commit (or roll back on dry run)."""
    before = _snapshot(entry)
    entry.deleted_at = new_deleted_at
    after = _snapshot(entry)
    if dry_run:
        db.rollback()
        return CSResult(ok=True, code="DRY_RUN", message=f"{action} 가능 (변경 없음). --execute로 실행하세요.",
                        details={"entry_id": entry.entry_id})

    audit = AuditLog(
        actor=actor.strip(),
        action=action,
        entity_type="ledger_entry",
        entity_id=entry.entry_id,
        before_data=before,
        after_data={**after, "reason": reason.strip(), "approved_by": approved_by.strip()},
    )
    db.add(audit)
    try:
        db.flush()
        # 실행 후 확인: 같은 트랜잭션 안에서 상태·감사 기록이 맞는지 보고 아니면 전부 되돌린다
        db.refresh(entry)
        if (entry.deleted_at is None) != (new_deleted_at is None) or audit.id is None:
            raise RuntimeError("post-check failed")
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("CS %s failed and was rolled back: entry=%s", action, entry.entry_id)
        raise
    logger.info("CS %s done: entry=%s audit=%s", action, entry.entry_id, audit.id)
    return CSResult(ok=True, code="OK", message=f"{action} 완료", audit_id=audit.id,
                    details={"entry_id": entry.entry_id})


def soft_delete(
    db: Session, *, user_id: str, entry_id: str, reason: str, actor: str, approved_by: str, dry_run: bool = True
) -> CSResult:
    """Soft-delete one entry unless a payment or mock invoice is linked."""
    refused = _check_request(reason, actor, approved_by)
    if refused:
        return refused
    entry = _lock_target(db, user_id, entry_id)
    if isinstance(entry, CSResult):
        db.rollback()
        return entry
    if entry.deleted_at is not None:
        db.rollback()
        return _refuse("ALREADY_DELETED", "이미 삭제된 거래입니다.")
    if _tax_linked(db, entry):
        db.rollback()
        return _refuse("LOCKED_TAX", "모의 발행 이력이 연결된 거래는 삭제할 수 없습니다.")
    allocated = _allocated_to(db, entry, include=False)
    if allocated > 0:
        db.rollback()
        return _refuse("LOCKED_PAYMENT", "입금이 연결된 거래는 삭제할 수 없습니다.", allocated=allocated)
    return _apply(db, entry, action="soft_delete", new_deleted_at=datetime.now(timezone.utc),
                  reason=reason, actor=actor, approved_by=approved_by, dry_run=dry_run)


def restore(
    db: Session, *, user_id: str, entry_id: str, reason: str, actor: str, approved_by: str, dry_run: bool = True
) -> CSResult:
    """Undo a soft delete unless restoring would move existing payment allocation onto it."""
    refused = _check_request(reason, actor, approved_by)
    if refused:
        return refused
    entry = _lock_target(db, user_id, entry_id)
    if isinstance(entry, CSResult):
        db.rollback()
        return entry
    if entry.deleted_at is None:
        db.rollback()
        return _refuse("NOT_DELETED", "삭제되지 않은 거래입니다.")
    if _tax_linked(db, entry):
        db.rollback()
        return _refuse("LOCKED_TAX", "모의 발행 이력이 연결된 거래는 복구할 수 없습니다.")
    allocated = _allocated_to(db, entry, include=True)
    if allocated > 0:
        db.rollback()
        return _refuse("LOCKED_PAYMENT", "복구하면 기존 입금 배분이 바뀌어 복구할 수 없습니다.", allocated=allocated)
    return _apply(db, entry, action="restore", new_deleted_at=None,
                  reason=reason, actor=actor, approved_by=approved_by, dry_run=dry_run)


# 묶음 실행 결과 분류 — 출력 순서이기도 하다
BULK_OUTCOMES = ("DELETED", "LOCKED_TAX", "LOCKED_PAYMENT", "ALREADY_DELETED", "ERROR")


def soft_delete_user_entries(
    db: Session, *, user_id: str, reason: str, actor: str, approved_by: str, dry_run: bool = True
) -> CSResult:
    """
    Soft-delete every live entry of one user, one transaction per entry (see module docstring).

    Returns counts + entry_ids per outcome in `details` (no counterparty, amount or utterance).
    Per-entry commits: a failure midway leaves every finished entry deleted *with* its audit row,
    and the rest untouched — the run stops there and the summary says exactly which ones ran.
    """
    refused = _check_request(reason, actor, approved_by)
    if refused:
        return refused
    rows = db.execute(
        select(LedgerEntry.entry_id, LedgerEntry.deleted_at)
        .where(LedgerEntry.user_id == user_id)
        .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())  # 최신 → 과거
    ).all()
    db.rollback()  # 목록 조회만 하고 잠금은 건마다 soft_delete()가 잡는다
    if not rows:
        return _refuse("NOT_FOUND", "해당 사용자의 거래가 없습니다.")

    outcomes: dict[str, list[str]] = {code: [] for code in BULK_OUTCOMES}
    for entry_id, deleted_at in rows:
        if deleted_at is not None:
            outcomes["ALREADY_DELETED"].append(entry_id)
            continue
        try:
            result = soft_delete(db, user_id=user_id, entry_id=entry_id, reason=reason,
                                 actor=actor, approved_by=approved_by, dry_run=dry_run)
        except Exception:
            # 이 건은 롤백된다. 남은 건은 손대지 않고 여기서 멈추며, 결과는 PARTIAL(종료 코드 1)로 알린다
            db.rollback()
            logger.exception("CS bulk soft_delete stopped at entry=%s", entry_id)
            outcomes["ERROR"].append(entry_id)
            break
        code = "DELETED" if result.code in ("OK", "DRY_RUN") else result.code
        # 목록 조회 뒤 다른 경로로 삭제됐으면 ALREADY_DELETED로 들어온다
        outcomes.setdefault(code, []).append(entry_id)

    details = {code: ids for code, ids in outcomes.items() if ids}
    if outcomes["ERROR"]:
        done = 0 if dry_run else len(outcomes["DELETED"])
        return CSResult(ok=False, code="PARTIAL",
                        message=f"오류로 중단 — {done}건 삭제 완료, 나머지는 변경 없음 (로그 확인).",
                        details=details)
    if dry_run:
        return CSResult(ok=True, code="DRY_RUN",
                        message="삭제 대상 확인 (변경 없음). --execute로 실행하세요.", details=details)
    return CSResult(ok=True, code="OK", message=f"soft_delete 완료 ({len(outcomes['DELETED'])}건)",
                    details=details)
