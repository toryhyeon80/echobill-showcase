"""Dev 오프라인 Rule 채점: python eval/score_rule.py [--golden FILE] [--ko-normalize on|off] [--output FILE].

운영과 같은 Rule 경로(`structure_utterance`, LLM 꺼짐)로 Golden 문장을 파싱하고, 모든 레코드에 서버 검증
(`check_input_length` → `validate_candidate`) 결과를 `validation_response`로 붙여 `eval/scoring.py`로 채점한다
— 정상 건 정확도와 추가 확인 적합성(채점 v1.3, 기획서 §8.3 두 트랙)을 함께 본다. API 호출 없음.
개발용 Golden과 Seed v2(규칙 개선에 쓴 140건)만 받는다. Seed v1(측정 전용)·최종 Golden은 받지 않는다 (DL-26).
Seed v2는 문장 원문이 섞인 후보를 출력하지 않도록 기본으로 `candidates`를 뺀다 (--include-candidates로 켬).
"""
import argparse
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))
from app.config import Settings  # noqa: E402
from app.services.parse_pipeline import structure_utterance  # noqa: E402
from app.services.validation import check_input_length, validate_candidate  # noqa: E402
from eval.scoring import SCORING_VERSION, score_records  # noqa: E402

DEFAULT_GOLDEN = REPO_ROOT / "data" / "golden" / "golden_dev.jsonl"
DEV_SPLIT = "개발용 Golden"
SEED_SPLIT = "Seed"
ALLOWED_SPLIT = DEV_SPLIT  # kept for eval/regression.py, which stays Dev-only
SEED_V2_ID_PREFIX = "seed2-"  # Seed v1 ids do not start with this; v1 stays refused
NORMAL = "정상 구조화 가능"


def _require_scorable(records: list[dict[str, Any]]) -> str:
    """Dev Golden or Seed v2 only, one kind per file. Seed v1 is measurement-only and final Golden is never used."""
    bad = []
    for r in records:
        if r.get("split") == DEV_SPLIT:
            continue
        if r.get("split") == SEED_SPLIT and str(r.get("id", "")).startswith(SEED_V2_ID_PREFIX):
            continue
        bad.append(r.get("id"))
    if bad:
        raise ValueError(f"score_rule.py accepts only split '{DEV_SPLIT}' or Seed v2 ('{SEED_V2_ID_PREFIX}*') "
                         f"Seed v1·final Golden refused: {bad[:5]}")
    splits = {r["split"] for r in records}
    if len(splits) > 1:
        raise ValueError(f"mixed splits in one file: {sorted(splits)}")  # never blend Dev and Seed numbers
    return splits.pop()


def _jsonable(fields: dict[str, Any]) -> dict[str, Any]:
    """Rule output uses Decimal qty (D04-4); scoring expects int/float."""
    items = []
    for item in fields.get("items") or []:
        qty = item.get("qty")
        if isinstance(qty, Decimal):
            qty = int(qty) if qty == qty.to_integral_value() else float(qty)
        items.append({**item, "qty": qty})
    return {**fields, "items": items}


def score_rule(golden_path: Path, *, ko_normalize: bool, include_candidates: bool = True) -> dict[str, Any]:
    """Parse every Golden utterance with the Rule path and score it; returns metrics and per-ID details."""
    records = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    split = _require_scorable(records)
    settings = Settings(_env_file=None, app_env="development", llm_parse_enabled=False,
                        ko_numeral_normalize=ko_normalize)
    predictions, rejected = {}, {}
    for record in records:
        result = structure_utterance(record["utterance_text"], settings=settings)
        fields = _jsonable(result.fields)
        # Same order as /parse; Rule uses its own counterparty/item counts (no recount needed)
        failure = check_input_length(record["utterance_text"], settings) or validate_candidate(
            result.fields, counterparty_count=result.counterparty_count, item_count=result.item_count,
            transaction_type=result.transaction_type, settings=settings)
        response = ({"status_code": 200, "error_code": None, "problem_fields": [], "partial_candidate": None}
                    if failure is None else
                    {"status_code": 400, "error_code": failure.error_code, "problem_fields": failure.problem_fields,
                     "partial_candidate": failure.partial_candidate})
        predictions[record["id"]] = {"candidate": fields, "validation_response": response}
        if failure and record.get("expected_outcome") == NORMAL:
            rejected[record["id"]] = {"error_code": failure.error_code, "problem_fields": failure.problem_fields}
    metrics = score_records(records, predictions)
    normal_total = sum(1 for r in records if r.get("expected_outcome") == NORMAL)
    return {
        "run": {
            "scoring_version": SCORING_VERSION,
            "golden_file": golden_path.resolve().relative_to(REPO_ROOT).as_posix()
            if golden_path.resolve().is_relative_to(REPO_ROOT) else golden_path.name,
            "dataset_sha256": hashlib.sha256(golden_path.read_bytes()).hexdigest(),
            "parser": "rule (structure_utterance, llm_parse_enabled=false)",
            "ko_numeral_normalize": ko_normalize,
            "split": split,
        },
        "metrics": {
            "transaction_exact": metrics["transaction_exact"],
            "amount_exact": metrics["amount_exact"]["overall"],
            "required_fields_micro_f1": metrics["required_fields_micro"]["f1"],
            "normal_rejected_400": {"count": len(rejected), "total": normal_total},
            "confirmation_appropriateness": metrics["confirmation_appropriateness"],
            "credit_wrongly_confirmed": metrics["credit_wrongly_confirmed"],
            "credit_flip_saved": metrics["credit_flip_saved"],
        },
        "wrong_transaction_ids": sorted(r["id"] for r in metrics["records"] if r["transaction_exact"] is False),
        "wrong_amount_ids": sorted(r["id"] for r in metrics["records"] if r["amount_exact"] is False),
        "rejected_400": rejected,
        **({"candidates": predictions} if include_candidates else {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--ko-normalize", choices=("on", "off"), default="on",
                        help="KO_NUMERAL_NORMALIZE for the Rule path (default on, same as the app default)")
    parser.add_argument("--include-candidates", action="store_true",
                        help="Dev: always included. Seed v2: omitted unless this flag is given (keeps sentence-derived text out of logs)")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    is_dev = args.golden.resolve() == DEFAULT_GOLDEN.resolve()
    result = score_rule(args.golden, ko_normalize=args.ko_normalize == "on",
                        include_candidates=is_dev or args.include_candidates)
    output = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(output + "\n")
    else:
        print(output)


if __name__ == "__main__":
    main()
