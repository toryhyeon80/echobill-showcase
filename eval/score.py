"""Offline scoring: python eval/score.py --golden FILE --predictions FILE."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from eval.scoring import SCORING_VERSION, score_records  # noqa: E402


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def _run_summary(predictions_path: Path) -> dict:
    """The run summary saved next to a predictions file (its settings decide how the run is replayed)."""
    summary_path = predictions_path.with_name(predictions_path.name.replace(".predictions.jsonl", ".summary.json"))
    if not summary_path.exists():
        raise ValueError(f"run summary not found next to predictions: {summary_path.name}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def _attach_validation(golden: list[dict], rows: list[dict], predictions_path: Path) -> list[dict]:
    """Copies of the rows with `validation_response` from the shared BE validator; existing values are kept."""
    backend_root = REPO_ROOT / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    from app.config import Settings

    from eval.validation_bridge import validation_response_for

    run = _run_summary(predictions_path)["run"]
    # Same normalization as the run, so the Rule counterparty count matches what /parse would have seen.
    settings = Settings(_env_file=None, app_env="development",
                        ko_numeral_normalize=bool(run.get("ko_numeral_normalize", False)))
    utterances = {record["id"]: record["utterance_text"] for record in golden}
    attached = []
    for row in rows:
        if row.get("validation_response") is None:
            candidate = None if row.get("error") else row.get("candidate")
            row = {**row, "validation_response": validation_response_for(candidate, utterances[row["id"]], settings,
                                                                         row.get("transaction_type"))}
        attached.append(row)
    return attached


def _postprocess_rows(golden: list[dict], rows: list[dict], predictions_path: Path) -> tuple[list[dict], dict, str | None]:
    """Apply the Rule post-processing to saved candidates (A-4c 3장); the input rows are not changed.

    The LLM input is rebuilt from the Golden utterance with the run's normalization setting, and its
    hash must match the saved `sent_text_sha256`, otherwise the offline result would not be reproducible.
    Like the live path, a normalization-off run gets the credit rules only (version "…-credit", PM 10/5).
    """
    backend_root = REPO_ROOT / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    from types import SimpleNamespace

    from app.services.parse_llm import build_llm_input
    from app.services.postprocess import postprocess_candidate, postprocess_label, unit_price_rules_active

    summary = _run_summary(predictions_path)
    # PM #37 리뷰: 이미 후처리된 출력에 다시 적용하면 원 출력 대비 효과를 잴 수 없다.
    # 필드가 없는 옛 summary(플래그 도입 전)는 당시 기본값 False라 허용한다.
    if summary["run"].get("llm_postprocess") is True:
        raise ValueError("이미 후처리된 출력 — --postprocess 적용 불가. 측정 시 llm_postprocess=false로 원 출력을 저장할 것")
    settings = SimpleNamespace(ko_numeral_normalize=bool(summary["run"].get("ko_numeral_normalize", False)))
    utterances = {record["id"]: record["utterance_text"] for record in golden}
    unit_price_rules = unit_price_rules_active(True, settings.ko_numeral_normalize)

    applied_counts: dict[str, int] = {}
    processed = []
    for row in rows:
        sent_text = build_llm_input(utterances[row["id"]], settings)
        if row.get("sent_text_sha256") != hashlib.sha256(sent_text.encode("utf-8")).hexdigest():
            raise ValueError(f"sent_text_sha256 mismatch for {row['id']}: rebuilt LLM input differs from the run")
        new_row = dict(row)
        if isinstance(row.get("candidate"), dict):
            new_row["candidate"], applied = postprocess_candidate(row["candidate"], sent_text,
                                                                  unit_price_rules=unit_price_rules)
            for rule in applied:
                applied_counts[rule] = applied_counts.get(rule, 0) + 1
        processed.append(new_row)
    return processed, applied_counts, postprocess_label(True, settings.ko_numeral_normalize)


def _changed_ids(before: dict, after: dict) -> tuple[dict, dict]:
    """Per metric, records that post-processing made correct (fixed) or wrong (broken)."""
    b = {row["id"]: row for row in before["records"]}
    a = {row["id"]: row for row in after["records"]}
    fixed, broken = {}, {}
    for key in ("transaction_exact", "amount_exact"):
        fixed[key] = sorted(i for i in b if b[i][key] is False and a[i][key] is True)
        broken[key] = sorted(i for i in b if b[i][key] is True and a[i][key] is False)
    return fixed, broken


def rescore(golden_path: Path, predictions_path: Path, *, only_predicted: bool = False,
            postprocess: bool = False, attach_validation: bool = False) -> dict:
    """Score saved predictions; refuse silent gaps between Golden and prediction IDs.

    attach_validation adds `validation_response` (eval/validation_bridge.py) after any post-processing,
    the same order as /parse. The predictions file itself is never changed.
    """
    golden = [json.loads(line) for line in golden_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    rows = [json.loads(line) for line in predictions_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    predicted_ids = [row['id'] for row in rows]
    if len(set(predicted_ids)) != len(predicted_ids):
        raise ValueError('duplicate prediction IDs')
    golden_ids = {record['id'] for record in golden}
    unknown = sorted(set(predicted_ids) - golden_ids)
    if unknown:
        raise ValueError(f'predictions for IDs not in Golden: {unknown[:5]}')
    missing = sorted(golden_ids - set(predicted_ids))
    if missing and not only_predicted:
        # Missing rows would silently count as generation failures (e.g. 40 runs vs 60 Golden).
        raise ValueError(f'{len(missing)} Golden IDs have no prediction; pass --only-predicted to narrow scope')
    if only_predicted:
        golden = [record for record in golden if record['id'] in set(predicted_ids)]
    result = {
        'run': {
            'scoring_version': SCORING_VERSION,
            'golden_file': _display_path(golden_path),
            'dataset_sha256': hashlib.sha256(golden_path.read_bytes()).hexdigest(),
            'predictions_file': _display_path(predictions_path),
            'scored_ids': len(golden),
            'excluded_golden_ids': missing if only_predicted else [],
            'postprocess_version': None,
            'validation_attached': attach_validation,
        },
    }
    with_validation = (lambda rs: _attach_validation(golden, rs, predictions_path)) if attach_validation else (lambda rs: rs)
    result['metrics'] = score_records(golden, {row['id']: row for row in with_validation(rows)})
    if postprocess:
        processed, applied_counts, version = _postprocess_rows(golden, rows, predictions_path)
        # A saved validation_response describes the raw candidate; drop it where post-processing changed the candidate.
        raw = {row['id']: row.get('candidate') for row in rows}
        processed = [{k: v for k, v in row.items() if k != 'validation_response'}
                     if row.get('candidate') != raw[row['id']] else row for row in processed]
        before = result['metrics']
        result['metrics'] = score_records(golden, {row['id']: row for row in with_validation(processed)})
        fixed, broken = _changed_ids(before, result['metrics'])
        result['run']['postprocess_version'] = version
        result['postprocess'] = {
            'before': {key: before[key] for key in ('transaction_exact', 'amount_exact', 'required_fields_micro')},
            'fixed_ids': fixed,
            'broken_ids': broken,
            'applied_counts': applied_counts,
        }
        if version.endswith('-credit'):
            result['postprocess']['note'] = '정규화 off 실행 — 외상 규칙만 적용, 단가 규칙 미적용'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--golden', type=Path, required=True)
    parser.add_argument('--predictions', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--only-predicted', action='store_true',
                        help='score only Golden IDs present in the predictions file')
    parser.add_argument('--attach-validation', action='store_true',
                        help='add validation_response from the shared BE validator (after --postprocess, as /parse does)')
    parser.add_argument('--postprocess', action='store_true',
                        help='apply the amount/credit Rule post-processing to saved candidates before scoring')
    args = parser.parse_args()
    result = rescore(args.golden, args.predictions, only_predicted=args.only_predicted,
                     postprocess=args.postprocess, attach_validation=args.attach_validation)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(output + '\n')
    else:
        print(output)


if __name__ == '__main__':
    main()
