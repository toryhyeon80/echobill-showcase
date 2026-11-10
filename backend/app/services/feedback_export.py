"""DL-32 개선 데이터 내보내기 — consented `correct` feedback of the last 90 days, pseudonymized, outside the repo.

Only allow-listed fields leave the DB: action, 거래처(가명), 품목·수량·단위·단가·품목별 금액, 총액, 외상,
날짜(KST 일). A record whose corrected_data holds anything else is not exported (counted only).
A 거래처명 seen in this export (수정 기록·장부) that appears inside item text (name·unit·qty) is replaced by its
alias too, ignoring spaces (가나다 상회 = 가나다상회). 1~2글자 names are not replaced in text (우리밀·김치 stay);
a record whose own short 거래처명 is still in its item text is not exported (counted only).
Records of 장부 deleted by CS (deleted_at) are not exported (counted only).
The 거래처 ↔ 가명 table lives in memory for one export and is never written or returned.
The DB rows stay (장부 정보, kept until 탈퇴); the 90-day limit applies to the exported copy (PM 10/12).
Files are created 600, new folders 700. Results carry counts only — no names, amounts or text.
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FeedbackEvent, LedgerEntry
from app.services.fifo import KST
from app.services.idempotency import as_utc

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXPORT_FORMAT = "echobill-dl32-feedback"
EXPORT_VERSION = 1
FILE_PREFIX = "dl32_feedback_"
RETENTION = timedelta(days=90)  # DL-32 / DL-13 「최대 3개월」 — export window and file purge
FILE_MODE = 0o600
DIR_MODE = 0o700

# corrected_data = {field: {"before": v, "after": v}} (services/feedback._diff)
_ITEM_FIELDS = {"name", "qty", "unit", "unit_price", "amount"}
_ITEM_TEXT_FIELDS = ("name", "unit", "qty")  # the only free-text values an export can carry
MIN_TEXT_NAME_LEN = 3  # shorter 거래처명 (spaces removed) are not replaced inside text — 병철 #110


class RepoPathError(ValueError):
    """Export/purge directory is inside a git working tree."""


def ensure_outside_repo(directory: Path, repo_root: Path = REPO_ROOT) -> Path:
    """Resolve `directory`; refuse this repo and any other git working tree (e.g. another worktree)."""
    resolved = directory.resolve()
    if resolved.is_relative_to(repo_root.resolve()):
        raise RepoPathError("레포 안 경로는 쓸 수 없습니다 — 레포 밖 폴더를 지정하세요")
    if any((p / ".git").exists() for p in (resolved, *resolved.parents)):
        raise RepoPathError("git 작업 폴더 안 경로는 쓸 수 없습니다 — 레포 밖 폴더를 지정하세요")
    return resolved


def alias_label(index: int) -> str:
    """0 → 거래처A, 25 → 거래처Z, 26 → 거래처AA, 27 → 거래처AB …"""
    letters = ""
    n = index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return f"거래처{letters}"


def _compact(name: str) -> str:
    """Name with all spaces removed — the key names are compared by."""
    return re.sub(r"\s+", "", name)


def _name_pattern(names: set[str]) -> re.Pattern[str] | None:
    """Regex for compact `names` allowing spaces between letters; longest first so one inside another
    is not half-replaced (가나다상회 before 가나다)."""
    if not names:
        return None
    ordered = sorted(names, key=lambda n: (-len(n), n))
    return re.compile("|".join(r"\s*".join(map(re.escape, n)) for n in ordered))


class _Pseudonymizer:
    """Same name (spaces ignored) → same alias within one export. The table is never exposed."""

    def __init__(self) -> None:
        self._table: dict[str, str] = {}

    def __call__(self, name: str | None) -> str | None:
        if name is None:
            return None
        key = _compact(name)
        if key not in self._table:
            self._table[key] = alias_label(len(self._table))
        return self._table[key]

    def in_text(self, text: Any, pattern: re.Pattern[str] | None) -> Any:
        """Replace every name `pattern` finds inside a text value with its alias; other values unchanged."""
        if not isinstance(text, str) or pattern is None:
            return text
        return pattern.sub(lambda m: self(m.group(0)), text)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _ok_item(item: Any) -> bool:
    """Item dict with allow-listed keys and plain value types only."""
    if not isinstance(item, dict) or not set(item) <= _ITEM_FIELDS:
        return False
    return (
        isinstance(item.get("name"), (str, type(None)))
        and isinstance(item.get("unit"), (str, type(None)))
        and (item.get("qty") is None or isinstance(item["qty"], str) or _is_int(item["qty"])
             or isinstance(item["qty"], float))
        and all(item.get(k) is None or _is_int(item[k]) for k in ("unit_price", "amount"))
    )


def _ok_value(field: str, value: Any) -> bool:
    if field == "counterparty":
        return value is None or isinstance(value, str)
    if field == "total_amount":
        return value is None or _is_int(value)
    if field == "credit":
        return value is None or isinstance(value, bool)
    if field == "items":
        return isinstance(value, list) and all(_ok_item(i) for i in value)
    return False


def _allowed(event: FeedbackEvent) -> bool:
    """True if corrected_data holds allow-listed fields and values only."""
    diff = event.corrected_data
    if not isinstance(diff, dict) or not diff:
        return False
    for field, change in diff.items():
        if not isinstance(change, dict) or set(change) != {"before", "after"}:
            return False
        if not (_ok_value(field, change["before"]) and _ok_value(field, change["after"])):
            return False
    return True


def _counterparty_names(event: FeedbackEvent, entry: LedgerEntry) -> set[str]:
    """Compact 거래처명 in this record: the correction's before/after and the 장부's own 거래처."""
    change = event.corrected_data.get("counterparty") or {}
    names = {change.get("before"), change.get("after"), entry.counterparty}
    return {_compact(n) for n in names if isinstance(n, str) and n.strip()}


def _item_texts(event: FeedbackEvent) -> list[str]:
    """Every free-text item value of the correction (before and after)."""
    change = event.corrected_data.get("items") or {}
    return [item[k] for side in ("before", "after") for item in change.get(side, [])
            for k in _ITEM_TEXT_FIELDS if isinstance(item.get(k), str)]


def _short_name_left(event: FeedbackEvent, own_short: set[str], long_pattern: re.Pattern[str] | None) -> bool:
    """True if one of the record's own short 거래처명 is still in its item text after long names are taken out
    (so 거래처 김씨→김씨상회 with item 「김씨상회 배추」 is kept)."""
    short_pattern = _name_pattern(own_short)
    if short_pattern is None:
        return False
    for text in _item_texts(event):
        rest = long_pattern.sub("\0", text) if long_pattern else text
        if short_pattern.search(rest):
            return True
    return False


def _convert(event: FeedbackEvent, alias: _Pseudonymizer, long_pattern: re.Pattern[str] | None) -> dict[str, Any]:
    """Export record of an allow-listed event; 거래처명 → alias, also inside item text.

    counterparty goes first and the rest by name, so aliases do not depend on the DB's key order
    (PostgreSQL JSONB stores `items` before `counterparty`).
    """
    changes: dict[str, Any] = {}
    for field in sorted(event.corrected_data, key=lambda f: (f != "counterparty", f)):
        change = event.corrected_data[field]
        if field == "counterparty":
            changes[field] = {side: alias(change[side]) for side in ("before", "after")}
        elif field == "items":
            changes[field] = {side: [{k: alias.in_text(v, long_pattern) if k in _ITEM_TEXT_FIELDS else v
                                      for k, v in item.items()} for item in change[side]]
                              for side in ("before", "after")}
        else:
            changes[field] = {"before": change["before"], "after": change["after"]}
    return {
        "action": event.action,
        "date": as_utc(event.created_at).astimezone(KST).date().isoformat(),
        "changes": changes,
    }


@dataclass
class ExportResult:
    """Counts only — safe to print."""

    dry_run: bool
    consented_correct: int = 0  # ai_consent = true · action = correct · last 90 days
    exported: int = 0
    excluded_unlisted: int = 0  # corrected_data had a field/value off the allow list
    excluded_deleted: int = 0  # 장부 deleted by CS (deleted_at) or no longer there
    excluded_short_name: int = 0  # own 1~2글자 거래처명 left in item text (not replaceable without damage)
    file_name: str | None = None


def _eligible(db: Session, now: datetime) -> list[tuple[FeedbackEvent, LedgerEntry | None]]:
    """Consented corrections made within RETENTION of `now` (exactly 90 days included), with their 장부.

    The window is checked in Python so SQLite (naive) and PostgreSQL (aware) agree on the boundary.
    """
    stmt = (
        select(FeedbackEvent, LedgerEntry)
        .outerjoin(LedgerEntry, LedgerEntry.id == FeedbackEvent.ledger_entry_id)
        .where(FeedbackEvent.ai_consent.is_(True), FeedbackEvent.action == "correct")
        .order_by(FeedbackEvent.created_at, FeedbackEvent.id)
    )
    cutoff = now - RETENTION
    return [(ev, entry) for ev, entry in db.execute(stmt) if as_utc(ev.created_at) >= cutoff]


def _make_private_dir(target: Path) -> None:
    """Create missing folders as 700 (chmod after mkdir — umask cannot widen or narrow it).

    An existing folder is left as it is; if it is wider than 700 a one-line warning is logged.
    """
    missing = [p for p in (target, *target.parents) if not p.exists()]
    for p in reversed(missing):
        p.mkdir()
        os.chmod(p, DIR_MODE)
    if not missing and os.name != "nt" and target.stat().st_mode & 0o077:
        logger.warning("DL-32 export folder is wider than 700 (left unchanged): mode=%o",
                       target.stat().st_mode & 0o777)


def _write_private(path: Path, payload: dict[str, Any]) -> None:
    """Create `path` exclusively with mode 600 from the first byte (fchmod so umask does not matter)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = -1
            json.dump(payload, f, ensure_ascii=False, indent=1)
    finally:
        if fd != -1:
            os.close(fd)


def export_feedback(
    db: Session, out_dir: Path, *, dry_run: bool = True, now: datetime | None = None,
    repo_root: Path = REPO_ROOT,
) -> ExportResult:
    """Build the pseudonymized export; write one JSON file under `out_dir` only when not dry run."""
    target = ensure_outside_repo(out_dir, repo_root)
    now = now or datetime.now(timezone.utc)
    events = _eligible(db, now)
    result = ExportResult(dry_run=dry_run, consented_correct=len(events))
    allowed: list[tuple[FeedbackEvent, set[str]]] = []
    for ev, entry in events:
        if entry is None or entry.deleted_at is not None:
            result.excluded_deleted += 1
        elif not _allowed(ev):
            result.excluded_unlisted += 1
        else:
            allowed.append((ev, _counterparty_names(ev, entry)))
    # every 3+ letter 거래처명 of the export is replaced in every record's text
    long_pattern = _name_pattern({n for _, names in allowed for n in names if len(n) >= MIN_TEXT_NAME_LEN})
    kept: list[FeedbackEvent] = []
    for ev, names in allowed:
        if _short_name_left(ev, {n for n in names if len(n) < MIN_TEXT_NAME_LEN}, long_pattern):
            result.excluded_short_name += 1
        else:
            kept.append(ev)
    alias = _Pseudonymizer()
    records = [_convert(ev, alias, long_pattern) for ev in kept]
    oldest = min((as_utc(ev.created_at) for ev in kept), default=None)
    result.exported = len(records)
    if dry_run or not records:
        logger.info("DL-32 export %s: eligible=%d exported=%d excluded=%d deleted=%d short_name=%d",
                    "dry-run" if dry_run else "empty", result.consented_correct, result.exported,
                    result.excluded_unlisted, result.excluded_deleted, result.excluded_short_name)
        return result

    payload = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": now.isoformat(),
        "oldest_record_at": oldest.isoformat(),  # purge deletes the whole file 90 days after this
        "records": records,
    }
    _make_private_dir(target)
    name = f"{FILE_PREFIX}{now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    final = target / name
    if final.exists():  # never overwrite an earlier export
        raise FileExistsError(f"같은 이름의 내보내기 파일이 이미 있습니다: {name}")
    tmp = target / f".{name}.tmp"
    try:
        _write_private(tmp, payload)
        os.replace(tmp, final)  # no half-written export file
    except OSError:
        logger.exception("DL-32 export write failed: file=%s", name)
        tmp.unlink(missing_ok=True)
        raise
    result.file_name = name
    logger.info("DL-32 export written: exported=%d excluded=%d deleted=%d short_name=%d file=%s",
                result.exported, result.excluded_unlisted, result.excluded_deleted, result.excluded_short_name, name)
    return result
