"""Demo account data for the presentation and screenshots (PM 10/19) — script-only, never via the API.

Fictional shops and sentences written for the demo (no Golden/Seed sentences, DL-25). Dates are
"days before the run", so running it just before capture keeps 경과일 right (최장 32일).
Every row is tagged DEMO_TAG in idempotency_key so it can be reset (Soft Delete) and kept out of ops metrics.
Exception approved by PM (10/19): rows go in as `confirmed` directly — synthetic demo data for our own demo
account, never a real owner's candidate (CLAUDE.md §4-1 is about the product flow).

Result after FIFO: 7 counterparties owe 2,383,000원, oldest 32 days; 3 paid-on-the-spot trades;
3 deposits (partial · full · partial); one mock tax invoice (issued = 모의 완료, CLAUDE.md §4-5).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import AuditLog, LedgerEntry, Merchant, PaymentRecord, TaxInvoiceEvent
from app.schemas.ledger import LedgerItem
from app.services.fifo import KST

DEMO_TAG = "demo-seed"
ACTOR = "seed_demo"

# (name, qty, unit, unit_price)
Item = tuple[str, int, str, int]


@dataclass(frozen=True)
class DemoEntry:
    days_ago: int
    counterparty: str
    utterance: str
    items: tuple[Item, ...]
    credit: bool  # True = 외상, False = 그 자리에서 받음(현금·이체)


@dataclass(frozen=True)
class DemoPayment:
    days_ago: int
    counterparty: str
    amount: int


DEMO_ENTRIES: tuple[DemoEntry, ...] = (
    DemoEntry(32, "한솥밥식당", "한솥밥식당에 배추 열 망, 망당 이만오천 원 외상",
              (("배추", 10, "망", 25000),), True),
    DemoEntry(27, "동네국밥", "동네국밥 배추 여덟 망 망당 이만오천 원, 대파 서른 단 단당 사천 원 외상",
              (("배추", 8, "망", 25000), ("대파", 30, "단", 4000)), True),
    DemoEntry(21, "홍성반점", "홍성반점에 양파 열 망 이만 원씩, 대파 스무 단 사천 원씩, 배추 네 망 이만오천 원씩 외상",
              (("양파", 10, "망", 20000), ("대파", 20, "단", 4000), ("배추", 4, "망", 25000)), True),
    DemoEntry(16, "소담한정식", "소담한정식 고춧가루 열 근 근당 삼만 원, 깐마늘 십 킬로 만이천 원, 애호박 오십 개 이천 원 외상",
              (("고춧가루", 10, "근", 30000), ("깐마늘", 10, "킬로", 12000), ("애호박", 50, "개", 2000)), True),
    DemoEntry(14, "바다횟집", "바다횟집 무 사십 개 개당 이천오백 원, 깻잎 스무 단 삼천 원씩 외상",
              (("무", 40, "개", 2500), ("깻잎", 20, "단", 3000)), True),
    DemoEntry(12, "한솥밥식당", "한솥밥식당 양파 열 망 망당 이만 원 외상으로",
              (("양파", 10, "망", 20000),), True),
    DemoEntry(11, "맛나분식", "맛나분식 계란 스무 판 판당 사천팔백 원 외상",
              (("계란", 20, "판", 4800),), True),
    DemoEntry(10, "맛나분식", "맛나분식 떡볶이떡 십 킬로 킬로에 삼천 원 현금으로 받았어",
              (("떡볶이떡", 10, "킬로", 3000),), False),
    DemoEntry(9, "동네국밥", "동네국밥 콩나물 서른 봉지 삼천 원씩, 두부 스무 모 삼천 원씩 외상",
              (("콩나물", 30, "봉지", 3000), ("두부", 20, "모", 3000)), True),
    DemoEntry(8, "새봄반찬가게", "새봄반찬가게 시금치 서른 단 사천 원, 콩나물 마흔 봉지 삼천 원, 두부 마흔 모 삼천 원 외상",
              (("시금치", 30, "단", 4000), ("콩나물", 40, "봉지", 3000), ("두부", 40, "모", 3000)), True),
    DemoEntry(6, "맛나분식", "맛나분식 대파 열 단 사천 원씩, 양배추 여덟 통 사천 원씩 외상",
              (("대파", 10, "단", 4000), ("양배추", 8, "통", 4000)), True),
    DemoEntry(5, "바다횟집", "바다횟집 쪽파 스무 단 단당 삼천 원 계좌이체로 받았어",
              (("쪽파", 20, "단", 3000),), False),
    DemoEntry(4, "소담한정식", "소담한정식 깻잎 마흔 단 삼천 원씩, 표고버섯 십 킬로 만이천 원씩 외상",
              (("깻잎", 40, "단", 3000), ("표고버섯", 10, "킬로", 12000)), True),
    DemoEntry(3, "홍성반점", "홍성반점 양배추 스무 통 사천 원씩, 당근 팔 킬로 오천 원씩 외상",
              (("양배추", 20, "통", 4000), ("당근", 8, "킬로", 5000)), True),
    DemoEntry(2, "바다횟집", "바다횟집 상추 열 박스 박스당 팔천오백 원 외상",
              (("상추", 10, "박스", 8500),), True),
    DemoEntry(1, "새봄반찬가게", "새봄반찬가게 고춧가루 다섯 근 삼만 원씩, 깐마늘 다섯 킬로 만이천 원씩 외상",
              (("고춧가루", 5, "근", 30000), ("깐마늘", 5, "킬로", 12000)), True),
    DemoEntry(0, "골목카페", "골목카페 레몬 오 킬로 킬로에 구천 원 현금",
              (("레몬", 5, "킬로", 9000),), False),
)

DEMO_PAYMENTS: tuple[DemoPayment, ...] = (
    DemoPayment(15, "동네국밥", 200000),  # partial: 27일 전 320,000 중
    DemoPayment(7, "소담한정식", 520000),  # full: 16일 전 건 완납
    DemoPayment(1, "바다횟집", 60000),  # partial: 14일 전 160,000 중
)

TAX_ENTRY_INDEX = 3  # 소담한정식 16일 전 (완납) — 모의 발행 완료 1건


@dataclass
class DemoResult:
    ok: bool
    code: str
    message: str
    entries: int = 0
    payments: int = 0
    tax_events: int = 0


def entry_total(entry: DemoEntry) -> int:
    """Sum of qty × unit price — the stored total always matches its items."""
    return sum(qty * price for _, qty, _, price in entry.items)


def _at(now: datetime, days_ago: int, slot: int, hour: int) -> datetime:
    """A KST working-hours timestamp `days_ago` days before `now`; today's rows stay in the past."""
    if days_ago == 0:
        return now - timedelta(minutes=30 + slot)
    day = (now - timedelta(days=days_ago)).astimezone(KST)
    return day.replace(hour=hour, minute=(slot * 7) % 60, second=0, microsecond=0)


def _demo_key(user_id: str, run: str, kind: str, index: int) -> str:
    """Globally unique tag — payment/tax keys are unique across users, and reset keeps old rows (soft)."""
    return f"{DEMO_TAG}:{user_id}:{run}:{kind}{index:02d}"


def _is_demo(key: str | None) -> bool:
    return bool(key) and key.startswith(f"{DEMO_TAG}:")


def _refuse(code: str, message: str) -> DemoResult:
    return DemoResult(ok=False, code=code, message=message)


def seed(db: Session, user_id: str, *, now: datetime, dry_run: bool = True) -> DemoResult:
    """
    Fill a freshly signed-up demo account. Refuses an unknown user (typo guard) and any account
    that already has trades or deposits, so a real owner's ledger is never mixed with demo rows.
    """
    if db.get(Merchant, user_id) is None:
        return _refuse("UNKNOWN_USER", "가입한 계정이 아닙니다. 데모 계정으로 먼저 가입해 주세요.")
    if _live_entries(db, user_id) or _valid_payments(db, user_id):
        return _refuse("NOT_EMPTY", "이미 장부나 입금 기록이 있는 계정입니다. 빈 데모 계정에만 넣습니다 (다시 넣으려면 --reset).")
    result = DemoResult(ok=True, code="DRY_RUN" if dry_run else "SEEDED", message="",
                        entries=len(DEMO_ENTRIES), payments=len(DEMO_PAYMENTS), tax_events=1)
    if dry_run:
        result.message = "미리 보기 — 바뀐 것 없음 (--execute로 실행)"
        return result

    run = now.astimezone(KST).strftime("%Y%m%d%H%M%S")
    rows: list[LedgerEntry] = []
    for i, demo in enumerate(DEMO_ENTRIES):
        row = LedgerEntry(
            entry_id=str(uuid.uuid4()), user_id=user_id, status="confirmed", counterparty=demo.counterparty,
            items=[LedgerItem(name=n, qty=q, unit=u, unit_price=p, amount=q * p).model_dump(mode="json")
                   for n, q, u, p in demo.items],
            total_amount=entry_total(demo), credit=demo.credit, utterance_text=demo.utterance,
            parser_type="manual", idempotency_key=_demo_key(user_id, run, "e", i),
            created_at=_at(now, demo.days_ago, i, 9 + i % 8),
        )
        rows.append(row)
        db.add(row)
    for i, pay in enumerate(DEMO_PAYMENTS):
        db.add(PaymentRecord(user_id=user_id, counterparty=pay.counterparty, amount=pay.amount,
                             idempotency_key=_demo_key(user_id, run, "p", i), created_at=_at(now, pay.days_ago, i, 16)))
    db.flush()
    taxed = rows[TAX_ENTRY_INDEX]
    issued_at = taxed.created_at + timedelta(hours=1)
    db.add(TaxInvoiceEvent(user_id=user_id, ledger_entry_id=taxed.id, nts_state="issued",
                           idempotency_key=_demo_key(user_id, run, "t", 0), created_at=issued_at, updated_at=issued_at))
    taxed.tax_status = "issued"
    db.add(AuditLog(actor=ACTOR, action="demo_seed", entity_type="user", entity_id=user_id, before_data=None,
                    after_data={"entries": result.entries, "payments": result.payments, "tax_events": 1}))
    db.commit()
    result.message = "데모 데이터를 넣었습니다."
    return result


def _live_entries(db: Session, user_id: str) -> list[LedgerEntry]:
    return list(db.scalars(select(LedgerEntry).where(LedgerEntry.user_id == user_id, LedgerEntry.deleted_at.is_(None))))


def _valid_payments(db: Session, user_id: str) -> list[PaymentRecord]:
    return list(db.scalars(select(PaymentRecord).where(PaymentRecord.user_id == user_id,
                                                       PaymentRecord.status == "valid")))


def reset(db: Session, user_id: str, *, now: datetime, dry_run: bool = True) -> DemoResult:
    """
    Hide the demo rows so the demo can be re-run with fresh dates — Soft Delete, not removal
    (CLAUDE.md §4-7): trades get deleted_at, deposits become canceled, and the mock invoice drops out of
    the list with its trade. Refuses when the account has any live untagged trade or deposit — it is then
    not a pure demo account and the normal CS Soft Delete script (with its locks) applies.
    """
    entries = _live_entries(db, user_id)
    payments = _valid_payments(db, user_id)
    if any(not _is_demo(r.idempotency_key) for r in (*entries, *payments)):
        return _refuse("NOT_DEMO_ONLY", "데모 데이터가 아닌 기록이 있는 계정이라 지우지 않습니다.")
    result = DemoResult(ok=True, code="DRY_RUN" if dry_run else "RESET", message="",
                        entries=len(entries), payments=len(payments))
    if dry_run:
        result.message = "미리 보기 — 바뀐 것 없음 (--execute로 실행)"
        return result
    for entry in entries:
        entry.deleted_at = now
    for payment in payments:
        payment.status = "canceled"
    db.add(AuditLog(actor=ACTOR, action="demo_reset", entity_type="user", entity_id=user_id,
                    before_data={"entries": len(entries), "payments": len(payments)},
                    # 병철 #169 리뷰: which rows were hidden, like the CS script's per-row snapshot (§4-7)
                    after_data={"entries_deleted_at": now.isoformat(), "payments_status": "canceled",
                                "entry_ids": [e.entry_id for e in entries],
                                "payment_ids": [p.id for p in payments]}))
    db.commit()
    result.message = "데모 데이터를 숨겼습니다 (Soft Delete)."
    return result


def demo_filter():
    """SQL condition for "not a demo row" (ops metrics)."""
    return or_(LedgerEntry.idempotency_key.is_(None), LedgerEntry.idempotency_key.not_like(f"{DEMO_TAG}:%"))
