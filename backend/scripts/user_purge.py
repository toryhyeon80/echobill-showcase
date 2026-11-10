"""사용성 테스트 계정 실제 삭제 CLI — BE owner only, after identity check + PM approval (동의서 "테스트 끝난 날 실제 삭제").

Dry run by default (counts only); nothing is deleted without --execute.

    cd backend
    python -m scripts.user_purge --signup-at "2026-11-03 14:05" \\
        --reason "UT 11/3 종료 — 동의서 실제 삭제" --actor 최병철 --approved-by 최승현 [--execute]

--signup-at is the KST signup minute from the observation notes (±1 minute); several matches are refused, then repeat
with the seconds it lists ("2026-11-03 14:05:32"). --user-id <카카오 회원번호> works instead when the ID is known.

Deletes every row of that user in one transaction (app/services/user_purge.py) and leaves one audit_log row with
counts and an HMAC of the user ID (server secret). Run scripts/feedback_purge.py too if the owner's records were
exported (DL-32).

Production: run inside the Railway service (DATABASE_URL from its env), never with a copied URL.
Output has counts per table and signup times only — no user ID, names, amounts or text.
"""

import argparse
import sys
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.services.user_purge import (
    PurgeResult,
    purge_by_signup,
    purge_user,
    signup_range,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="user_purge", description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--user-id", help="본인 확인한 사용자(카카오 회원번호)")
    target.add_argument("--signup-at", type=signup_range, help='관찰 기록의 가입 시각 KST "YYYY-MM-DD HH:MM[:SS]"')
    parser.add_argument("--reason", required=True, help="삭제 사유 (테스트 날짜 포함 권장)")
    parser.add_argument("--actor", required=True, help="실행자 (BE 담당)")
    parser.add_argument("--approved-by", required=True, help="승인자 (PM)")
    parser.add_argument("--execute", action="store_true", help="실제로 삭제 (없으면 dry run)")
    return parser


def run(argv: list[str], session_factory: Callable[[], Session]) -> PurgeResult:
    """Parse args and run one purge in its own session."""
    args = build_parser().parse_args(argv)
    approval = {"reason": args.reason, "actor": args.actor, "approved_by": args.approved_by,
                "dry_run": not args.execute}
    with session_factory() as db:
        if args.signup_at:
            return purge_by_signup(db, signup_at=args.signup_at, **approval)
        return purge_user(db, user_id=args.user_id, **approval)


def main(argv: list[str] | None = None) -> int:
    from app.db import SessionLocal

    result = run(sys.argv[1:] if argv is None else argv, SessionLocal)
    print(f"[{result.code}] {result.message}")
    for table, count in result.counts.items():
        print(f"  {table}: {count}건")
    if result.audit_id is not None:
        print(f"audit_log id: {result.audit_id}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
