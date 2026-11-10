import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.score import REPO_ROOT, rescore

sys.path.insert(0, str(REPO_ROOT / "backend"))
from app.services.parse_llm import build_llm_input  # noqa: E402


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    return path


def _golden(record_id: str) -> dict:
    return {
        "id": record_id,
        "utterance_text": "가나상회 무 세 개 현금 삼천 원",
        "expected_json": {"counterparty": "가나상회", "items": [{"name": "무", "qty": 3}],
                          "total_amount": 3000, "credit": False},
        "expected_outcome": "정상 구조화 가능",
        "notes": {"total_source": "stated"},
    }


def test_missing_predictions_are_refused_unless_scope_is_narrowed(tmp_path: Path):
    golden = _write(tmp_path / "golden.jsonl", [_golden("a"), _golden("b")])
    predictions = _write(tmp_path / "pred.jsonl", [{"id": "a", "candidate": _golden("a")["expected_json"]}])

    with pytest.raises(ValueError, match="no prediction"):
        rescore(golden, predictions)

    result = rescore(golden, predictions, only_predicted=True)
    assert result["run"]["scored_ids"] == 1
    assert result["run"]["excluded_golden_ids"] == ["b"]
    assert result["metrics"]["transaction_exact"] == {"correct": 1, "total": 1, "rate": 1.0}


def test_predictions_for_unknown_ids_are_refused(tmp_path: Path):
    golden = _write(tmp_path / "golden.jsonl", [_golden("a")])
    predictions = _write(tmp_path / "pred.jsonl", [{"id": "a", "candidate": None}, {"id": "z", "candidate": None}])
    with pytest.raises(ValueError, match="not in Golden"):
        rescore(golden, predictions)


def _pp_case(tmp_path: Path, *, utterance: str, gold_credit, pred_credit, sent_text: str | None = None,
             normalize: bool = True, llm_postprocess: bool | None = None) -> tuple[Path, Path]:
    """One normal record whose saved prediction differs from the gold only in credit."""
    record = _golden("pp")
    record["utterance_text"] = utterance
    record["expected_json"]["credit"] = gold_credit
    golden = _write(tmp_path / "golden.jsonl", [record])
    candidate = {**record["expected_json"], "credit": pred_credit}
    sent = sent_text if sent_text is not None else build_llm_input(utterance, SimpleNamespace(ko_numeral_normalize=normalize))
    row = {"id": "pp", "candidate": candidate, "sent_text_sha256": hashlib.sha256(sent.encode("utf-8")).hexdigest()}
    predictions = _write(tmp_path / "run.predictions.jsonl", [row])
    run = {"ko_numeral_normalize": normalize}
    if llm_postprocess is not None:  # None = old summary written before the flag existed
        run["llm_postprocess"] = llm_postprocess
    (tmp_path / "run.summary.json").write_text(json.dumps({"run": run}), encoding="utf-8")
    return golden, predictions


def test_postprocess_rescoring_lists_fixed_ids_and_keeps_the_original_file(tmp_path: Path):
    """A-4c 3장: post-processing is applied offline; the predictions file itself never changes."""
    golden, predictions = _pp_case(tmp_path, utterance="가나상회 무 세 개 삼천 원 달아놔", gold_credit=True, pred_credit=None)
    before = predictions.read_bytes()

    result = rescore(golden, predictions, postprocess=True)

    assert predictions.read_bytes() == before
    assert result["metrics"]["transaction_exact"]["correct"] == 1
    assert result["postprocess"]["fixed_ids"]["transaction_exact"] == ["pp"]
    assert result["postprocess"]["broken_ids"]["transaction_exact"] == []
    assert result["postprocess"]["before"]["transaction_exact"]["correct"] == 0
    assert result["run"]["postprocess_version"].startswith("pp-")


def test_postprocess_rescoring_lists_newly_broken_ids(tmp_path: Path):
    """When the label keeps credit null but the text says "카드로", post-processing breaks the record."""
    golden, predictions = _pp_case(tmp_path, utterance="가나상회 무 세 개 삼천 원 카드로", gold_credit=None, pred_credit=None)

    result = rescore(golden, predictions, postprocess=True)

    assert result["postprocess"]["broken_ids"]["transaction_exact"] == ["pp"]
    assert result["postprocess"]["fixed_ids"]["transaction_exact"] == []


def test_item_name_payment_word_no_longer_breaks_a_record(tmp_path: Path):
    """pp-1.1 regression: "카드지갑" is an item name, so a correct null credit stays null."""
    golden, predictions = _pp_case(tmp_path, utterance="가나상회 카드지갑 세 개 삼천 원", gold_credit=None, pred_credit=None)

    result = rescore(golden, predictions, postprocess=True)

    assert result["postprocess"]["broken_ids"]["transaction_exact"] == []


def test_postprocess_rescoring_refuses_a_sent_text_hash_mismatch(tmp_path: Path):
    """If the rebuilt LLM input differs from what was sent, the offline result is not reproducible."""
    golden, predictions = _pp_case(tmp_path, utterance="가나상회 무 세 개 삼천 원 달아놔", gold_credit=True,
                                   pred_credit=None, sent_text="다른 문장")
    with pytest.raises(ValueError, match="sent_text_sha256"):
        rescore(golden, predictions, postprocess=True)


def test_normalization_off_run_gets_credit_rules_only(tmp_path: Path):
    """PM #35: offline scoring follows the live condition. PM 10/5: credit rules are word-based, so an
    off run still gets them (version "…-credit"); the unit-price rule stays off."""
    golden, predictions = _pp_case(tmp_path, utterance="가나상회 무 세 개 삼천 원 달아놔", gold_credit=True,
                                   pred_credit=None, normalize=False)

    result = rescore(golden, predictions, postprocess=True)

    assert result["run"]["postprocess_version"].endswith("-credit")
    assert result["postprocess"]["note"] == "정규화 off 실행 — 외상 규칙만 적용, 단가 규칙 미적용"
    assert result["postprocess"]["fixed_ids"]["transaction_exact"] == ["pp"]
    assert result["postprocess"]["applied_counts"] == {"credit_from_text": 1}


@pytest.mark.parametrize(("llm_postprocess", "applied"), [(True, None), (False, True), (None, True)])
def test_postprocess_refuses_runs_already_postprocessed(tmp_path: Path, llm_postprocess, applied):
    """PM #37 리뷰: a run saved with llm_postprocess=true is refused; false or a missing field is scored."""
    golden, predictions = _pp_case(tmp_path, utterance="가나상회 무 세 개 삼천 원 달아놔", gold_credit=True,
                                   pred_credit=None, llm_postprocess=llm_postprocess)
    if applied is None:
        with pytest.raises(ValueError, match="이미 후처리된 출력"):
            rescore(golden, predictions, postprocess=True)
        return
    result = rescore(golden, predictions, postprocess=True)
    assert result["postprocess"]["fixed_ids"]["transaction_exact"] == ["pp"]
