"""Demo account data CLI — PM only, for presentation screenshots (app/services/demo_seed.py).

Dry run by default; nothing changes without --execute.

    cd backend
    python -m scripts.seed_demo --user-id <데모 계정 카카오 회원번호> [--execute]
    python -m scripts.seed_demo --user-id <같은 회원번호> --reset [--execute]   # 날짜를 새로 맞출 때

Run it just before capture (경과일은 실행일 기준), in the daytime (KST) — right after midnight
"today" rows land on the previous day. Output has counts only — no names or amounts.

Production — inside the running backend container, never with a copied DATABASE_URL. `railway run`
does NOT work: it runs on your Mac, and the private DB host (postgres.railway.internal) is unreachable
from there (병철 #169 리뷰):

    railway ssh --service <백엔드 서비스 이름>      # opens a shell in /app/backend
    python -m scripts.seed_demo --user-id <회원번호>              # dry run, check the counts
    python -m scripts.seed_demo --user-id <회원번호> --execute

Do not rehearse on the demo account after seeding: a row you add by hand has no demo tag, so --reset
refuses (NOT_DEMO_ONLY) and the row counts in the ops metrics. Rehearse first, then seed.
"""

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.services.demo_seed import DemoResult, reset, seed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seed_demo", description=__doc__.splitlines()[0])
    parser.add_argument("--user-id", required=True, help="데모 계정(가입 완료)의 카카오 회원번호")
    parser.add_argument("--reset", action="store_true",
                        help="이 스크립트가 넣은 데모 기록만 숨기기 (Soft Delete: 장부 deleted_at, 입금 canceled)")
    parser.add_argument("--execute", action="store_true", help="실제로 변경 (없으면 미리 보기)")
    return parser


def run(argv: list[str], session_factory: Callable[[], Session], now: datetime | None = None) -> DemoResult:
    """Parse args and run seed or reset in its own session."""
    args = build_parser().parse_args(argv)
    now = now or datetime.now(UTC)
    with session_factory() as db:
        if args.reset:
            return reset(db, args.user_id, now=now, dry_run=not args.execute)
        return seed(db, args.user_id, now=now, dry_run=not args.execute)


def main(argv: list[str] | None = None) -> int:
    from app.db import SessionLocal

    result = run(sys.argv[1:] if argv is None else argv, SessionLocal)
    print(f"[{result.code}] {result.message}")
    if result.ok:
        print(f"  장부 {result.entries}건 · 입금 {result.payments}건 · 모의 발행 {result.tax_events}건")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
