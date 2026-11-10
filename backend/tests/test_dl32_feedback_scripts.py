"""DL-32 내보내기·파기 스크립트 — consent filter, 90-day window, allow list, pseudonyms, repo-path refusal,
file/folder modes, DB address never printed, purge touches export files only, dry run."""

import json
import logging
import os
import stat
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, FeedbackEvent, LedgerEntry
from app.services.feedback_export import REPO_ROOT, RepoPathError, alias_label, export_feedback
from app.services.feedback_purge import purge_exports
from scripts import feedback_export as export_cli
from scripts import feedback_purge as purge_cli

NOW = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)  # KST 10/12 12:00
SECRET_NAMES = ("김씨비밀상회", "박씨비밀상회", "이씨비밀상회")
SECRET_USER = "kakao-secret-user-777"


def _item(name: str = "배추", qty: str = "3", unit_price: int | None = 5000) -> dict:
    return {"name": name, "qty": qty, "unit": "박스", "unit_price": unit_price, "amount": None}


def _event(db: Session, *, consent: bool = True, action: str = "correct", diff: dict | None = None,
           created: datetime = NOW, user_id: str = SECRET_USER, counterparty: str = SECRET_NAMES[0],
           deleted_at: datetime | None = None) -> FeedbackEvent:
    entry = LedgerEntry(entry_id=str(uuid.uuid4()), user_id=user_id, status="confirmed",
                        counterparty=counterparty, items=[], total_amount=987654321, credit=True,
                        utterance_text="김씨비밀상회 배추 원문 발화", created_at=created, deleted_at=deleted_at)
    db.add(entry)
    db.flush()
    if diff is None and action == "correct":
        diff = {"counterparty": {"before": SECRET_NAMES[0], "after": SECRET_NAMES[1]}}
    ev = FeedbackEvent(user_id=user_id, ledger_entry_id=entry.id, action=action, corrected_data=diff,
                       ai_consent=consent, created_at=created)
    db.add(ev)
    db.commit()
    return ev


def _read_export(folder: Path) -> dict:
    files = list(folder.glob("dl32_feedback_*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


# --- export -------------------------------------------------------------------------------


def test_export_only_consented_correct_records(db: Session, tmp_path: Path) -> None:
    _event(db, consent=True)
    _event(db, consent=False)
    _event(db, consent=True, action="confirm")
    _event(db, consent=True, action="reject")
    result = export_feedback(db, tmp_path, dry_run=False, now=NOW)
    assert (result.consented_correct, result.exported, result.excluded_unlisted) == (1, 1, 0)
    data = _read_export(tmp_path)
    assert len(data["records"]) == 1 and data["records"][0]["action"] == "correct"


def test_export_has_only_allow_listed_fields_and_no_ids(db: Session, tmp_path: Path) -> None:
    diff = {
        "counterparty": {"before": SECRET_NAMES[0], "after": SECRET_NAMES[1]},
        "items": {"before": [_item(qty="2")], "after": [_item(qty="3")]},
        "total_amount": {"before": 10000, "after": 15000},
        "credit": {"before": None, "after": True},
    }
    _event(db, diff=diff, created=datetime(2026, 10, 11, 16, 30, tzinfo=timezone.utc))  # KST 10/12 01:30
    export_feedback(db, tmp_path, dry_run=False, now=NOW)
    data = _read_export(tmp_path)
    rec = data["records"][0]
    assert set(rec) == {"action", "date", "changes"}
    assert rec["date"] == "2026-10-12"  # KST day, no time
    assert set(rec["changes"]) == {"counterparty", "items", "total_amount", "credit"}
    assert rec["changes"]["items"]["after"] == [
        {"name": "배추", "qty": "3", "unit": "박스", "unit_price": 5000, "amount": None}]
    text = json.dumps(data, ensure_ascii=False)
    assert SECRET_USER not in text and "user_id" not in text
    assert "ledger_entry_id" not in text and "utterance" not in text and "원문" not in text
    assert all(name not in text for name in SECRET_NAMES)


def test_pseudonyms_same_name_same_alias_within_file(db: Session, tmp_path: Path) -> None:
    a, b, c = SECRET_NAMES
    _event(db, diff={"counterparty": {"before": a, "after": b}}, created=NOW - timedelta(hours=3))
    _event(db, diff={"counterparty": {"before": b, "after": a}}, created=NOW - timedelta(hours=2),
           user_id="other-user")  # same names from another user → same aliases
    _event(db, diff={"counterparty": {"before": None, "after": c}}, created=NOW - timedelta(hours=1))
    export_feedback(db, tmp_path, dry_run=False, now=NOW)
    changes = [r["changes"]["counterparty"] for r in _read_export(tmp_path)["records"]]
    assert changes == [
        {"before": "거래처A", "after": "거래처B"},
        {"before": "거래처B", "after": "거래처A"},
        {"before": None, "after": "거래처C"},
    ]


def test_counterparty_name_inside_item_name_is_pseudonymized(db: Session, tmp_path: Path) -> None:
    a, b, c = SECRET_NAMES
    # name from the correction, used in another record's item name too
    _event(db, diff={"counterparty": {"before": a, "after": b},
                     "items": {"before": [_item(name=f"{a} 배추")], "after": [_item(name="배추")]}},
           created=NOW - timedelta(hours=2))
    # items-only correction: the 장부's own 거래처 (c) inside the item name; b from the record above
    _event(db, diff={"items": {"before": [_item(name=f"{c}배추")], "after": [_item(name=f"{b} 무")]}},
           counterparty=c, created=NOW - timedelta(hours=1))
    export_feedback(db, tmp_path, dry_run=False, now=NOW)
    data = _read_export(tmp_path)
    first, second = (r["changes"] for r in data["records"])
    assert first["counterparty"] == {"before": "거래처A", "after": "거래처B"}
    assert [i["name"] for i in first["items"]["before"]] == ["거래처A 배추"]
    assert [i["name"] for i in second["items"]["before"]] == ["거래처C배추"]
    assert [i["name"] for i in second["items"]["after"]] == ["거래처B 무"]
    assert all(name not in json.dumps(data, ensure_ascii=False) for name in SECRET_NAMES)


@pytest.mark.parametrize("items_first", [False, True])  # True = PostgreSQL JSONB key order
def test_longer_counterparty_name_is_replaced_first(db: Session, tmp_path: Path, items_first: bool) -> None:
    counterparty = {"counterparty": {"before": "가나다", "after": "가나다상회"}}
    items = {"items": {"before": [], "after": [_item(name="가나다상회 배추")]}}
    _event(db, diff={**items, **counterparty} if items_first else {**counterparty, **items})
    export_feedback(db, tmp_path, dry_run=False, now=NOW)
    changes = _read_export(tmp_path)["records"][0]["changes"]
    assert changes["counterparty"] == {"before": "거래처A", "after": "거래처B"}
    assert changes["items"]["after"][0]["name"] == "거래처B 배추"  # not "거래처A상회 배추"


@pytest.mark.parametrize(("counterparty", "item_name"), [
    ("가나다상회", "가나다 상회 배추"),
    ("가나다 상회", "가나다상회 배추"),
    ("가나다 상회", "가 나다상 회 배추"),
])
def test_names_match_ignoring_spaces(db: Session, tmp_path: Path, counterparty: str, item_name: str) -> None:
    _event(db, diff={"counterparty": {"before": None, "after": counterparty},
                     "items": {"before": [], "after": [_item(name=item_name)]}}, counterparty=counterparty)
    export_feedback(db, tmp_path, dry_run=False, now=NOW)
    changes = _read_export(tmp_path)["records"][0]["changes"]
    assert changes["counterparty"]["after"] == "거래처A"
    assert changes["items"]["after"][0]["name"] == "거래처A 배추"


def test_spaced_and_joined_name_share_one_alias(db: Session, tmp_path: Path) -> None:
    _event(db, diff={"counterparty": {"before": "가나다 상회", "after": "가나다상회"}})
    export_feedback(db, tmp_path, dry_run=False, now=NOW)
    assert _read_export(tmp_path)["records"][0]["changes"]["counterparty"] == {
        "before": "거래처A", "after": "거래처A"}


@pytest.mark.parametrize(("counterparty", "item_name"), [("우리", "우리밀"), ("김", "김치"), ("김", "김 치")])
def test_own_short_name_in_item_text_excludes_record(db: Session, tmp_path: Path, caplog: pytest.LogCaptureFixture,
                                                     counterparty: str, item_name: str) -> None:
    _event(db)  # one clean record
    _event(db, diff={"items": {"before": [], "after": [_item(name=item_name)]}}, counterparty=counterparty)
    caplog.set_level(logging.DEBUG)
    result = export_feedback(db, tmp_path, dry_run=False, now=NOW)
    assert (result.consented_correct, result.exported, result.excluded_short_name) == (2, 1, 1)
    assert item_name not in json.dumps(_read_export(tmp_path), ensure_ascii=False)
    assert "짧은 거래처명(1~2글자)이 품목 글에 있어 제외: 1" in export_cli.render(result)
    assert item_name not in caplog.text


def test_short_name_is_not_replaced_and_other_records_stay(db: Session, tmp_path: Path) -> None:
    _event(db, diff={"counterparty": {"before": None, "after": "김"}}, counterparty="김",
           created=NOW - timedelta(hours=2))  # short name in the 거래처 칸 only → alias, kept
    _event(db, diff={"items": {"before": [], "after": [_item(name="김치"), _item(name="우리밀")]}},
           counterparty="가나다상회", created=NOW - timedelta(hours=1))  # another record's 「김」 does not matter
    result = export_feedback(db, tmp_path, dry_run=False, now=NOW)
    assert (result.exported, result.excluded_short_name) == (2, 0)
    first, second = (r["changes"] for r in _read_export(tmp_path)["records"])
    assert first["counterparty"] == {"before": None, "after": "거래처A"}
    assert [i["name"] for i in second["items"]["after"]] == ["김치", "우리밀"]


def test_short_name_inside_long_name_keeps_record(db: Session, tmp_path: Path) -> None:
    _event(db, diff={"counterparty": {"before": "김씨", "after": "김씨상회"},
                     "items": {"before": [], "after": [_item(name="김씨상회 배추")]}}, counterparty="김씨상회")
    result = export_feedback(db, tmp_path, dry_run=False, now=NOW)
    assert (result.exported, result.excluded_short_name) == (1, 0)
    changes = _read_export(tmp_path)["records"][0]["changes"]
    assert changes["counterparty"] == {"before": "거래처A", "after": "거래처B"}
    assert changes["items"]["after"][0]["name"] == "거래처B 배추"


def test_unit_and_qty_text_follow_the_same_rules(db: Session, tmp_path: Path) -> None:
    item = {**_item(), "unit": "가나다상회 박스", "qty": "가나다상회 3"}
    _event(db, diff={"items": {"before": [], "after": [item]}}, counterparty="가나다상회",
           created=NOW - timedelta(hours=2))
    _event(db, diff={"items": {"before": [], "after": [{**_item(), "unit": "우리박스"}]}}, counterparty="우리",
           created=NOW - timedelta(hours=1))
    result = export_feedback(db, tmp_path, dry_run=False, now=NOW)
    assert (result.exported, result.excluded_short_name) == (1, 1)
    after = _read_export(tmp_path)["records"][0]["changes"]["items"]["after"][0]
    assert (after["unit"], after["qty"]) == ("거래처A 박스", "거래처A 3")


def test_aliases_do_not_depend_on_insert_order(db: Session, tmp_path: Path) -> None:
    rows = [
        (NOW - timedelta(hours=3), {"counterparty": {"before": SECRET_NAMES[0], "after": SECRET_NAMES[1]}}),
        (NOW - timedelta(hours=2), {"counterparty": {"before": None, "after": SECRET_NAMES[2]}}),
        (NOW - timedelta(hours=1), {"items": {"before": [], "after": [_item(name=f"{SECRET_NAMES[1]} 배추")]}}),
    ]
    exports = []
    for n, order in enumerate((rows, rows[::-1])):
        db.query(FeedbackEvent).delete()
        db.query(LedgerEntry).delete()
        db.commit()
        for created, diff in order:
            _event(db, diff=diff, created=created)
        export_feedback(db, tmp_path / str(n), dry_run=False, now=NOW)
        exports.append(_read_export(tmp_path / str(n))["records"])
    assert exports[0] == exports[1]
    assert [r["changes"].get("counterparty") for r in exports[0]] == [
        {"before": "거래처A", "after": "거래처B"}, {"before": None, "after": "거래처C"}, None]


def test_records_of_deleted_ledger_entries_are_not_exported(db: Session, tmp_path: Path) -> None:
    _event(db)
    _event(db, diff={"counterparty": {"before": None, "after": SECRET_NAMES[2]}},
           deleted_at=NOW - timedelta(days=1))  # CS soft delete
    orphan = _event(db, diff={"counterparty": {"before": None, "after": SECRET_NAMES[2]}})
    db.delete(db.get(LedgerEntry, orphan.ledger_entry_id))  # 장부 row gone
    db.commit()
    result = export_feedback(db, tmp_path, dry_run=False, now=NOW)
    assert (result.consented_correct, result.exported, result.excluded_deleted) == (3, 1, 2)
    assert SECRET_NAMES[2] not in json.dumps(_read_export(tmp_path), ensure_ascii=False)
    assert "삭제된 장부라 제외: 2" in export_cli.render(result)


def test_alias_label_goes_past_z() -> None:
    assert [alias_label(i) for i in (0, 25, 26, 27, 51, 52)] == [
        "거래처A", "거래처Z", "거래처AA", "거래처AB", "거래처AZ", "거래처BA"]


@pytest.mark.parametrize("diff", [
    {"memo": {"before": None, "after": "자유 메모"}},  # field off the list
    {"items": {"before": [], "after": [{**_item(), "note": "메모"}]}},  # item key off the list
    {"total_amount": {"before": 1000, "after": "만오천원"}},  # text where a number belongs
    {"counterparty": {"before": "a", "after": "b", "note": "x"}},  # not a plain before/after pair
])
def test_unlisted_field_excludes_record_and_is_counted(db: Session, tmp_path: Path, diff: dict) -> None:
    _event(db)  # one clean record
    _event(db, diff=diff)
    result = export_feedback(db, tmp_path, dry_run=False, now=NOW)
    assert (result.consented_correct, result.exported, result.excluded_unlisted) == (2, 1, 1)
    text = json.dumps(_read_export(tmp_path), ensure_ascii=False)
    assert "메모" not in text and "만오천원" not in text


def test_item_line_amount_is_exported_like_unit_price(db: Session, tmp_path: Path) -> None:
    item = {**_item(), "amount": 15000}
    _event(db, diff={"items": {"before": [], "after": [item]}})
    export_feedback(db, tmp_path, dry_run=False, now=NOW)
    assert _read_export(tmp_path)["records"][0]["changes"]["items"]["after"][0]["amount"] == 15000


def test_item_line_amount_as_text_excludes_record(db: Session, tmp_path: Path) -> None:
    _event(db, diff={"items": {"before": [], "after": [{**_item(), "amount": "만오천원"}]}})
    result = export_feedback(db, tmp_path, dry_run=False, now=NOW)
    assert (result.exported, result.excluded_unlisted) == (0, 1)


@pytest.mark.parametrize(("age", "included"), [
    (timedelta(days=89), True),
    (timedelta(days=90), True),  # exactly 90 days → still exported
    (timedelta(days=90, seconds=1), False),
])
def test_export_window_is_last_90_days(db: Session, tmp_path: Path, age: timedelta, included: bool) -> None:
    _event(db, created=NOW - age)
    result = export_feedback(db, tmp_path, dry_run=True, now=NOW)
    assert (result.consented_correct, result.exported) == ((1, 1) if included else (0, 0))


def test_export_keeps_db_rows(db: Session, tmp_path: Path) -> None:
    _event(db)
    _event(db, created=NOW - timedelta(days=200))
    export_feedback(db, tmp_path, dry_run=False, now=NOW)
    db.expire_all()
    assert db.query(FeedbackEvent).count() == 2  # 장부 정보 — export never deletes the original


def test_export_dry_run_writes_nothing(db: Session, tmp_path: Path) -> None:
    _event(db)
    out = tmp_path / "new-folder"
    result = export_feedback(db, out, dry_run=True, now=NOW)
    assert result.exported == 1 and result.file_name is None
    assert not out.exists()


def test_export_refuses_paths_inside_repo(db: Session, tmp_path: Path) -> None:
    _event(db)
    with pytest.raises(RepoPathError):
        export_feedback(db, REPO_ROOT / "data" / "export", dry_run=False, now=NOW)
    assert not (REPO_ROOT / "data" / "export").exists()
    fake_repo = tmp_path / "other-repo"
    (fake_repo / ".git").mkdir(parents=True)  # another git working tree is refused too
    with pytest.raises(RepoPathError):
        export_feedback(db, fake_repo / "out", dry_run=False, now=NOW, repo_root=REPO_ROOT)


def test_export_never_overwrites_and_skips_empty(db: Session, tmp_path: Path) -> None:
    result = export_feedback(db, tmp_path, dry_run=False, now=NOW)
    assert result.file_name is None and not list(tmp_path.iterdir())  # 0 records → no file
    _event(db)
    export_feedback(db, tmp_path, dry_run=False, now=NOW)
    with pytest.raises(FileExistsError):
        export_feedback(db, tmp_path, dry_run=False, now=NOW)
    assert len(list(tmp_path.iterdir())) == 1


def test_failed_write_leaves_no_tmp_file(db: Session, tmp_path: Path, monkeypatch) -> None:
    _event(db)

    def fail_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        export_feedback(db, tmp_path, dry_run=False, now=NOW)
    assert list(tmp_path.iterdir()) == []  # no half-written export, no leftover .tmp


def test_export_cli_prints_counts_only(db: Session, tmp_path: Path, session_factory, capsys,
                                       caplog: pytest.LogCaptureFixture) -> None:
    _event(db, diff={"counterparty": {"before": SECRET_NAMES[0], "after": SECRET_NAMES[1]},
                     "total_amount": {"before": 987654321, "after": 123456789}})
    caplog.set_level(logging.DEBUG)
    result = export_cli.run(["--out-dir", str(tmp_path)], session_factory)
    report = export_cli.render(result)
    assert result.dry_run and "DRY_RUN" in report and not list(tmp_path.iterdir())
    result = export_cli.run(["--out-dir", str(tmp_path), "--execute"], session_factory)
    report += export_cli.render(result) + caplog.text
    for secret in (*SECRET_NAMES, SECRET_USER, "987654321", "123456789", "거래처A"):
        assert secret not in report


# --- purge --------------------------------------------------------------------------------


def _write_export(folder: Path, name: str, oldest: datetime) -> Path:
    path = folder / f"dl32_feedback_{name}.json"
    path.write_text(json.dumps({"format": "echobill-dl32-feedback", "version": 1,
                                "oldest_record_at": oldest.isoformat(), "records": []}), encoding="utf-8")
    return path


def test_purge_never_touches_db_rows(db: Session, tmp_path: Path) -> None:
    _event(db, created=NOW - timedelta(days=200))  # consented and old — stays (DB 원본은 탈퇴까지 보관)
    _event(db, consent=False, created=NOW - timedelta(days=200))
    _write_export(tmp_path, "old", NOW - timedelta(days=200))
    purge_exports(tmp_path, dry_run=False, now=NOW)
    db.expire_all()
    assert db.query(FeedbackEvent).count() == 2


def test_purge_deletes_whole_file_by_oldest_record(db: Session, tmp_path: Path) -> None:
    cutoff = NOW - timedelta(days=90)
    old = _write_export(tmp_path, "old", cutoff - timedelta(days=1))
    recent = _write_export(tmp_path, "recent", cutoff + timedelta(days=1))
    junk = tmp_path / "dl32_feedback_junk.json"
    junk.write_text("not json", encoding="utf-8")
    other = tmp_path / "notes.json"
    other.write_text("{}", encoding="utf-8")
    result = purge_exports(tmp_path, dry_run=False, now=NOW)
    assert result.files == [old.name] and result.files_unreadable == [junk.name]
    assert not old.exists() and recent.exists() and junk.exists() and other.exists()


def test_purge_removes_file_written_by_export(db: Session, tmp_path: Path) -> None:
    _event(db, created=NOW - timedelta(days=10))  # oldest record in the file
    _event(db, created=NOW - timedelta(days=5))
    export_feedback(db, tmp_path, dry_run=False, now=NOW)
    assert purge_exports(tmp_path, dry_run=False, now=NOW + timedelta(days=80)).files == []  # 90 days
    result = purge_exports(tmp_path, dry_run=False, now=NOW + timedelta(days=81))  # 91 days
    assert len(result.files) == 1 and not list(tmp_path.glob("dl32_feedback_*.json"))


def test_purge_lists_leftover_tmp_but_never_deletes_it(tmp_path: Path) -> None:
    old = _write_export(tmp_path, "old", NOW - timedelta(days=200))
    tmp = tmp_path / f".{old.name}.tmp"  # export killed mid-write
    tmp.write_text('{"format": "echobill-dl32-feedback", "records": [', encoding="utf-8")
    result = purge_exports(tmp_path, dry_run=False, now=NOW)
    assert result.files == [old.name] and result.files_unreadable == [tmp.name]
    assert not old.exists() and tmp.exists()
    assert tmp.name in purge_cli.render(result)


def test_purge_dry_run_changes_nothing(tmp_path: Path) -> None:
    path = _write_export(tmp_path, "old", NOW - timedelta(days=120))
    result = purge_exports(tmp_path, dry_run=True, now=NOW)
    assert result.files == [path.name] and path.exists()


def test_purge_refuses_repo_path_and_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(RepoPathError):
        purge_exports(REPO_ROOT, dry_run=True, now=NOW)
    with pytest.raises(NotADirectoryError):
        purge_exports(tmp_path / "typo", dry_run=True, now=NOW)


def test_purge_cli_lists_then_deletes_file_names_only(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = _write_export(tmp_path, "old", datetime.now(timezone.utc) - timedelta(days=120))
    caplog.set_level(logging.DEBUG)
    result = purge_cli.run(["--export-dir", str(tmp_path)])
    report = purge_cli.render(result)
    assert result.dry_run and "DRY_RUN" in report and path.name in report and path.exists()
    result = purge_cli.run(["--export-dir", str(tmp_path), "--execute"])
    assert not result.dry_run and not path.exists()


# --- export CLI: DB address from the environment, never printed -----------------------------


def _file_db(tmp_path: Path) -> str:
    """SQLite file DB with one consented correction, for running main() end to end."""
    url = f"sqlite:///{(tmp_path / 'local.db').as_posix()}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        _event(db)
    engine.dispose()
    return url


def test_export_main_reads_database_url_from_env(tmp_path: Path, capsys) -> None:
    url = _file_db(tmp_path)
    out = tmp_path / "out"
    assert export_cli.main(["--out-dir", str(out)], {"DATABASE_URL": url}) == 0
    assert not out.exists()  # dry run
    assert export_cli.main(["--out-dir", str(out), "--execute"], {"DATABASE_URL": url}) == 0
    assert len(list(out.glob("dl32_feedback_*.json"))) == 1
    assert "local.db" not in capsys.readouterr().out


def test_export_main_refuses_without_database_url(tmp_path: Path, capsys) -> None:
    assert export_cli.main(["--out-dir", str(tmp_path)], {}) == 2
    assert "DATABASE_URL" in capsys.readouterr().out


@pytest.mark.parametrize("url", [
    "notadriver://admin:SECRETPW@db.example.internal:5432/echobill",  # unknown driver
    "sqlite:////SECRETPW-no-such-dir/x.db",  # connection fails
    "postgresql://admin:SECRETPW@db.example.internal:notaport/echobill",  # port not a number (ValueError)
    "sqlite:///SECRETPW.db?timeout=db.example.internal",  # query value of the wrong type (ValueError)
])
def test_export_main_never_prints_db_address(tmp_path: Path, capsys, caplog: pytest.LogCaptureFixture,
                                             url: str) -> None:
    caplog.set_level(logging.DEBUG)
    assert export_cli.main(["--out-dir", str(tmp_path)], {"DATABASE_URL": url}) == 1
    out = capsys.readouterr()
    assert "DB_ERROR" in out.out
    for text in (out.out, out.err, caplog.text):
        assert "SECRETPW" not in text and "db.example.internal" not in text


# --- file and folder modes (POSIX only — chmod means something else on Windows) ----------------

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")


@posix_only
def test_export_creates_private_folder_and_file(db: Session, tmp_path: Path) -> None:
    _event(db)
    out = tmp_path / "new" / "private"
    old_umask = os.umask(0)  # a permissive umask must not widen the modes
    try:
        result = export_feedback(db, out, dry_run=False, now=NOW)
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "new").stat().st_mode) == 0o700
    assert stat.S_IMODE((out / result.file_name).stat().st_mode) == 0o600


@posix_only
def test_existing_wide_folder_is_left_and_warned(db: Session, tmp_path: Path,
                                                 caplog: pytest.LogCaptureFixture) -> None:
    _event(db)
    out = tmp_path / "shared"
    out.mkdir()
    os.chmod(out, 0o755)
    caplog.set_level(logging.WARNING)
    result = export_feedback(db, out, dry_run=False, now=NOW)
    assert stat.S_IMODE(out.stat().st_mode) == 0o755  # not changed
    assert stat.S_IMODE((out / result.file_name).stat().st_mode) == 0o600
    assert sum("wider than 700" in r.getMessage() for r in caplog.records) == 1
