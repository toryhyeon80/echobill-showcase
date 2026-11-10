"""Ops metrics CLI (PM #18) — prints markdown tables; totals only, no PII.

    cd backend
    python -m scripts.ops_metrics                        # 오늘 · 최근 7일 (DB)
    python -m scripts.ops_metrics --log-file railway.log # + 400·503(옛 로그 504) 건수 · 음성 답 칸별 성공률 (로그)

Production: run inside the Railway service; export logs with `railway logs > railway.log`
(the log covers whatever window Railway returns — say so when reporting).
"""

import argparse
import sys
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.services.ops_metrics import (
    compute, count_log_errors, count_postprocess_400, count_voice_answers, default_periods, render,
)


def run(argv: list[str], session_factory: Callable[[], Session]) -> str:
    parser = argparse.ArgumentParser(prog="ops_metrics", description=__doc__.splitlines()[0])
    parser.add_argument("--log-file", help="exported server log (api_error lines)")
    args = parser.parse_args(argv)
    with session_factory() as db:
        periods = [compute(db, label, start, end) for label, start, end in default_periods()]
    log_errors = pp_400 = voice_answers = None
    if args.log_file:
        with open(args.log_file, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        log_errors, pp_400 = count_log_errors(lines), count_postprocess_400(lines)
        voice_answers = count_voice_answers(lines)
    return render(periods, log_errors, pp_400, voice_answers)


def main(argv: list[str] | None = None) -> int:
    from app.db import SessionLocal

    print(run(sys.argv[1:] if argv is None else argv, SessionLocal))
    return 0


if __name__ == "__main__":
    sys.exit(main())
