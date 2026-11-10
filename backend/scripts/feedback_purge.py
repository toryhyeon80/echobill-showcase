"""DL-32 개선용 사본 파기 CLI — 가장 오래된 기록이 90일 지난 내보내기 파일을 통째로 삭제 (DB 접속 없음).

Dry run by default (target list and counts only); nothing is deleted without --execute.

    cd backend
    python -m scripts.feedback_purge --export-dir ~/private/echobill-dl32            # 1) dry run: 대상 확인
    python -m scripts.feedback_purge --export-dir ~/private/echobill-dl32 --execute  # 2) 삭제

- DB rows (feedback_events) are 장부 정보 kept until 탈퇴 — this script never connects to the DB.
- Seed made from real user records never goes into the repo; keep it outside the repo and delete it by
  the same 90-day rule (this script only sees the export folder it is given).
Output has file names and counts only — no names, amounts or text.
"""

import argparse
import sys
from pathlib import Path

from app.services.feedback_export import RepoPathError
from app.services.feedback_purge import PurgeResult, purge_exports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="feedback_purge", description=__doc__.splitlines()[0])
    parser.add_argument("--export-dir", required=True, help="내보내기 파일 폴더 (레포 밖)")
    parser.add_argument("--execute", action="store_true", help="실제로 삭제 (없으면 dry run)")
    return parser


def run(argv: list[str]) -> PurgeResult:
    """Parse args and run one purge."""
    args = build_parser().parse_args(argv)
    return purge_exports(Path(args.export_dir).expanduser(), dry_run=not args.execute)


def render(result: PurgeResult) -> str:
    """File names and counts only."""
    verb = "지울" if result.dry_run else "지운"
    lines = [
        f"[{'DRY_RUN' if result.dry_run else 'EXECUTED'}] DL-32 개선용 사본 파기 "
        f"(가장 오래된 기록이 {result.cutoff.isoformat()} 이전인 파일)",
        f"  {verb} 내보내기 파일: {len(result.files)}",
        *(f"    {name}" for name in result.files),
    ]
    if result.files_unreadable:
        lines.append(f"  읽을 수 없어 건너뛴 파일 (삭제 안 함): {len(result.files_unreadable)}")
        lines += [f"    {name}" for name in result.files_unreadable]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(sys.argv[1:] if argv is None else argv)
    except (RepoPathError, NotADirectoryError) as exc:
        print(f"[REFUSED] {exc}")
        return 2
    print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
