"""CS Soft Delete script (DL-06, 기획서 CS 정책) — locks, approval, audit_log, dry run, restore."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import AuditLog, LedgerEntry, PaymentRecord, TaxInvoiceEvent
from app.services.cs_soft_delete import restore, soft_delete, soft_delete_user_entries
from scripts.cs_soft_delete import main, run

client = TestClient(app)

APPROVAL = {"reason": "CS #1 중복 입력", "actor": "최병철", "approved_by": "최승현"}


def _entry(db: Session, amount: int = 10000, days_ago: int = 1, *, credit: bool = True,
           status: str = "confirmed", user_id: str = "user-b", deleted: bool = False) -> LedgerEntry:
    entry = LedgerEntry(
        entry_id=str(uuid.uuid4()), user_id=user_id, status=status, counterparty="김씨",
        items=[{"name": "배추", "qty": "1", "unit": "박스", "unit_price": None, "amount": amount}],
        total_amount=amount, credit=credit, utterance_text="김씨 배추 원문",
        created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        deleted_at=datetime.now(timezone.utc) if deleted else None,
    )
    db.add(entry)
    db.commit()
    return entry


def _pay(db: Session, amount: int) -> None:
    db.add(PaymentRecord(user_id="user-b", counterparty="김씨", amount=amount))
    db.commit()


def _delete(db: Session, entry: LedgerEntry, *, dry_run: bool = False, **over):
    return soft_delete(db, user_id=over.pop("user_id", "user-b"), entry_id=entry.entry_id,
                       dry_run=dry_run, **{**APPROVAL, **over})


def _reload(db: Session, entry_id: str) -> LedgerEntry:
    db.expire_all()
    return db.query(LedgerEntry).filter_by(entry_id=entry_id).one()


def test_delete_sets_deleted_at_and_writes_audit(db: Session) -> None:
    entry = _entry(db)
    result = _delete(db, entry)
    assert result.ok and result.code == "OK"
    assert _reload(db, entry.entry_id).deleted_at is not None

    audit = db.query(AuditLog).one()
    assert (audit.actor, audit.action, audit.entity_type, audit.entity_id) == (
        "최병철", "soft_delete", "ledger_entry", entry.entry_id)
    assert audit.before_data["deleted_at"] is None
    assert audit.after_data["deleted_at"] is not None
    assert audit.after_data["reason"] == "CS #1 중복 입력"
    assert audit.after_data["approved_by"] == "최승현"
    assert "utterance_text" not in audit.before_data  # 원문은 감사 기록에 남기지 않는다
    assert audit.id == result.audit_id


def test_dry_run_changes_nothing(db: Session) -> None:
    entry = _entry(db)
    result = _delete(db, entry, dry_run=True)
    assert result.ok and result.code == "DRY_RUN"
    assert _reload(db, entry.entry_id).deleted_at is None
    assert db.query(AuditLog).count() == 0


def test_deleted_entry_disappears_from_api_and_totals(db: Session) -> None:
    entry = _entry(db)
    _delete(db, entry)
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "user-b"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    assert client.get(f"/api/v1/ledger/{entry.entry_id}", headers=h).status_code == 404
    assert client.get("/api/v1/dashboard/unpaid", headers=h).json()["total_unpaid"] == 0


def test_payment_linked_entry_is_locked(db: Session) -> None:
    """Partial payment counts as linked (FIFO allocates it to the oldest entry)."""
    entry = _entry(db, 10000)
    _pay(db, 1)
    result = _delete(db, entry)
    assert not result.ok and result.code == "LOCKED_PAYMENT"
    assert _reload(db, entry.entry_id).deleted_at is None
    assert db.query(AuditLog).count() == 0


def test_newer_unallocated_entry_can_be_deleted(db: Session) -> None:
    """Payment covers only the older entry → deleting the newer one moves no money."""
    _entry(db, 10000, days_ago=5)
    newer = _entry(db, 10000, days_ago=1)
    _pay(db, 10000)
    assert _delete(db, newer).code == "OK"


def test_tax_linked_entry_is_locked_even_if_failed(db: Session) -> None:
    """발행 완료 여부와 무관하게 이력이 있으면 차단."""
    entry = _entry(db)
    db.add(TaxInvoiceEvent(user_id="user-b", ledger_entry_id=entry.id, nts_state="failed"))
    db.commit()
    result = _delete(db, entry)
    assert result.code == "LOCKED_TAX"
    assert _reload(db, entry.entry_id).deleted_at is None


def test_owner_mismatch_is_refused(db: Session) -> None:
    entry = _entry(db, user_id="user-b")
    result = _delete(db, entry, user_id="user-a")
    assert result.code == "OWNER_MISMATCH"
    assert _reload(db, entry.entry_id).deleted_at is None


@pytest.mark.parametrize("over, code", [
    ({"reason": "  "}, "REASON_REQUIRED"),
    ({"approved_by": ""}, "APPROVAL_REQUIRED"),
    ({"approved_by": "최병철"}, "APPROVAL_REQUIRED"),  # self-approval
])
def test_reason_and_separate_pm_approval_required(db: Session, over: dict, code: str) -> None:
    entry = _entry(db)
    assert _delete(db, entry, **over).code == code
    assert _reload(db, entry.entry_id).deleted_at is None


def test_already_deleted_and_missing(db: Session) -> None:
    entry = _entry(db, deleted=True)
    assert _delete(db, entry).code == "ALREADY_DELETED"
    ghost = LedgerEntry(entry_id=str(uuid.uuid4()))
    assert _delete(db, ghost).code == "NOT_FOUND"


def test_candidate_and_cash_entries_can_be_deleted(db: Session) -> None:
    assert _delete(db, _entry(db, status="candidate")).code == "OK"
    assert _delete(db, _entry(db, credit=False)).code == "OK"


def test_restore_round_trip_with_audit(db: Session) -> None:
    entry = _entry(db)
    _delete(db, entry)
    result = restore(db, user_id="user-b", entry_id=entry.entry_id, dry_run=False, **APPROVAL)
    assert result.code == "OK"
    assert _reload(db, entry.entry_id).deleted_at is None
    assert [a.action for a in db.query(AuditLog).order_by(AuditLog.id)] == ["soft_delete", "restore"]


def test_restore_that_would_move_payments_is_locked(db: Session) -> None:
    """Old deleted entry + later payment: restoring would make FIFO pay it first → refused."""
    old = _entry(db, 10000, days_ago=10)
    _delete(db, old)
    _entry(db, 10000, days_ago=1)
    _pay(db, 5000)
    result = restore(db, user_id="user-b", entry_id=old.entry_id, dry_run=False, **APPROVAL)
    assert result.code == "LOCKED_PAYMENT"
    assert _reload(db, old.entry_id).deleted_at is not None


def test_cli_dry_run_by_default_and_execute(db: Session, session_factory) -> None:
    entry = _entry(db)
    args = ["delete", "--user-id", "user-b", "--entry-id", entry.entry_id,
            "--reason", "CS #2", "--actor", "최병철", "--approved-by", "최승현"]
    assert run(args, session_factory).code == "DRY_RUN"
    assert _reload(db, entry.entry_id).deleted_at is None
    assert run([*args, "--execute"], session_factory).code == "OK"
    assert _reload(db, entry.entry_id).deleted_at is not None


# --- 사용자 장부 전체 (--all-entries) ---

def _bulk(db: Session, *, dry_run: bool = False, user_id: str = "user-b", **over):
    return soft_delete_user_entries(db, user_id=user_id, dry_run=dry_run, **{**APPROVAL, **over})


def _mixed_ledger(db: Session) -> dict[str, LedgerEntry]:
    """Test ledger of user-b: one of every outcome + another user's entry."""
    entries = {
        "paid": _entry(db, 10000, days_ago=10),        # FIFO가 입금 5000을 배분 → LOCKED_PAYMENT
        "tax": _entry(db, 10000, days_ago=9, credit=False),
        "unpaid": _entry(db, 10000, days_ago=5),       # 입금 배분 0 → 삭제 가능
        "candidate": _entry(db, status="candidate", days_ago=4),
        "rejected": _entry(db, status="rejected", days_ago=3),
        "gone": _entry(db, days_ago=2, deleted=True),
        "other": _entry(db, user_id="user-a", days_ago=1),
    }
    db.add(TaxInvoiceEvent(user_id="user-b", ledger_entry_id=entries["tax"].id, nts_state="issued"))
    db.commit()
    _pay(db, 5000)
    return entries


def test_bulk_dry_run_changes_nothing(db: Session) -> None:
    e = _mixed_ledger(db)
    result = _bulk(db, dry_run=True)
    assert result.ok and result.code == "DRY_RUN"
    assert sorted(result.details["DELETED"]) == sorted(e[k].entry_id for k in ("unpaid", "candidate", "rejected"))
    assert result.details["LOCKED_PAYMENT"] == [e["paid"].entry_id]
    assert result.details["LOCKED_TAX"] == [e["tax"].entry_id]
    assert result.details["ALREADY_DELETED"] == [e["gone"].entry_id]
    assert all(_reload(db, x.entry_id).deleted_at is None for k, x in e.items() if k != "gone")
    assert db.query(AuditLog).count() == 0


def test_bulk_execute_deletes_eligible_with_one_audit_each(db: Session) -> None:
    e = _mixed_ledger(db)
    result = _bulk(db)
    assert result.ok and result.code == "OK"
    deleted = {e[k].entry_id for k in ("unpaid", "candidate", "rejected")}
    assert set(result.details["DELETED"]) == deleted
    for key in ("unpaid", "candidate", "rejected"):
        assert _reload(db, e[key].entry_id).deleted_at is not None
    # Lock 건·다른 사용자 건은 그대로
    for key in ("paid", "tax", "other"):
        assert _reload(db, e[key].entry_id).deleted_at is None
    audits = db.query(AuditLog).all()
    assert sorted(a.entity_id for a in audits) == sorted(deleted)
    assert all(a.action == "soft_delete" and a.after_data["reason"] == "CS #1 중복 입력"
               and a.after_data["approved_by"] == "최승현" and a.before_data["deleted_at"] is None
               for a in audits)
    # 입금 배분은 그대로: 남은 외상 10000에 입금 5000 → 미수 5000 (음수·초과 입금 없음)
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "user-b"}).json()["access_token"]
    detail = client.get("/api/v1/ledger/counterparty/김씨", headers={"Authorization": f"Bearer {token}"}).json()
    assert (detail["credit_total"], detail["paid_total"], detail["unpaid"]) == (10000, 5000, 5000)
    assert [x["allocated"] for x in detail["entries"]] == [5000]


def test_bulk_never_moves_payment_allocation(db: Session) -> None:
    """Payment spans two entries: both stay, only the unallocated tail goes."""
    first = _entry(db, 10000, days_ago=10)
    second = _entry(db, 10000, days_ago=8)
    tail = [_entry(db, 10000, days_ago=d) for d in (5, 3, 1)]
    _pay(db, 15000)
    result = _bulk(db)
    assert result.details["LOCKED_PAYMENT"] == [second.entry_id, first.entry_id]  # 최신 → 과거
    assert result.details["DELETED"] == [t.entry_id for t in reversed(tail)]
    assert _reload(db, first.entry_id).deleted_at is None
    assert _reload(db, second.entry_id).deleted_at is None


def test_bulk_rerun_skips_already_deleted(db: Session) -> None:
    a, b = _entry(db, days_ago=2), _entry(db, days_ago=1)
    assert _bulk(db).code == "OK"
    again = _bulk(db)
    assert again.ok and set(again.details) == {"ALREADY_DELETED"}
    assert sorted(again.details["ALREADY_DELETED"]) == sorted([a.entry_id, b.entry_id])
    assert db.query(AuditLog).count() == 2


def test_bulk_self_approval_refused(db: Session) -> None:
    entry = _entry(db)
    result = _bulk(db, approved_by="최병철")
    assert result.code == "APPROVAL_REQUIRED"
    assert _reload(db, entry.entry_id).deleted_at is None
    assert db.query(AuditLog).count() == 0


def test_bulk_unknown_user_is_not_found(db: Session) -> None:
    _entry(db, user_id="user-a")
    assert _bulk(db, user_id="user-zzz").code == "NOT_FOUND"
    assert db.query(AuditLog).count() == 0


_BULK_ARGS = ["--user-id", "user-b", "--reason", "UT 전 정리", "--actor", "최병철", "--approved-by", "최승현"]


def test_cli_all_entries_dry_run_and_execute(db: Session, session_factory) -> None:
    entry = _entry(db)
    args = ["delete", "--all-entries", *_BULK_ARGS]
    assert run(args, session_factory).code == "DRY_RUN"
    assert _reload(db, entry.entry_id).deleted_at is None
    assert run([*args, "--execute"], session_factory).code == "OK"
    assert _reload(db, entry.entry_id).deleted_at is not None


def test_cli_all_entries_output_has_no_pii(db: Session, monkeypatch, session_factory, capsys) -> None:
    entry = _entry(db)
    _pay(db, 1)
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    assert main(["delete", "--all-entries", *_BULK_ARGS]) == 0
    out = capsys.readouterr().out
    assert "LOCKED_PAYMENT: 1건" in out and entry.entry_id in out
    assert "김씨" not in out and "배추" not in out and "10000" not in out


@pytest.mark.parametrize("extra", [
    ["delete", "--all-entries", "--entry-id", "x"],   # 둘 다
    ["delete"],                                        # 둘 다 없음
    ["restore", "--all-entries"],                      # 묶음 복구 없음
])
def test_cli_target_options_are_exclusive(session_factory, extra: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        run([*extra, *_BULK_ARGS], session_factory)
    assert exc.value.code == 2
