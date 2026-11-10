"""사용성 테스트 사장님 계정 실제 삭제 (PM 10/21 — 동의서 "테스트 끝난 날 실제 삭제").

The one physical-delete path, an exception to CS Soft Delete (CLAUDE.md §4-7) for owners who agreed to delete on the
day their test ends. Run by the BE owner through scripts/user_purge.py with PM approval; never an API or UI.

- Every row of the user goes in one transaction: merchants · ledger_entries (soft-deleted ones too) · payment_records ·
  tax_invoice_events · feedback_events · idempotency_records, and audit_logs that hold the user's ledger snapshots
  (거래처·품목) or name the user. All or nothing.
- One audit_log row stays: who ran it, who approved, why, and counts per table. The user is kept only as an HMAC of
  the user ID keyed with the server secret, so "was this account deleted?" can be answered without storing the Kakao
  ID — a plain SHA-256 of a numeric Kakao ID could be found by hashing every number (PM #238 리뷰).
- The demo account is refused (its rows are reset by scripts/seed_demo.py).
- Out of reach here: DL-32 export files outside the repo (scripts/feedback_purge.py) and Railway logs (masked, no PII).
- The facilitator doesn't know the Kakao ID, only the signup minute in the observation notes (PM 11/3): purge_by_signup
  finds the account by terms_agreed_at and refuses unless exactly one matches, so the ID never has to be read or typed.
"""

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    AuditLog,
    FeedbackEvent,
    IdempotencyRecord,
    LedgerEntry,
    Merchant,
    PaymentRecord,
    TaxInvoiceEvent,
)
from app.services.cs_soft_delete import _check_request
from app.services.demo_seed import DEMO_TAG
from app.services.fifo import KST

USER_TABLES = (Merchant, LedgerEntry, PaymentRecord, TaxInvoiceEvent, FeedbackEvent, IdempotencyRecord)


@dataclass
class PurgeResult:
    """Outcome printed by the script. `code` is OK, DRY_RUN or a refusal reason; counts per table, no PII."""

    ok: bool
    code: str
    message: str
    counts: dict[str, int] = field(default_factory=dict)
    audit_id: int | None = None


def user_hash(user_id: str, settings: Settings | None = None) -> str:
    """What the audit row keeps instead of the user ID: HMAC-SHA256 keyed with BUSINESS_NUMBER_KEY (fixed in production —
    changing it means re-encrypting every business number, so a later check hashes the same way). Prefixed so the
    value means nothing outside this audit row."""
    key = (settings or get_settings()).business_number_key.get_secret_value().encode()
    return hmac.new(key, b"user_purge:" + user_id.encode(), hashlib.sha256).hexdigest()


def _audit_filter(user_id: str, entry_ids: list[str]):
    """Audit rows naming this user: ledger snapshots of their entries, demo/user-level rows."""
    return or_(
        (AuditLog.entity_type == "ledger_entry") & AuditLog.entity_id.in_(entry_ids),
        (AuditLog.entity_type == "user") & (AuditLog.entity_id == user_id),
    )


def _count(db: Session, model, condition) -> int:
    return db.scalar(select(func.count()).select_from(model).where(condition)) or 0


def purge_user(db: Session, *, user_id: str, reason: str, actor: str, approved_by: str,
               dry_run: bool) -> PurgeResult:
    """Delete every row of `user_id` (dry run: count only) and leave one audit_log row with counts."""
    user_id = user_id.strip()
    refused = _check_request(reason, actor, approved_by)
    if refused is not None:
        return PurgeResult(ok=False, code=refused.code, message=refused.message)
    if not user_id:
        return PurgeResult(ok=False, code="USER_REQUIRED", message="사용자 ID를 입력하세요.")
    if db.scalar(select(LedgerEntry.id).where(LedgerEntry.user_id == user_id,
                                              LedgerEntry.idempotency_key.like(f"{DEMO_TAG}:%")).limit(1)):
        return PurgeResult(ok=False, code="DEMO_ACCOUNT", message="데모 계정은 지우지 않습니다 (seed_demo로 초기화).")

    entry_ids = list(db.scalars(select(LedgerEntry.entry_id).where(LedgerEntry.user_id == user_id)))
    counts = {model.__tablename__: _count(db, model, model.user_id == user_id) for model in USER_TABLES}
    counts[AuditLog.__tablename__] = _count(db, AuditLog, _audit_filter(user_id, entry_ids))
    if not any(counts.values()):
        return PurgeResult(ok=False, code="NOT_FOUND", message="이 사용자의 데이터가 없습니다.", counts=counts)
    if dry_run:
        return PurgeResult(ok=True, code="DRY_RUN", message="지울 건수만 셌어요 (변경 없음). --execute로 실행하세요.",
                           counts=counts)

    try:
        db.execute(delete(AuditLog).where(_audit_filter(user_id, entry_ids)))
        for model in USER_TABLES:
            db.execute(delete(model).where(model.user_id == user_id))
        audit = AuditLog(actor=actor.strip(), action="user_purge", entity_type="user_hash",
                         entity_id=user_hash(user_id), before_data={"counts": counts},
                         after_data={"reason": reason.strip(), "approved_by": approved_by.strip()})
        db.add(audit)
        db.flush()
        # 실행 후 확인: 같은 트랜잭션 안에서 남은 행이 0인지 보고 아니면 전부 되돌린다
        if any(_count(db, model, model.user_id == user_id) for model in USER_TABLES):
            raise RuntimeError("post-check failed")
        db.commit()
    except Exception:
        db.rollback()
        raise
    return PurgeResult(ok=True, code="OK", message="실제 삭제했어요.", counts=counts, audit_id=audit.id)


def signup_range(text: str) -> tuple[datetime, datetime]:
    """KST signup time → UTC [start, end). "YYYY-MM-DD HH:MM" allows ±1 minute (the facilitator's watch vs the server
    clock); "YYYY-MM-DD HH:MM:SS" is that one second, to pick one of several matches. ValueError if malformed."""
    text = text.strip()
    if text.count(":") == 2:
        at = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        start, end = at, at + timedelta(seconds=1)
    else:
        at = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        start, end = at - timedelta(minutes=1), at + timedelta(minutes=2)
    return start.astimezone(UTC), end.astimezone(UTC)


def _kst_text(value: datetime) -> str:
    """Stored signup time as KST text (SQLite returns it naive, in UTC)."""
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


def purge_by_signup(db: Session, *, signup_at: tuple[datetime, datetime], reason: str, actor: str, approved_by: str,
                    dry_run: bool) -> PurgeResult:
    """purge_user for the one account whose terms_agreed_at falls in `signup_at`. None or several: nothing is deleted;
    several are listed by exact time and ledger count, without IDs."""
    start, end = signup_at
    matches = db.execute(select(Merchant.user_id, Merchant.terms_agreed_at)
                         .where(Merchant.terms_agreed_at >= start, Merchant.terms_agreed_at < end)
                         .order_by(Merchant.terms_agreed_at)).all()
    if not matches:
        return PurgeResult(ok=False, code="SIGNUP_NOT_FOUND", message="그 시각(앞뒤 1분)에 가입한 계정이 없습니다.")
    if len(matches) > 1:
        found = ", ".join(f"{_kst_text(at)} (장부 {_count(db, LedgerEntry, LedgerEntry.user_id == user_id)}건)"
                          for user_id, at in matches)
        return PurgeResult(ok=False, code="SIGNUP_AMBIGUOUS",
                           message=f"같은 시각대에 {len(matches)}개 계정: {found}. 초까지 적어 다시 실행하세요.")
    user_id, at = matches[0]
    result = purge_user(db, user_id=user_id, reason=reason, actor=actor, approved_by=approved_by, dry_run=dry_run)
    result.message = f"가입 시각 {_kst_text(at)} KST · {result.message}"
    return result
