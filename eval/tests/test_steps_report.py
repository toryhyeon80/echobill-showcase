"""steps_report(측정 틀 2단계, 저장된 출력 재생) 고정 테스트 (PM 10/30).

- 데이터 가드: Seed v1·최종 Golden Test·실행 때와 다른 데이터·이미 후처리된 출력은 거부
- 세 지점(A 원 출력·B +후처리·C +검증) 점수가 score.py(무옵션·--postprocess·--postprocess --attach-validation)와 같은지
- 시간 집계(문장별 중앙값 → p50·p95, 합계, 5초 초과)와 규칙별 효과
API 호출 없음(0원).
"""

import hashlib
import json
from pathlib import Path

import pytest

from eval import steps_frame as sf
from eval import steps_report as sr
from eval.score import rescore
from eval.scoring import score_records

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_GOLDEN = REPO_ROOT / "data" / "golden" / "golden_dev.jsonl"
DEV_R1 = REPO_ROOT / "eval" / "results" / "gpt-4o-mini-20261001-193115-kst-p0-on-r1.predictions.jsonl"
SCORE_KEYS = ("transaction_exact", "amount_exact", "required_fields_micro")

NORMAL = {"expected_outcome": "정상 구조화 가능", "confirm_type": None, "required_clarifications": [],
          "expected_error_code": None}
ASK_CREDIT = {"expected_outcome": "추가 확인 필요", "confirm_type": "필수값 누락",
              "required_clarifications": ["credit"], "expected_error_code": "MISSING_REQUIRED_FIELD"}


def _record(record_id: str, text: str, expected: dict, kind: dict, split: str = "개발용 Golden") -> dict:
    return {"id": record_id, "utterance_text": text, "expected_json": expected, **kind,
            "difficulty_label": "명확한 표현", "review_status": "검수 완료", "notes": {"total_source": "stated"},
            "version": "v2.1", "split": split}


def _item(name: str, qty: int, unit: str, amount: int, unit_price=None) -> dict:
    return {"name": name, "qty": qty, "unit": unit, "unit_price": unit_price, "amount": amount}


OK = ("ok", "가나상회에 무 3개 현금 3000원",
      {"counterparty": "가나상회", "items": [_item("무", 3, "개", 3000)], "total_amount": 3000, "credit": False})
# 외상·현금 말이 없는데 AI가 외상으로 채움 → 후처리가 지우고(credit_cleared_no_word) 검증이 되묻는다
NO_CREDIT_WORD = ("ask", "다나상회 배추 2개 4000원",
                  {"counterparty": "다나상회", "items": [_item("배추", 2, "개", 4000)], "total_amount": 4000,
                   "credit": None})
# AI가 외상을 비우고 단가로 읽음 → 후처리가 외상을 채우고(credit_from_text) 품목 금액으로 옮긴다
UNIT_PRICE = ("unit", "라라상회에 사과 1박스 20000원 외상",
              {"counterparty": "라라상회", "items": [_item("사과", 1, "박스", 20000)], "total_amount": 20000,
               "credit": True})
FAILED = ("fail", "마마상회 양파 5개 5000원 현금",
          {"counterparty": "마마상회", "items": [_item("양파", 5, "개", 5000)], "total_amount": 5000, "credit": False})


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_run(tmp_path: Path, records: list[dict], rows: list[dict], run_extra: dict | None = None) -> tuple[Path, Path]:
    golden = tmp_path / "golden.jsonl"
    golden.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
    predictions = tmp_path / "run-r1.predictions.jsonl"
    predictions.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    run = {"id": "run-r1", "executed_at": "2026-10-01T19:33:09+09:00", "model": "gpt-4o-mini",
           "ko_numeral_normalize": True, "llm_postprocess": False,
           "dataset_sha256": hashlib.sha256(golden.read_bytes()).hexdigest(), **(run_extra or {})}
    (tmp_path / "run-r1.summary.json").write_text(json.dumps({"run": run}, ensure_ascii=False), encoding="utf-8")
    return golden, predictions


def _fixture_run(tmp_path: Path) -> tuple[Path, Path]:
    records = [_record(*OK, NORMAL), _record(*NO_CREDIT_WORD, ASK_CREDIT), _record(*UNIT_PRICE, NORMAL),
               _record(*FAILED, NORMAL)]
    ai_answers = {
        "ok": OK[2],
        "ask": {**NO_CREDIT_WORD[2], "credit": True},
        "unit": {**UNIT_PRICE[2], "credit": None, "items": [_item("사과", 1, "박스", None, unit_price=20000)]},
        "fail": None,
    }
    rows = [{"id": r["id"], "candidate": ai_answers[r["id"]], "error": None if ai_answers[r["id"]] else "timeout",
             "sent_text_sha256": _sha(r["utterance_text"]), "processing_time_ms": 1000 + i,
             "wall_time_ms": 1500 + i * 2000} for i, r in enumerate(records)]
    return _write_run(tmp_path, records, rows)


# ── 데이터 가드 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("record", [
    _record("seed-001", OK[1], OK[2], NORMAL, split="Seed"),  # Seed v1 (seed2-가 아닌 Seed)
    _record("g-test-001", OK[1], OK[2], NORMAL, split="최종 Golden"),  # 최종 Golden Test
])
def test_refuses_seed_v1_and_final_golden(tmp_path: Path, record: dict):
    golden, predictions = _write_run(tmp_path, [record], [{"id": record["id"], "candidate": OK[2]}])
    with pytest.raises(ValueError):
        sr.load_run(golden, predictions)


def test_accepts_seed_v2_rows(tmp_path: Path):
    record = _record("seed2-001", OK[1], OK[2], NORMAL, split="Seed")
    golden, predictions = _write_run(tmp_path, [record], [{"id": "seed2-001", "candidate": OK[2]}])
    assert sr.load_run(golden, predictions)[3] == "Seed"


def test_refuses_other_data_file_and_postprocessed_output(tmp_path: Path):
    golden, predictions = _fixture_run(tmp_path)
    summary = tmp_path / "run-r1.summary.json"
    run = json.loads(summary.read_text(encoding="utf-8"))["run"]
    summary.write_text(json.dumps({"run": {**run, "dataset_sha256": "0" * 64}}), encoding="utf-8")
    with pytest.raises(ValueError, match="dataset_sha256"):
        sr.load_run(golden, predictions)
    summary.write_text(json.dumps({"run": {**run, "llm_postprocess": True}}), encoding="utf-8")
    with pytest.raises(ValueError, match="이미 후처리된 출력"):
        sr.load_run(golden, predictions)


@pytest.mark.parametrize("recorded", ["crlf", "lf"])
def test_same_data_passes_with_crlf_or_lf_checkout_but_other_content_is_refused(tmp_path: Path, recorded: str):
    """Windows 측정(CRLF 해시)을 CI(LF 체크아웃)에서 재생해도, 그 반대도 같은 데이터로 본다 (PR #243 CI)."""
    golden, predictions = _fixture_run(tmp_path)
    lf = golden.read_bytes().replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    summary = tmp_path / "run-r1.summary.json"
    run = json.loads(summary.read_text(encoding="utf-8"))["run"]
    recorded_bytes = crlf if recorded == "crlf" else lf
    summary.write_text(json.dumps({"run": {**run, "dataset_sha256": hashlib.sha256(recorded_bytes).hexdigest()}}),
                       encoding="utf-8")
    for checkout in (crlf, lf):
        golden.write_bytes(checkout)
        assert sr.load_run(golden, predictions)[3] == "개발용 Golden"
    golden.write_bytes(lf.replace("가나상회".encode(), "가나상점".encode()))  # 내용이 다른 파일
    with pytest.raises(ValueError, match="dataset_sha256"):
        sr.load_run(golden, predictions)


# ── 세 지점 = score.py ──────────────────────────────────────────────────
def _assert_points_match_score_py(golden: Path, predictions: Path) -> dict:
    records, rows, run, _ = sr.load_run(golden, predictions)
    inputs = sr.point_rows(sr.replay(records, rows, run, repeats=1))
    plain = rescore(golden, predictions)["metrics"]
    pp = rescore(golden, predictions, postprocess=True)["metrics"]
    full = rescore(golden, predictions, postprocess=True, attach_validation=True)["metrics"]
    a, b, c = (score_records(records, inputs[name]) for name in sr.POINTS)
    assert sf.diff_against_baseline({k: a[k] for k in SCORE_KEYS}, {k: plain[k] for k in SCORE_KEYS}) == []
    assert sf.diff_against_baseline({k: b[k] for k in SCORE_KEYS}, {k: pp[k] for k in SCORE_KEYS}) == []
    # C는 채점 결과 전체(문장별 판정·되묻기·외상 잘못 확정 포함)가 같다
    assert sf.diff_against_baseline(c, full) == []
    return {"a": a, "b": b, "c": c}


def test_three_points_match_score_py_on_fixture(tmp_path: Path):
    golden, predictions = _fixture_run(tmp_path)
    _assert_points_match_score_py(golden, predictions)
    result = sr.analyze_run(golden, predictions, repeats=2)
    a, b, c = (result["points"][name] for name in sr.POINTS)
    # 원 출력: 외상 말 없는 문장을 외상으로 저장(외상 잘못 확정), 외상을 비운 문장은 거래 틀림
    assert a["credit_wrongly_confirmed"]["count"] == 1
    assert (a["transaction_exact"]["correct"], b["transaction_exact"]["correct"]) == (1, 2)
    assert a["wrong_saved_normal"] == {"count": 1, "ids": ["unit"]}
    assert a["saved_without_asking"] == {"count": 1, "ids": ["ask"]}
    assert b["credit_wrongly_confirmed"]["count"] == 0
    assert b["wrong_saved_normal"]["count"] == 0 and b["saved_without_asking"]["ids"] == ["ask"]
    # 검증: 지워진 외상을 되묻고(400), 저장된 틀린 값은 없다. 생성 실패는 분모에 남는다
    assert c["asked_400"] == {"count": 1, "ids": ["ask"]}
    assert c["wrong_saved_normal"]["count"] == 0 and c["saved_without_asking"]["count"] == 0
    assert c["transaction_exact"] == {"correct": 2, "total": 3, "rate": round(2 / 3, 6)}
    # 한 문장에 붙은 두 규칙에 같은 변화를 센다
    assert result["rule_effects"]["credit_from_text"]["wrong_to_right"] == ["unit"]
    assert result["rule_effects"]["unit_price_to_amount"]["wrong_to_right"] == ["unit"]
    assert result["rule_effects"]["credit_cleared_no_word"]["confirmation_sentences"] == ["ask"]
    assert result["sent_text_mismatch_ids"] == []
    # 따로 볼 문장: 이 fixture에는 (C)까지 남은 외상 잘못 확정·맞음→틀림이 없다
    assert result["cases"] == {}


def test_case_facts_list_rule_and_changed_numbers_without_names(tmp_path: Path):
    """외상 말이 부정으로만 나오고 AI가 false → 후처리가 남기고 검증 200 → (C)까지 외상 잘못 확정 (seed2-061 유형)."""
    record = _record("neg", "바바상회 무 1개 1000원 외상 안 해요",
                     {"counterparty": "바바상회", "items": [_item("무", 1, "개", 1000)], "total_amount": 1000,
                      "credit": None}, ASK_CREDIT)
    answer = {**record["expected_json"], "credit": False}
    golden, predictions = _write_run(tmp_path, [record], [
        {"id": "neg", "candidate": answer, "error": None, "sent_text_sha256": _sha(record["utterance_text"]),
         "processing_time_ms": 900, "wall_time_ms": 1000}])
    result = sr.analyze_run(golden, predictions, repeats=1)
    fact = result["cases"]["neg"]
    assert fact["reasons"] == ["(C)까지 외상 잘못 확정"]
    assert fact["ai_credit"] is False and fact["applied"] == [] and fact["changes"] == []
    assert fact["validation"]["status_code"] == 200
    assert sr._value_changes({"total_amount": 13100, "credit": True,
                              "items": [{"name": "우유", "qty": 2, "unit_price": 5800, "amount": 11600}]},
                             {"total_amount": None, "credit": True,
                              "items": [{"name": "우유", "qty": 2, "unit_price": None, "amount": None}]}) == [
        "total_amount: 13100 → None", "items[0].unit_price: 5800 → None", "items[0].amount: 11600 → None"]


@pytest.mark.skipif(not DEV_R1.exists(), reason="Dev 저장 출력 없음")
def test_three_points_match_score_py_on_saved_dev_run():
    _assert_points_match_score_py(DEV_GOLDEN, DEV_R1)


def test_changed_normalization_is_marked_and_also_scored_without_it(tmp_path: Path):
    golden, predictions = _fixture_run(tmp_path)
    rows = [json.loads(line) for line in predictions.read_text(encoding="utf-8").splitlines()]
    rows[0]["sent_text_sha256"] = "0" * 64  # 'ok'를 AI가 다른 글자로 받은 것으로
    predictions.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    result = sr.analyze_run(golden, predictions, repeats=1)
    assert result["sent_text_mismatch_ids"] == ["ok"]
    assert result["points_excluding_mismatch"]["C_validated"]["transaction_exact"]["total"] == 2


def test_outputs_carry_ids_but_not_sentences(tmp_path: Path):
    golden, predictions = _fixture_run(tmp_path)
    result = sr.analyze_run(golden, predictions, repeats=1)
    summary = {"generated_at": "t", "git_commit": "abc", "git_dirty": False, "scoring_version": "v", "repeats": 1,
               "normalizer_version": "n", "machine": "m", "ko_numeral_reference": sr.KO_NUMERAL_REFERENCE,
               "datasets": {"fixture": [result]}}
    text = sr.render_report(summary) + json.dumps(sr._strip_private(summary), ensure_ascii=False)
    for record_text in (OK[1], NO_CREDIT_WORD[1], UNIT_PRICE[1], FAILED[1]):
        assert record_text not in text
    assert "ask" in text and "_records" not in text


# ── 시간 집계 ───────────────────────────────────────────────────────────
def test_timing_uses_per_sentence_median_and_saved_ai_time():
    replayed = {
        "s1": {"step_ms": {"normalize": [1.0, 9.0, 2.0], "postprocess": [1.0, 1.0, 1.0], "validate": [2.0, 2.0, 2.0]}},
        "s2": {"step_ms": {"normalize": [3.0, 3.0, 3.0], "postprocess": [2.0, 2.0, 2.0], "validate": [1.0, 1.0, 1.0]}},
        "s3": {"step_ms": {"normalize": [1.0, 1.0, 1.0], "postprocess": [1.0, 1.0, 1.0], "validate": [1.0, 1.0, 1.0]}},
    }
    rows = {"s1": {"processing_time_ms": 900, "wall_time_ms": 1000},
            "s2": {"processing_time_ms": 4900, "wall_time_ms": 5000, "error": None},
            "s3": {"processing_time_ms": None, "wall_time_ms": None}}  # 생성 실패: AI 시간 없음
    t = sr.timing(replayed, rows)
    assert t["local_steps_ms"]["normalize"] == {"p50": 2.0, "p95": 3.0, "max": 3.0, "n": 3}  # s1 중앙값 2 (튀는 9 무시)
    assert t["ai_wall_time_ms"] == {"p50": 1000, "p95": 5000, "max": 5000, "n": 2}
    # 합계 = 정규화+AI wall+후처리+검증: s1 1005, s2 5006 (> 5초), s3은 AI 시간이 없어 빠진다
    assert t["total_ms"] == {"p50": 1005.0, "p95": 5006.0, "max": 5006.0, "n": 2}
    assert t["over_deadline"] == {"count": 1, "total": 3, "ids": ["s2"], "detail": [
        {"id": "s2", "processing_time_ms": 4900, "wall_time_ms": 5000, "error_type": None}]}
    assert t["ratio_of_deadline"]["p95"] == round(5006 / 5000, 4)
