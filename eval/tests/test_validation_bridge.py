"""추가 확인 적합성 지표: validation_bridge + score.py --attach-validation (S-9 1부)."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

from eval.score import rescore
from eval.validation_bridge import BACKEND_ROOT, validation_response_for

sys.path.insert(0, str(BACKEND_ROOT))
from app.config import Settings  # noqa: E402

GOLDEN_DEV = Path(__file__).resolve().parents[2] / "data" / "golden" / "golden_dev.jsonl"
DEV = [json.loads(line) for line in GOLDEN_DEV.read_text(encoding="utf-8").splitlines() if line.strip()]
SETTINGS = Settings(_env_file=None, app_env="development", ko_numeral_normalize=True)
_G4 = {"EXCEED_MAX_ITEMS", "MULTIPLE_COUNTERPARTIES_DETECTED"}  # answers use items=[] / counterparty=null (G4)


def test_generation_failure_has_no_status_and_stays_in_the_denominator():
    assert validation_response_for(None, "솔바람한테 무 3개 3000원 현금", SETTINGS) == {
        "status_code": None, "error_code": None, "problem_fields": [], "partial_candidate": None}


@pytest.mark.parametrize("record", [r for r in DEV if r["expected_outcome"] == "정상 구조화 가능"], ids=lambda r: r["id"])
def test_normal_golden_answer_passes(record):
    assert validation_response_for(record["expected_json"], record["utterance_text"], SETTINGS)["status_code"] == 200


@pytest.mark.parametrize("record", [r for r in DEV if r["expected_outcome"] == "추가 확인 필요"
                                    and r["expected_error_code"] not in _G4], ids=lambda r: r["id"])
def test_confirmation_golden_answer_gets_the_expected_code(record):
    response = validation_response_for(record["expected_json"], record["utterance_text"], SETTINGS)
    assert (response["status_code"], response["error_code"]) == (400, record["expected_error_code"])
    assert sorted(response["problem_fields"]) == sorted(record["required_clarifications"])


def test_two_buyers_are_counted_by_the_rule_function():
    """G4 cases are checked separately: the counterparty count comes from the production Rule function."""
    candidate = {"counterparty": "솔바람", "items": [{"name": "배추", "qty": 2, "unit": "포기", "unit_price": None,
                                                   "amount": 10000}], "total_amount": 10000, "credit": True}
    response = validation_response_for(candidate, "솔바람한테 배추 2포기 1만원 외상이고 새벽상회한테 무 3개 9천원 현금", SETTINGS)
    assert (response["status_code"], response["error_code"]) == (400, "MULTIPLE_COUNTERPARTIES_DETECTED")


def _run(tmp_path: Path, rows: list[dict], *, normalize: bool = True) -> tuple[Path, Path]:
    record = {"id": "v", "utterance_text": "가나상회 무 세 개 삼천 원", "expected_outcome": "추가 확인 필요",
              "confirm_type": "필수값 누락", "expected_error_code": "MISSING_REQUIRED_FIELD",
              "required_clarifications": ["credit"], "notes": {"total_source": "stated"},
              "expected_json": {"counterparty": "가나상회", "items": [{"name": "무", "qty": 3, "unit": "개",
                                                                    "unit_price": None, "amount": 3000}],
                                "total_amount": 3000, "credit": None}}
    golden = tmp_path / "golden.jsonl"
    golden.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    predictions = tmp_path / "run.predictions.jsonl"
    predictions.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    (tmp_path / "run.summary.json").write_text(json.dumps({"run": {"ko_numeral_normalize": normalize,
                                                                    "llm_postprocess": False}}), encoding="utf-8")
    return golden, predictions


def _candidate(credit):
    return {"counterparty": "가나상회", "items": [{"name": "무", "qty": 3, "unit": "개", "unit_price": None,
                                               "amount": 3000}], "total_amount": 3000, "credit": credit}


def test_attach_validation_scores_appropriateness_and_keeps_the_file(tmp_path: Path):
    golden, predictions = _run(tmp_path, [{"id": "v", "candidate": _candidate(None)}])
    before = predictions.read_bytes()

    plain = rescore(golden, predictions)
    attached = rescore(golden, predictions, attach_validation=True)

    assert predictions.read_bytes() == before
    assert plain["metrics"]["confirmation_appropriateness"]["status"] == "not_available"
    assert attached["metrics"]["confirmation_appropriateness"]["correct"] == 1
    assert attached["run"]["validation_attached"] is True


def test_existing_validation_response_is_not_overwritten(tmp_path: Path):
    stored = {"status_code": 200, "error_code": None, "problem_fields": [], "partial_candidate": None}
    golden, predictions = _run(tmp_path, [{"id": "v", "candidate": _candidate(None), "validation_response": stored}])
    assert rescore(golden, predictions, attach_validation=True)["metrics"]["confirmation_appropriateness"]["correct"] == 0


def test_postprocess_runs_before_validation(tmp_path: Path):
    """LLM credit=false on a sentence without payment words stays 200; pp fills credit only from the text."""
    golden, predictions = _run(tmp_path, [{"id": "v", "candidate": _candidate(None),
                                           "sent_text_sha256": hashlib.sha256("가나상회 무 3개 3000원 달아놔".encode()).hexdigest()}])
    record = json.loads(golden.read_text(encoding="utf-8"))
    record["utterance_text"] = "가나상회 무 세 개 삼천 원 달아놔"
    golden.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    raw = rescore(golden, predictions, attach_validation=True)
    processed = rescore(golden, predictions, postprocess=True, attach_validation=True)

    # raw candidate: credit null → MISSING [credit] (matches); after pp credit=true → 200 (no longer a 400)
    assert raw["metrics"]["confirmation_appropriateness"]["correct"] == 1
    assert processed["metrics"]["confirmation_appropriateness"]["correct"] == 0


def _confirm_record(code: str, fields: list[str], utterance: str) -> dict:
    return {"id": "c", "utterance_text": utterance, "expected_outcome": "추가 확인 필요", "confirm_type": "입력 범위 위반",
            "expected_error_code": code, "required_clarifications": fields, "notes": {"total_source": None},
            "expected_json": {"counterparty": None, "items": [], "total_amount": None, "credit": True}}


def test_two_buyers_end_to_end_are_appropriate_under_v13():
    """S-10: Rule-counted counterparty 2 → MULTIPLE with matching fields counts as appropriate (v1.3)."""
    from eval.scoring import score_records

    text = "솔바람한테 배추 2포기 1만원 외상이고 새벽상회한테 무 3개 9천원 외상"
    record = _confirm_record("MULTIPLE_COUNTERPARTIES_DETECTED", ["counterparty"], text)
    candidate = {"counterparty": "솔바람", "items": [{"name": "배추", "qty": 2, "unit": "포기", "unit_price": None,
                                                   "amount": 10000}], "total_amount": 10000, "credit": True}
    response = validation_response_for(candidate, text, SETTINGS)
    result = score_records([record], {"c": {"candidate": candidate, "validation_response": response}})
    assert result["confirmation_appropriateness"]["correct"] == 1


def test_four_items_end_to_end_are_appropriate_under_v13():
    """S-10: a 4-item candidate → EXCEED_MAX_ITEMS with no partial candidate counts as appropriate (v1.3)."""
    from eval.scoring import score_records

    text = "솔바람한테 무 1개 배추 1포기 양파 1망 대파 1단 해서 1만원 외상"
    record = _confirm_record("EXCEED_MAX_ITEMS", ["items"], text)
    items = [{"name": n, "qty": 1, "unit": u, "unit_price": None, "amount": 2500}
             for n, u in (("무", "개"), ("배추", "포기"), ("양파", "망"), ("대파", "단"))]
    candidate = {"counterparty": "솔바람", "items": items, "total_amount": 10000, "credit": True}
    response = validation_response_for(candidate, text, SETTINGS)
    assert response["partial_candidate"] is None
    result = score_records([record], {"c": {"candidate": candidate, "validation_response": response}})
    assert result["confirmation_appropriateness"]["correct"] == 1


_THREE_ITEMS = {"counterparty": "솔바람", "items": [{"name": n, "qty": q, "unit": u, "unit_price": None, "amount": None}
                                                 for n, q, u in (("대파", 7, "근"), ("고추", 3, "근"), ("두부", 2, "통"))],
                "total_amount": 200000, "credit": True}


def test_items_dropped_by_the_llm_are_counted_from_the_utterance():
    """#120: same as /parse — the larger of the candidate's and the utterance's item count is judged."""
    response = validation_response_for(_THREE_ITEMS, "솔바람한테 대파 7근, 고추 3근, 두부 2통, 사과 3박스 20만원 외상", SETTINGS)
    assert (response["status_code"], response["error_code"]) == (400, "EXCEED_MAX_ITEMS")


def test_a_corrected_quantity_is_not_counted_twice():
    response = validation_response_for(_THREE_ITEMS, "솔바람한테 대파 7개 아니 8개, 고추 3박스, 두부 2팩 20만원 외상", SETTINGS)
    assert response["status_code"] == 200


_CREDIT_30000 = {"counterparty": "솔바람", "items": [{"name": "양파", "qty": 3, "unit": "박스", "unit_price": None,
                                                    "amount": 30000}], "total_amount": 30000, "credit": True}


def test_a_clear_partial_payment_is_unsupported():
    """PR #128: same as /parse — the transaction type is judged on the utterance by the Rule parser."""
    response = validation_response_for(_CREDIT_30000, "솔바람 양파 3박스 2만원 받고 남은 1만원 외상 합계 3만원", SETTINGS)
    assert (response["status_code"], response["error_code"]) == (400, "UNSUPPORTED_TRANSACTION")
    assert response["partial_candidate"] is None


def test_an_ambiguous_partial_payment_is_not_unsupported():
    response = validation_response_for(_CREDIT_30000, "솔바람 양파 3박스 3만원 현금 받고 나머지 외상", SETTINGS)
    assert response["status_code"] == 200  # the credit question comes from post-processing, not run here


@pytest.mark.parametrize("kind, text, status", [
    (None, "솔바람 양파 3박스 3만원 외상", 200), ("sale", "솔바람 양파 3박스 3만원 외상", 200),
    ("purchase", "솔바람 양파 3박스 3만원 외상", 200), ("return", "솔바람 양파 3박스 3만원 외상", 200),
    ("purchase", "솔바람에서 양파 3박스 3만원 외상으로 사왔어", 400), ("return", "솔바람 양파 3박스 3만원 반품", 400),
    ("partial_payment", "솔바람 양파 3박스 3만원 외상", 200)])
def test_saved_llm_transaction_type_is_combined_like_parse(kind, text, status):
    """PM 10/14: the model's 매입·반품 count with a word for it (2차); partial payment only from the Rule (#128)."""
    response = validation_response_for(_CREDIT_30000, text, SETTINGS, kind)
    assert response["status_code"] == status
    if status == 400:
        assert response["error_code"] == "UNSUPPORTED_TRANSACTION"


def test_attach_validation_passes_the_saved_transaction_type(tmp_path: Path):
    golden, predictions = _run(tmp_path, [{"id": "v", "candidate": _candidate(None), "transaction_type": "return"}])
    record = json.loads(golden.read_text(encoding="utf-8"))
    record.update(utterance_text="가나상회 무 세 개 삼천 원 반품", expected_error_code="UNSUPPORTED_TRANSACTION",
                  required_clarifications=[])
    golden.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    assert rescore(golden, predictions, attach_validation=True)["metrics"]["confirmation_appropriateness"]["correct"] == 1
