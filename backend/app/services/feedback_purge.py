"""DL-32 개선용 사본 파기 — exported files whose oldest record is older than 90 days.

Only export files (`dl32_feedback_*.json`) in the given folder are deleted, whole. The DB rows
(feedback_events) are 장부 정보 kept until 탈퇴 and are never touched here (PM 10/12).
Files that are not readable exports are never deleted (listed only) — this includes a `.tmp` left by an
export killed mid-write (PM 10/13: list it, delete by hand).
Results carry file names and counts only — no names, amounts or text.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.services.feedback_export import EXPORT_FORMAT, FILE_PREFIX, REPO_ROOT, RETENTION, ensure_outside_repo
from app.services.idempotency import as_utc

logger = logging.getLogger(__name__)


@dataclass
class PurgeResult:
    """File names and counts only — safe to print."""

    dry_run: bool
    cutoff: datetime
    files: list[str] = field(default_factory=list)
    files_unreadable: list[str] = field(default_factory=list)  # not a valid export, or a leftover .tmp


def _oldest_record_at(path: Path) -> datetime | None:
    """`oldest_record_at` of a DL-32 export file, or None if it is not one."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
            return None
        return as_utc(datetime.fromisoformat(data["oldest_record_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("DL-32 purge: not a readable export file=%s", path.name)
        return None


def purge_exports(
    export_dir: Path, *, dry_run: bool = True, now: datetime | None = None, repo_root: Path = REPO_ROOT,
) -> PurgeResult:
    """List (dry run) or delete export files whose oldest record is older than 90 days."""
    folder = ensure_outside_repo(export_dir, repo_root)
    if not folder.is_dir():  # a mistyped folder must not look like "no files to purge"
        raise NotADirectoryError(f"내보내기 폴더가 없습니다: {folder}")
    now = now or datetime.now(timezone.utc)
    result = PurgeResult(dry_run=dry_run, cutoff=now - RETENTION)

    expired: list[Path] = []
    for path in sorted(folder.glob(f"{FILE_PREFIX}*.json")):
        oldest = _oldest_record_at(path)
        if oldest is None:
            result.files_unreadable.append(path.name)
        elif oldest < result.cutoff:
            expired.append(path)
    result.files = [p.name for p in expired]
    result.files_unreadable += [p.name for p in sorted(folder.glob(f".{FILE_PREFIX}*.json.tmp"))]

    if not dry_run:
        for path in expired:
            path.unlink()
    logger.info("DL-32 purge %s: files=%d unreadable=%d", "dry-run" if dry_run else "done",
                len(result.files), len(result.files_unreadable))
    return result
