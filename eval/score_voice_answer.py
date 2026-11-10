"""DL-31 음성 되묻기 답 칸별 개발용 채점: python eval/score_voice_answer.py [--backend DIR] [--no-item-names] [--output FILE].

판정 세트(`eval/data/voice_answer_judge_v1.jsonl`, 칸당 정답 있는 답 15 + 실패해야 하는 답 5, 수량·총액·합계 선택은
PM 10/16 끝 물음표 답 14줄 추가 — 정답 있는 답 6 + 실패해야 하는 답 8)를 BE의
`interpret(field, text, *, choices=None, item_names=None)`로 해석해 칸별로 채점한다. STT·LLM·API 호출 없음.

- 해석 성공률 = 정답 있는 답 중 값까지 맞은 ok / 정답 있는 답 수
- 틀린 값 채움 = ok인데 값이 틀린 수 (실패해야 하는 답이 ok면 틀린 값 채움으로 셈)
- 판정: 성공률 90%↑ + 틀린 값 채움 0 → "dev 기준 켜기 가능" (DL-31 기준을 개발용 세트에 적용)

이 세트는 개발용(dev set)이다. 공식 10/12 판정은 PM 보관 세트로 하며, 여기 숫자를 공식 판정처럼 쓰지 않는다.

`--backend`로 다른 worktree(예: PR #72)의 `backend/`를 채점할 수 있다.
APP_ENV가 비어 있으면 development로 실행한다 (`.env` 없이도 동작, 운영 설정은 건드리지 않음).
"""
import argparse
import inspect
import json
import os
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
DEV_SET_NOTICE = "dev set — 공식 판정은 PM 보관 세트"
DEFAULT_SET = REPO_ROOT / "eval" / "data" / "voice_answer_judge_v1.jsonl"
PASS_SUCCESS_RATE = 0.9

# 판정 세트 칸 → BE interpret 칸 (합계 선택은 total + choices)
FIELD_MAP = {"credit": "credit", "quantity": "qty", "total_amount": "total",
             "counterparty": "counterparty", "mismatch_choice": "total"}
FIELD_ORDER = ("credit", "quantity", "total_amount", "mismatch_choice", "counterparty")
# 같은 단위로 보는 표기 (STT·말버릇 차이일 뿐 다른 값이 아님)
UNIT_ALIASES = {"키로": "kg", "킬로": "kg", "kg": "kg", "그램": "g", "g": "g"}


def load_set(path: Path) -> list[dict[str, Any]]:
    """Read the judge set (one JSON object per line)."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _same_value(field: str, expected: Any, got: Any) -> bool:
    """Compare by meaning: qty as Decimal ("3" == 3), amounts as int, credit as bool, names as stripped text."""
    if field == "quantity":
        try:
            return Decimal(str(got)) == Decimal(str(expected))
        except InvalidOperation:
            return False
    if field == "credit":
        return isinstance(got, bool) and got is expected
    if field in ("total_amount", "mismatch_choice"):
        return not isinstance(got, bool) and isinstance(got, int) and got == expected
    return isinstance(got, str) and got.strip() == expected


def _same_unit(expected: str | None, got: str | None) -> bool:
    """A unit conflicts only when both sides name a unit and they differ (no unit = FE uses the question's unit)."""
    if expected is None or got is None:
        return True
    return UNIT_ALIASES.get(got, got) == UNIT_ALIASES.get(expected, expected)


def judge_row(row: dict[str, Any], status: str, value: Any, unit: str | None) -> str:
    """One row → hit (정답 ok) · miss (정답 있는 답을 실패) · correct_fail · wrong_fill."""
    if status != "ok":
        return "miss" if row["expected_status"] == "ok" else "correct_fail"
    if row["expected_status"] != "ok":
        return "wrong_fill"
    if _same_value(row["field"], row["expected_value"], value) and _same_unit(row.get("expected_unit"), unit):
        return "hit"
    return "wrong_fill"


def score(rows: list[dict[str, Any]], interpret: Callable[..., Any], *, pass_item_names: bool) -> dict[str, Any]:
    """Interpret every row and aggregate per field; returns per-field metrics and per-row details."""
    details = []
    for row in rows:
        kwargs: dict[str, Any] = {}
        if row.get("choices") is not None:
            kwargs["choices"] = row["choices"]
        if pass_item_names and row.get("item_names") is not None:
            kwargs["item_names"] = row["item_names"]
        result = interpret(FIELD_MAP[row["field"]], row["text"], **kwargs)
        outcome = judge_row(row, result.status, result.value, result.unit)
        details.append({"id": row["id"], "field": row["field"], "text": row["text"],
                        "expected_status": row["expected_status"], "expected_value": row.get("expected_value"),
                        "expected_unit": row.get("expected_unit"), "status": result.status,
                        "value": result.value, "unit": result.unit, "reason": result.reason, "outcome": outcome})

    fields = {}
    for field in FIELD_ORDER:
        rows_f = [d for d in details if d["field"] == field]
        if not rows_f:
            continue
        answerable = [d for d in rows_f if d["expected_status"] == "ok"]
        hits = sum(d["outcome"] == "hit" for d in rows_f)
        wrong = [d for d in rows_f if d["outcome"] == "wrong_fill"]
        rate = hits / len(answerable) if answerable else 0.0
        fields[field] = {
            "total": len(rows_f), "answerable": len(answerable), "hits": hits, "success_rate": round(rate, 4),
            "wrong_fills": len(wrong), "enable_1014": rate >= PASS_SUCCESS_RATE and not wrong,
            "reasons_on_answerable": dict(Counter(d["reason"] for d in answerable if d["status"] != "ok")),
            "reasons_on_should_fail": dict(Counter(d["reason"] for d in rows_f
                                                   if d["expected_status"] != "ok" and d["status"] != "ok")),
        }
    return {"set": DEV_SET_NOTICE, "pass_item_names": pass_item_names, "fields": fields, "details": details}


def _load_interpret(backend: Path) -> Callable[..., Any]:
    """Import `interpret` from the given backend dir (another worktree for a PR comparison).

    Settings read `.env` from the working directory, so we run from that backend dir like uvicorn does.
    Without APP_ENV (no `.env`, e.g. a fresh clone or CI) settings validation demands production secrets, so this
    offline dev script defaults APP_ENV to development before the import. It only needs the amount/qty limits.
    """
    os.environ.setdefault("APP_ENV", "development")
    backend = backend.resolve()
    os.chdir(backend)
    sys.path.insert(0, str(backend))
    from app.services.voice_answer import interpret  # noqa: E402
    return interpret


def _print_report(result: dict[str, Any]) -> None:
    print(f"[{DEV_SET_NOTICE}]")
    print(f"item_names 전달: {'예' if result['pass_item_names'] else '아니오'}")
    print(f"{'칸':<16}{'성공률':>12}{'틀린 값 채움':>10}  판정")
    for field, m in result["fields"].items():
        verdict = "dev 기준 켜기 가능" if m["enable_1014"] else "끔"
        print(f"{field:<16}{m['hits']:>3}/{m['answerable']:<3}{m['success_rate']:>6.0%}{m['wrong_fills']:>10}  {verdict}")
    for kind, title in (("wrong_fill", "틀린 값 채움"), ("miss", "정답 있는 답 해석 실패")):
        rows = [d for d in result["details"] if d["outcome"] == kind]
        print(f"\n[{title}] {len(rows)}건")
        for d in rows:
            want = d["expected_value"] if d["expected_status"] == "ok" else "실패"
            got = f"{d['value']}{d['unit'] or ''}" if d["status"] == "ok" else f"failed:{d['reason']}"
            print(f"  {d['id']:<11} {d['text']!r:<26} 기대={want}{d['expected_unit'] or ''}  결과={got}")
    print("\n[실패 reason 분포] 칸: 정답 있는 답 / 실패해야 하는 답")
    for field, m in result["fields"].items():
        print(f"  {field:<16} {m['reasons_on_answerable']} / {m['reasons_on_should_fail']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", type=Path, default=DEFAULT_SET)
    parser.add_argument("--backend", type=Path, default=REPO_ROOT / "backend")
    parser.add_argument("--no-item-names", action="store_true", help="item_names를 넘기지 않음 (PR #72 판정)")
    parser.add_argument("--output", type=Path, help="결과 JSON 저장 경로")
    args = parser.parse_args()
    args.set = args.set.resolve()
    if args.output:
        args.output = args.output.resolve()

    interpret = _load_interpret(args.backend)
    pass_item_names = not args.no_item_names and "item_names" in inspect.signature(interpret).parameters
    result = score(load_set(args.set), interpret, pass_item_names=pass_item_names)
    _print_report(result)
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
