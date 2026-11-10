import json
from pathlib import Path

import pytest

from eval.score_rule import score_rule


def _record(record_id: str, utterance: str, expected: dict, *, outcome: str = "정상 구조화 가능",
            split: str = "개발용 Golden") -> dict:
    return {"id": record_id, "utterance_text": utterance, "expected_json": expected, "expected_outcome": outcome,
            "split": split, "notes": {"total_source": "stated"}}


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return path


def test_scores_rule_output_and_counts_400_on_normal_records(tmp_path: Path):
    """One sentence the Rule path reads fully, one it cannot (no counterparty marker → 400)."""
    ok = {"counterparty": "솔바람", "items": [{"name": "무", "qty": 3, "unit": "개", "unit_price": None, "amount": 3000}],
          "total_amount": 3000, "credit": False}
    golden = _write(tmp_path / "g.jsonl", [
        _record("a", "솔바람한테 무 3개 3000원 현금", ok),
        _record("b", "솔바람 무 3개 3000원 현금", ok),
    ])

    result = score_rule(golden, ko_normalize=True)

    assert result["metrics"]["transaction_exact"] == {"correct": 1, "total": 2, "rate": 0.5}
    assert result["metrics"]["normal_rejected_400"] == {"count": 1, "total": 2}
    assert result["rejected_400"]["b"]["error_code"] == "MISSING_REQUIRED_FIELD"
    assert result["wrong_transaction_ids"] == ["b"]


@pytest.mark.parametrize("split", ["Seed", "최종 Golden"])
def test_refuses_anything_but_dev_golden_or_seed_v2(tmp_path: Path, split: str):
    """Seed v1 (ids without the seed2- prefix) is measurement-only (DL-26); final Golden is never scored here."""
    golden = _write(tmp_path / "g.jsonl", [_record("s", "솔바람한테 무 3개 3000원 현금", {}, split=split)])
    with pytest.raises(ValueError, match="accepts only split"):
        score_rule(golden, ko_normalize=True)


def _confirm(record_id: str, utterance: str, code: str, fields: list[str], expected: dict) -> dict:
    record = _record(record_id, utterance, expected, outcome="추가 확인 필요")
    record.update(expected_error_code=code, required_clarifications=fields, confirm_type="필수값 누락")
    return record


def test_rule_track_scores_an_appropriate_confirmation(tmp_path: Path):
    """S-13: no payment word → Rule leaves credit null → 400 MISSING ["credit"] = appropriate (v1.3)."""
    expected = {"counterparty": "솔바람", "items": [{"name": "무", "qty": 3, "unit": "개", "unit_price": None,
                                                 "amount": 3000}], "total_amount": 3000, "credit": None}
    golden = _write(tmp_path / "g.jsonl", [_confirm("c", "솔바람한테 무 3개 3000원", "MISSING_REQUIRED_FIELD",
                                                    ["credit"], expected)])

    result = score_rule(golden, ko_normalize=True)

    assert result["metrics"]["confirmation_appropriateness"]["correct"] == 1
    assert result["metrics"]["confirmation_appropriateness"]["total"] == 1


def test_rule_track_counts_a_wrong_400_as_inappropriate(tmp_path: Path):
    """The Rule 400 asks for the counterparty (no marker) instead of the expected credit → not appropriate."""
    expected = {"counterparty": "솔바람", "items": [{"name": "무", "qty": 3, "unit": "개", "unit_price": None,
                                                 "amount": 3000}], "total_amount": 3000, "credit": None}
    golden = _write(tmp_path / "g.jsonl", [_confirm("c", "솔바람 무 3개 3000원", "MISSING_REQUIRED_FIELD",
                                                    ["credit"], expected)])

    result = score_rule(golden, ko_normalize=True)

    assert result["metrics"]["confirmation_appropriateness"]["correct"] == 0


def test_scores_seed_v2_and_omits_candidates_by_default(tmp_path: Path):
    """Seed v2 (split Seed, ids seed2-*) is scored; sentence-derived candidates stay out of the result."""
    ok = {"counterparty": "솔바람", "items": [{"name": "무", "qty": 3, "unit": "개", "unit_price": None, "amount": 3000}],
          "total_amount": 3000, "credit": False}
    golden = _write(tmp_path / "g.jsonl", [_record("seed2-001", "솔바람한테 무 3개 3000원 현금", ok, split="Seed")])

    result = score_rule(golden, ko_normalize=True, include_candidates=False)

    assert result["run"]["split"] == "Seed"
    assert result["metrics"]["transaction_exact"]["correct"] == 1
    assert "candidates" not in result
    assert "candidates" in score_rule(golden, ko_normalize=True)


def test_refuses_mixed_dev_and_seed_v2_in_one_file(tmp_path: Path):
    """Dev and Seed v2 numbers are never blended into one score."""
    golden = _write(tmp_path / "g.jsonl", [
        _record("a", "솔바람한테 무 3개 3000원 현금", {}),
        _record("seed2-001", "솔바람한테 무 3개 3000원 현금", {}, split="Seed"),
    ])
    with pytest.raises(ValueError, match="mixed splits"):
        score_rule(golden, ko_normalize=True)

