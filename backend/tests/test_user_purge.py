"""사용성 테스트 계정 실제 삭제 (PM 10/21 동의서) — every table, other users untouched, audit row, dry run, refusals."""

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    AuditLog,
    FeedbackEvent,
    IdempotencyRecord,
    LedgerEntry,
    Merchant,
    PaymentRecord,
    TaxInvoiceEvent,
)
from app.services import demo_seed
from app.services.cs_soft_delete import soft_delete
from app.services.user_purge import USER_TABLES, purge_by_signup, purge_user, signup_range, user_hash
from scripts.user_purge import main, run

APPROVAL = {"reason": "UT 11/3 종료 — 동의서 실제 삭제", "actor": "최병철", "approved_by": "최승현"}
ARGS = ["--reason", APPROVAL["reason"], "--actor", "최병철", "--approved-by", "최승현"]


def _owner(db: Session, user_id: str) -> LedgerEntry:
    """One of everything: merchant, credit entry (+ a soft-deleted one), payment, tax event, feedback, idempotency."""
    db.add(Merchant(user_id=user_id, kakao_provider_id=f"k-{user_id}", business_name="가나상회"))
    entry = LedgerEntry(entry_id=str(uuid.uuid4()), user_id=user_id, status="confirmed", counterparty="김씨",
                        items=[{"name": "배추", "qty": "1", "unit": "박스", "unit_price": None, "amount": 10000}],
                        total_amount=10000, credit=True, utterance_text="김씨 배추 원문")
    gone = LedgerEntry(entry_id=str(uuid.uuid4()), user_id=user_id, status="confirmed", counterparty="김씨",
                       items=[], total_amount=5000, credit=False)
    db.add_all([entry, gone])
    db.commit()
    db.add_all([
        PaymentRecord(user_id=user_id, counterparty="김씨", amount=3000),
        TaxInvoiceEvent(user_id=user_id, ledger_entry_id=entry.id, nts_state="issued"),
        FeedbackEvent(user_id=user_id, ledger_entry_id=entry.id, action="confirm"),
        IdempotencyRecord(user_id=user_id, key=str(uuid.uuid4()), request_hash="h", scope="parse"),
    ])
    db.commit()
    # a CS Soft Delete leaves an audit row with the entry's 거래처·품목 snapshot
    assert soft_delete(db, user_id=user_id, entry_id=gone.entry_id, dry_run=False, **APPROVAL).ok
    return entry


def _rows(db: Session, user_id: str) -> dict[str, int]:
    db.expire_all()
    return {m.__tablename__: db.scalar(select(func.count()).select_from(m).where(m.user_id == user_id))
            for m in USER_TABLES}


def test_execute_deletes_every_row_of_the_user_and_nothing_else(db: Session) -> None:
    _owner(db, "ut-1")
    _owner(db, "other")
    result = purge_user(db, user_id="ut-1", dry_run=False, **APPROVAL)
    assert result.code == "OK"
    assert result.counts == {"merchants": 1, "ledger_entries": 2, "payment_records": 1, "tax_invoice_events": 1,
                             "feedback_events": 1, "idempotency_records": 1, "audit_logs": 1}
    assert set(_rows(db, "ut-1").values()) == {0}
    assert set(_rows(db, "other").values()) == {1, 2}  # the other owner keeps everything


def test_audit_keeps_counts_and_a_hash_not_the_user_or_snapshots(db: Session) -> None:
    _owner(db, "ut-1")
    result = purge_user(db, user_id="ut-1", dry_run=False, **APPROVAL)
    audits = list(db.scalars(select(AuditLog)))
    assert [a.id for a in audits] == [result.audit_id]  # the soft-delete snapshot (김씨·배추) is gone too
    audit = audits[0]
    assert audit.action == "user_purge" and audit.entity_id == user_hash("ut-1") and audit.actor == "최병철"
    assert audit.after_data == {"reason": APPROVAL["reason"], "approved_by": "최승현"}
    assert "ut-1" not in str(audit.before_data) and "김씨" not in str(audit.before_data)


def test_audit_hash_needs_the_server_secret() -> None:
    """PM #238 리뷰: a plain SHA-256 of a numeric Kakao ID is found by hashing every number — keyed HMAC instead."""
    kakao_id = "4123456789"
    assert user_hash(kakao_id) != hashlib.sha256(kakao_id.encode()).hexdigest()
    assert user_hash(kakao_id) == user_hash(kakao_id)  # 다시 해시해서 확인
    other = Settings(_env_file=None, app_env="development", business_number_key=Fernet.generate_key().decode())
    assert user_hash(kakao_id, other) != user_hash(kakao_id)  # 비밀값 없이는 같은 값을 못 만듦


def test_dry_run_counts_only(db: Session) -> None:
    _owner(db, "ut-1")
    result = purge_user(db, user_id="ut-1", dry_run=True, **APPROVAL)
    assert result.code == "DRY_RUN" and result.counts["ledger_entries"] == 2
    assert _rows(db, "ut-1")["ledger_entries"] == 2


@pytest.mark.parametrize(("over", "code"), [
    ({"reason": " "}, "REASON_REQUIRED"),
    ({"approved_by": ""}, "APPROVAL_REQUIRED"),
    ({"approved_by": "최병철"}, "APPROVAL_REQUIRED"),  # 실행자 본인 승인 금지
])
def test_needs_reason_and_pm_approval(db: Session, over: dict, code: str) -> None:
    _owner(db, "ut-1")
    assert purge_user(db, user_id="ut-1", dry_run=False, **{**APPROVAL, **over}).code == code
    assert _rows(db, "ut-1")["merchants"] == 1


def test_unknown_user_is_refused(db: Session) -> None:
    assert purge_user(db, user_id="nobody", dry_run=False, **APPROVAL).code == "NOT_FOUND"


def test_demo_account_is_refused(db: Session) -> None:
    db.add(Merchant(user_id="demo-1"))
    db.commit()
    assert demo_seed.seed(db, "demo-1", now=datetime(2026, 10, 28, tzinfo=UTC), dry_run=False).ok
    assert purge_user(db, user_id="demo-1", dry_run=False, **APPROVAL).code == "DEMO_ACCOUNT"
    assert _rows(db, "demo-1")["ledger_entries"] > 0


def test_cli_dry_run_then_execute_without_pii_in_output(db: Session, monkeypatch, session_factory, capsys) -> None:
    _owner(db, "ut-1")
    assert run(["--user-id", "ut-1", *ARGS], session_factory).code == "DRY_RUN"
    assert _rows(db, "ut-1")["merchants"] == 1
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    assert main(["--user-id", "ut-1", *ARGS, "--execute"]) == 0
    out = capsys.readouterr().out
    assert "[OK]" in out and "ledger_entries: 2건" in out
    assert "ut-1" not in out and "김씨" not in out and "가나상회" not in out
    assert set(_rows(db, "ut-1").values()) == {0}


def _signed_up(db: Session, user_id: str, kst: str) -> None:
    """An owner who agreed to the terms at `kst` (a KST second), with their records."""
    _owner(db, user_id)
    db.get(Merchant, user_id).terms_agreed_at = signup_range(kst)[0]
    db.commit()


@pytest.mark.parametrize("noted", ["2026-11-03 14:04", "2026-11-03 14:05", "2026-11-03 14:06"])
def test_signup_minute_finds_the_account_within_one_minute(db: Session, noted: str) -> None:
    """The facilitator's watch and the server clock differ a little (stored in UTC, noted in KST)."""
    _signed_up(db, "ut-1", "2026-11-03 14:05:32")
    _signed_up(db, "other", "2026-11-03 15:05:32")
    result = purge_by_signup(db, signup_at=signup_range(noted), dry_run=False, **APPROVAL)
    assert result.code == "OK" and "2026-11-03 14:05:32" in result.message
    assert set(_rows(db, "ut-1").values()) == {0}
    assert _rows(db, "other")["merchants"] == 1


def test_signup_outside_the_window_or_never_agreed_is_not_found(db: Session) -> None:
    _signed_up(db, "ut-1", "2026-11-03 14:05:32")
    _owner(db, "no-consent")  # terms_agreed_at NULL
    result = purge_by_signup(db, signup_at=signup_range("2026-11-03 14:08"), dry_run=False, **APPROVAL)
    assert result.code == "SIGNUP_NOT_FOUND"
    assert _rows(db, "ut-1")["merchants"] == 1 and _rows(db, "no-consent")["merchants"] == 1


def test_two_signups_in_the_window_delete_nothing_until_seconds_pick_one(db: Session) -> None:
    _signed_up(db, "ut-1", "2026-11-03 14:05:32")
    _signed_up(db, "ut-2", "2026-11-03 14:05:50")
    refused = purge_by_signup(db, signup_at=signup_range("2026-11-03 14:05"), dry_run=False, **APPROVAL)
    assert refused.code == "SIGNUP_AMBIGUOUS" and "14:05:32" in refused.message and "14:05:50" in refused.message
    assert "ut-" not in refused.message
    assert _rows(db, "ut-1")["merchants"] == 1 and _rows(db, "ut-2")["merchants"] == 1
    assert purge_by_signup(db, signup_at=signup_range("2026-11-03 14:05:50"), dry_run=False, **APPROVAL).code == "OK"
    assert _rows(db, "ut-1")["merchants"] == 1 and _rows(db, "ut-2")["merchants"] == 0


def test_signup_dry_run_changes_nothing_and_approval_still_applies(db: Session) -> None:
    _signed_up(db, "ut-1", "2026-11-03 14:05:32")
    window = signup_range("2026-11-03 14:05")
    assert purge_by_signup(db, signup_at=window, dry_run=True, **APPROVAL).code == "DRY_RUN"
    bad = {**APPROVAL, "approved_by": "최병철"}
    assert purge_by_signup(db, signup_at=window, dry_run=False, **bad).code == "APPROVAL_REQUIRED"
    assert _rows(db, "ut-1")["merchants"] == 1


def test_cli_signup_at_without_id_in_output(db: Session, monkeypatch, session_factory, capsys) -> None:
    _signed_up(db, "ut-1", "2026-11-03 14:05:32")
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    assert main(["--signup-at", "2026-11-03 14:05", *ARGS]) == 0
    assert _rows(db, "ut-1")["merchants"] == 1
    assert main(["--signup-at", "2026-11-03 14:05", *ARGS, "--execute"]) == 0
    out = capsys.readouterr().out
    assert "[OK]" in out and "14:05:32" in out and "ut-1" not in out and "김씨" not in out
    assert _rows(db, "ut-1")["merchants"] == 0


@pytest.mark.parametrize("argv", [
    ["--signup-at", "11/3 14:05", *ARGS],  # 형식 오류
    ["--signup-at", "2026-11-03 14:05", "--user-id", "ut-1", *ARGS],  # 둘 다
    ARGS,  # 둘 다 없음
])
def test_cli_rejects_bad_target(argv: list[str], session_factory) -> None:
    with pytest.raises(SystemExit):
        run(argv, session_factory)
