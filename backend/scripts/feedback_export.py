"""DL-32 개선 데이터 내보내기 CLI — 최근 90일 동의 기록만, 가명, 레포 밖 비공개 폴더 (PM 로컬 터미널, PM 승인 후).

Dry run by default (counts only); the file is written only with --execute.

    cd backend
    export DATABASE_URL='<운영 DB 주소>'   # 환경변수로만 받는다 — 출력·로그·오류 메시지에 찍지 않음
    python -m scripts.feedback_export --out-dir ~/private/echobill-dl32            # 1) dry run: 건수 확인
    python -m scripts.feedback_export --out-dir ~/private/echobill-dl32 --execute  # 2) 파일 생성
    unset DATABASE_URL

- Run only after PM approval and a DB backup. The output folder must be a private folder outside
  the repo (any git working tree is refused); new folders are created 700, files 600.
- DATABASE_URL is read from the environment only (not backend/.env, not app settings), so a missing
  value stops the run instead of silently reading the local SQLite DB.
- Export is Seed-only (DL-25: never Golden / final evaluation). Seed made from real user records never
  goes into the repo (git history cannot be purged): keep it outside the repo and delete it by the same
  90-day rule (`python -m scripts.feedback_purge`).
Output has counts and the file name only — no names, amounts or text.
"""

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.services.feedback_export import ExportResult, RepoPathError, export_feedback

DB_URL_ENV = "DATABASE_URL"


class MissingDatabaseUrl(RuntimeError):
    """DATABASE_URL is not set in the environment."""


class InvalidDatabaseUrl(RuntimeError):
    """DATABASE_URL could not be used (e.g. a port or query value of the wrong type)."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="feedback_export", description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", required=True, help="내보낼 폴더 (레포 밖 비공개 폴더만 허용)")
    parser.add_argument("--execute", action="store_true", help="실제로 파일 생성 (없으면 dry run)")
    return parser


def session_factory_from_env(environ: Mapping[str, str]) -> Callable[[], Session]:
    """Session factory for the DB named by DATABASE_URL. hide_parameters keeps row values out of errors."""
    url = environ.get(DB_URL_ENV, "").strip()
    if not url:
        raise MissingDatabaseUrl(f"{DB_URL_ENV} 환경변수가 없습니다")
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    try:
        engine = create_engine(url, connect_args=connect_args, hide_parameters=True)
    except ValueError:  # e.g. int("notaport") — the message can carry part of the address
        raise InvalidDatabaseUrl("DATABASE_URL 형식 오류") from None
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def run(argv: list[str], session_factory: Callable[[], Session]) -> ExportResult:
    """Parse args and run one export in its own session."""
    args = build_parser().parse_args(argv)
    with session_factory() as db:
        return export_feedback(db, Path(args.out_dir).expanduser(), dry_run=not args.execute)


def render(result: ExportResult) -> str:
    """Counts-only report."""
    lines = [
        f"[{'DRY_RUN' if result.dry_run else 'EXECUTED'}] DL-32 개선 데이터 내보내기 (최근 90일)",
        f"  동의 기록(correct): {result.consented_correct}",
        f"  내보낼 기록: {result.exported}",
        f"  허용 목록 밖 칸이 있어 제외: {result.excluded_unlisted}",
        f"  삭제된 장부라 제외: {result.excluded_deleted}",
        f"  짧은 거래처명(1~2글자)이 품목 글에 있어 제외: {result.excluded_short_name}",
    ]
    if result.file_name:
        lines.append(f"  파일: {result.file_name}")
    elif not result.dry_run:
        lines.append("  파일: 만들지 않음 (내보낼 기록 0건)")
    return "\n".join(lines)


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    """CLI entry. DB errors print the error class only — their text can carry the DB address."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        result = run(argv, session_factory_from_env(os.environ if environ is None else environ))
    except (RepoPathError, FileExistsError, MissingDatabaseUrl) as exc:
        print(f"[REFUSED] {exc}")
        return 2
    # ImportError = DB driver for that URL not installed · InvalidDatabaseUrl = malformed address
    except (SQLAlchemyError, ImportError, InvalidDatabaseUrl) as exc:
        print(f"[DB_ERROR] DB 연결 또는 조회 실패: {type(exc).__name__} (주소·오류 내용은 표시하지 않음)")
        return 1
    print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
