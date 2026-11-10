"""CS Soft Delete / restore CLI — BE owner only, after identity check + PM approval (DL-06).

Dry run by default; nothing changes without --execute.

    cd backend
    python -m scripts.cs_soft_delete delete  --user-id <카카오 회원번호> --entry-id <UUID> \\
        --reason "CS #12 중복 입력" --actor 최병철 --approved-by 최승현 [--execute]
    python -m scripts.cs_soft_delete restore ...same options... (복구는 한 건씩만)

    # 사용자 장부 전체 (팀 테스트 장부·중복 입력 정리) — --entry-id 대신 --all-entries
    python -m scripts.cs_soft_delete delete  --user-id <카카오 회원번호> --all-entries \\
        --reason "UT 전 테스트 장부 정리" --actor 최병철 --approved-by 최승현 [--execute]

--all-entries: candidate·confirmed·rejected 전부를 건마다 같은 규칙(Lock·audit_log)으로, 거래 하나씩
커밋한다. 모의 발행 연결(LOCKED_TAX)·입금 배분(LOCKED_PAYMENT) 건은 건너뛰고 결과에 남긴다.
입금 기록은 지우지 않는다. FIFO 규칙은 app/services/cs_soft_delete.py 상단 설명 참고.

Production: run inside the Railway service (DATABASE_URL from its env), never with a copied URL.
Output has no utterance text or other PII — entry ID, result code and audit ID only
(--all-entries: 결과별 건수와 entry_id 목록만 — 거래처명·금액 없음).
"""

import argparse
import sys
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.services.cs_soft_delete import (
    CSResult,
    restore,
    soft_delete,
    soft_delete_user_entries,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cs_soft_delete", description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["delete", "restore"])
    parser.add_argument("--user-id", required=True, help="본인 확인한 사용자(카카오 회원번호)")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--entry-id", help="거래 entry_id (UUID)")
    target.add_argument("--all-entries", action="store_true",
                        help="이 사용자의 삭제되지 않은 거래 전부 (delete 전용)")
    parser.add_argument("--reason", required=True, help="변경 사유 (CS 번호 포함 권장)")
    parser.add_argument("--actor", required=True, help="실행자 (BE 담당)")
    parser.add_argument("--approved-by", required=True, help="승인자 (PM)")
    parser.add_argument("--execute", action="store_true", help="실제로 변경 (없으면 dry run)")
    return parser


def run(argv: list[str], session_factory: Callable[[], Session]) -> CSResult:
    """Parse args and run one action in its own session."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.all_entries:
        if args.action != "delete":
            parser.error("--all-entries는 delete에만 쓸 수 있습니다 (복구는 한 건씩).")
        with session_factory() as db:
            return soft_delete_user_entries(
                db,
                user_id=args.user_id,
                reason=args.reason,
                actor=args.actor,
                approved_by=args.approved_by,
                dry_run=not args.execute,
            )
    fn = soft_delete if args.action == "delete" else restore
    with session_factory() as db:
        return fn(
            db,
            user_id=args.user_id,
            entry_id=args.entry_id,
            reason=args.reason,
            actor=args.actor,
            approved_by=args.approved_by,
            dry_run=not args.execute,
        )


def main(argv: list[str] | None = None) -> int:
    from app.db import SessionLocal

    result = run(sys.argv[1:] if argv is None else argv, SessionLocal)
    print(f"[{result.code}] {result.message}")
    if result.audit_id is not None:
        print(f"audit_log id: {result.audit_id}")
    for key, value in result.details.items():
        if isinstance(value, list):  # --all-entries: 결과별 건수 + entry_id
            print(f"  {key}: {len(value)}건")
            for entry_id in value:
                print(f"    {entry_id}")
        else:
            print(f"  {key}: {value}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
