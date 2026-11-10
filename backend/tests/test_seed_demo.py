"""Demo account seed (PM 10/19) — data adds up, refuses real accounts, resets only its own rows."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.main import app
from app.models import AuditLog, LedgerEntry, Merchant, PaymentRecord, TaxInvoiceEvent
from app.schemas.ledger import MAX_ITEMS
from app.services import ops_metrics
from app.services.demo_seed import DEMO_ENTRIES, DEMO_PAYMENTS, entry_total, reset, seed
from app.services.fifo import KST
from scripts import seed_demo as cli

DEMO = "demo-user"
NOW = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)  # 12:00 KST
client = TestClient(app)


def _signup(db: Session, user_id: str = DEMO) -> None:
    db.add(Merchant(user_id=user_id, kakao_provider_id=user_id))
    db.commit()


def _count(db: Session, model) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.user_id == DEMO))


def _headers(user_id: str = DEMO) -> dict[str, str]:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": user_id}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# --- the data itself ----------------------------------------------------------------------------

def test_every_entry_is_valid_shape() -> None:
    assert len(DEMO_ENTRIES) == 17
    for e in DEMO_ENTRIES:
        assert 1 <= len(e.items) <= MAX_ITEMS
        assert e.counterparty and e.counterparty in e.utterance
        assert all(qty > 0 and price > 0 for _, qty, _, price in e.items)
    assert sum(not e.credit for e in DEMO_ENTRIES) == 3


def test_payments_never_exceed_what_is_owed() -> None:
    for pay in DEMO_PAYMENTS:
        owed = sum(entry_total(e) for e in DEMO_ENTRIES
                   if e.credit and e.counterparty == pay.counterparty and e.days_ago > pay.days_ago)
        assert pay.amount <= owed


# --- seed -------------------------------------------------------------------------------------

def test_unknown_user_is_refused(db: Session) -> None:
    result = seed(db, "typo-id", now=NOW, dry_run=False)
    assert (result.ok, result.code) == (False, "UNKNOWN_USER")
    assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 0


def test_account_with_any_record_is_refused(db: Session) -> None:
    _signup(db)
    db.add(PaymentRecord(user_id=DEMO, counterparty="실제가게", amount=1000))
    db.commit()
    result = seed(db, DEMO, now=NOW, dry_run=False)
    assert (result.ok, result.code) == (False, "NOT_EMPTY")
    assert _count(db, LedgerEntry) == 0


def test_dry_run_changes_nothing(db: Session) -> None:
    _signup(db)
    result = seed(db, DEMO, now=NOW)
    assert (result.ok, result.code, result.entries) == (True, "DRY_RUN", 17)
    assert _count(db, LedgerEntry) == 0


def test_seed_fills_the_demo_screens(db: Session) -> None:
    _signup(db)
    assert seed(db, DEMO, now=NOW, dry_run=False).code == "SEEDED"
    assert (_count(db, LedgerEntry), _count(db, PaymentRecord), _count(db, TaxInvoiceEvent)) == (17, 3, 1)
    assert all(e.status == "confirmed" for e in db.scalars(select(LedgerEntry)))  # dashboard counts confirmed only

    body = client.get("/api/v1/dashboard/unpaid", headers=_headers()).json()
    owed = {r["counterparty"]: r["unpaid"] for r in body["counterparties"]}
    assert body["total_unpaid"] == 2_383_000
    assert owed == {"새봄반찬가게": 570_000, "홍성반점": 500_000, "한솥밥식당": 450_000, "동네국밥": 270_000,
                    "소담한정식": 240_000, "바다횟집": 185_000, "맛나분식": 168_000}

    oldest = min(e.created_at for e in db.scalars(select(LedgerEntry)))
    assert (NOW.astimezone(KST).date() - oldest.replace(tzinfo=oldest.tzinfo or UTC).astimezone(KST).date()).days == 32
    assert all(e.created_at.replace(tzinfo=e.created_at.tzinfo or UTC) <= NOW for e in db.scalars(select(LedgerEntry)))

    taxed = db.get(LedgerEntry, db.scalar(select(TaxInvoiceEvent.ledger_entry_id)))
    assert (taxed.counterparty, taxed.tax_status) == ("소담한정식", "issued")
    assert db.scalar(select(AuditLog.action).where(AuditLog.entity_id == DEMO)) == "demo_seed"


def test_demo_rows_stay_out_of_ops_metrics(db: Session) -> None:
    _signup(db)
    seed(db, DEMO, now=NOW, dry_run=False)
    day = NOW.astimezone(KST).date()
    assert ops_metrics.compute(db, "w", day - timedelta(days=40), day).candidates == 0


# --- reset ------------------------------------------------------------------------------------

def test_reset_is_a_soft_delete_then_seed_again(db: Session) -> None:
    _signup(db)
    seed(db, DEMO, now=NOW, dry_run=False)
    assert reset(db, DEMO, now=NOW).code == "DRY_RUN"
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.deleted_at.is_(None))) == 17
    result = reset(db, DEMO, now=NOW, dry_run=False)
    assert (result.code, result.entries, result.payments) == ("RESET", 17, 3)
    db.expire_all()
    # nothing removed (CLAUDE.md §4-7) — hidden from every screen instead
    assert (_count(db, LedgerEntry), _count(db, PaymentRecord), _count(db, TaxInvoiceEvent)) == (17, 3, 1)
    assert all(e.deleted_at is not None for e in db.scalars(select(LedgerEntry)))
    assert all(p.status == "canceled" for p in db.scalars(select(PaymentRecord)))
    h = _headers()
    assert client.get("/api/v1/dashboard/unpaid", headers=h).json()["total_unpaid"] == 0
    audit = db.scalars(select(AuditLog).where(AuditLog.action == "demo_reset")).one()
    # 병철 #169 리뷰: the audit row names every hidden row, not only the counts (§4-7)
    assert sorted(audit.after_data["entry_ids"]) == sorted(e.entry_id for e in db.scalars(select(LedgerEntry)))
    assert sorted(audit.after_data["payment_ids"]) == sorted(p.id for p in db.scalars(select(PaymentRecord)))

    later = NOW + timedelta(days=2)
    assert seed(db, DEMO, now=later, dry_run=False).code == "SEEDED"  # new run keys, no unique clash
    assert client.get("/api/v1/dashboard/unpaid", headers=h).json()["total_unpaid"] == 2_383_000


def test_reset_refuses_an_account_with_real_records(db: Session) -> None:
    _signup(db)
    seed(db, DEMO, now=NOW, dry_run=False)
    db.add(LedgerEntry(entry_id=str(uuid.uuid4()), user_id=DEMO, status="confirmed", counterparty="실제가게",
                       total_amount=1000, credit=True))
    db.commit()
    assert reset(db, DEMO, now=NOW, dry_run=False).code == "NOT_DEMO_ONLY"
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.deleted_at.is_(None))) == 18


def test_another_users_records_are_untouched(db: Session) -> None:
    _signup(db)
    _signup(db, "other")
    db.add(LedgerEntry(entry_id=str(uuid.uuid4()), user_id="other", status="confirmed", counterparty="남의가게",
                       total_amount=5000, credit=True))
    db.commit()
    seed(db, DEMO, now=NOW, dry_run=False)
    reset(db, DEMO, now=NOW, dry_run=False)
    other = db.scalar(select(LedgerEntry).where(LedgerEntry.user_id == "other"))
    assert other is not None and other.deleted_at is None


# --- CLI --------------------------------------------------------------------------------------

def test_cli_is_dry_run_without_execute(db: Session, session_factory) -> None:
    _signup(db)
    assert cli.run(["--user-id", DEMO], session_factory, now=NOW).code == "DRY_RUN"
    assert cli.run(["--user-id", DEMO, "--execute"], session_factory, now=NOW).code == "SEEDED"
    assert cli.run(["--user-id", DEMO, "--reset", "--execute"], session_factory).code == "RESET"
