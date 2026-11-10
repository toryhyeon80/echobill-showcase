"""steps_frame(측정 틀 1단계) 고정 테스트 (PM 10/23 #196 요청).

- 가짜 저장 답(가짜 모델)을 넣으면 run_baseline과 점수가 같은지 (10/22 수동 대조를 자동으로 고정)
- --replay-predictions(저장된 답 재채점)가 같은 점수를 내고, 다른 문장이면 막는지
- 기준선 대조(diff_against_baseline)가 같음/다름을 제대로 가르는지
API 호출 없음(0원), git 상태는 깨끗한 것으로 고정.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import eval.run_baseline as runner
from app.config import Settings
from eval import steps_frame as sf
from eval.run_baseline import run_baseline

COMPARED_KEYS = ("transaction_exact", "amount_exact", "required_fields_micro")


@pytest.fixture(autouse=True)
def _clean_git_tree(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner, "_git_state", lambda: {"git_commit": "abc1234", "git_dirty": False})


def _record(record_id: str, text: str, expected: dict) -> dict:
    return {
        "id": record_id, "utterance_text": text, "expected_json": expected,
        "expected_outcome": "정상 구조화 가능", "confirm_type": None, "required_clarifications": [],
        "difficulty_label": "명확한 표현", "review_status": "검수 완료", "notes": {"total_source": "stated"},
        "version": "v2.1", "split": "개발용 Golden",
    }


OK_EXPECTED = {
    "counterparty": "가나상회",
    "items": [{"name": "무", "qty": 3, "unit": "개", "unit_price": None, "amount": 3000}],
    "total_amount": 3000,
    "credit": False,
}
FAIL_EXPECTED = {
    "counterparty": "실패상회",
    "items": [{"name": "배추", "qty": 1, "unit": None, "unit_price": None, "amount": 1000}],
    "total_amount": 1000,
    "credit": True,
}
OK_TEXT = "가나상회에 무 3개 현금 3000원"  # 숫자가 이미 숫자 표기라 후처리가 건드리지 않는다
FAIL_TEXT = "실패 입력"


def _write_golden(tmp_path: Path) -> Path:
    records = [_record("ok", OK_TEXT, OK_EXPECTED), _record("bad", FAIL_TEXT, FAIL_EXPECTED)]
    path = tmp_path / "golden.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
    return path


def _fake_parser(utterance: str, **_kwargs):
    """'ok'는 정답 그대로, 'bad'는 호출 실패. run_baseline과 steps_frame이 같은 가짜를 쓴다."""
    if utterance == FAIL_TEXT:
        raise RuntimeError("timeout")
    return SimpleNamespace(
        candidate=SimpleNamespace(model_dump=lambda mode=None: OK_EXPECTED),
        model="gpt-4o-mini", processing_time_ms=120, prompt_tokens=100, completion_tokens=20,
        response_model="gpt-4o-mini-2024-07-18", system_fingerprint="fp_test",
    )


def _frame_settings() -> Settings:
    return Settings(_env_file=None, app_env="development", llm_model="gpt-4o-mini", ko_numeral_normalize=False)


def _run_frame(tmp_path: Path, golden: Path, name: str, parser) -> dict:
    return sf.run_frame(golden_path=golden, output_dir=tmp_path / name, settings=_frame_settings(),
                        parser=parser, run_id=name, check_cost=False)


def _baseline_summary(tmp_path: Path, golden: Path) -> dict:
    return run_baseline(
        golden_path=golden, output_dir=tmp_path / "baseline", parser=_fake_parser, run_id="baseline",
        settings=SimpleNamespace(llm_model="gpt-4o-mini", llm_max_retries=0, llm_timeout_seconds=5.0),
    )


def test_frame_score_matches_run_baseline_for_the_same_fake_answers(tmp_path: Path):
    golden = _write_golden(tmp_path)
    baseline = _baseline_summary(tmp_path, golden)
    frame = _run_frame(tmp_path, golden, "frame", _fake_parser)

    final = frame["metrics_by_step"]["final"]
    keys = [k for k in COMPARED_KEYS if k in final and k in baseline["metrics"]]
    assert "transaction_exact" in keys
    assert sf.diff_against_baseline({k: final[k] for k in keys}, {k: baseline["metrics"][k] for k in keys}) == []
    # 실패한 문장도 분모에 남는다 (2문장 중 1개 성공)
    assert final["transaction_exact"]["total"] == 2
    assert final["transaction_exact"]["correct"] == 1
    assert frame["time"]["total"] == 2
    assert frame["run"]["steps"] == ["normalize", "llm", "postprocess", "validate"]


def test_replay_of_saved_answers_gives_the_same_score_without_calling_a_model(tmp_path: Path):
    golden = _write_golden(tmp_path)
    live = _run_frame(tmp_path, golden, "live", _fake_parser)
    saved = tmp_path / "live" / "live.predictions.jsonl"

    # parser 자리에 replay_parser만 들어가므로 어떤 모델 연결 코드도 부르지 않는다
    replayed = _run_frame(tmp_path, golden, "replay", sf.replay_parser(saved))
    assert sf.diff_against_baseline(replayed["metrics_by_step"]["final"], live["metrics_by_step"]["final"]) == []
    assert sf.diff_against_baseline(replayed["metrics_by_step"]["llm_raw"], live["metrics_by_step"]["llm_raw"]) == []
    # 저장된 실패('bad')는 실패 그대로 다시 나온다
    rows = [json.loads(line) for line in (tmp_path / "replay" / "replay.predictions.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert rows[0]["candidate"] == OK_EXPECTED
    assert rows[1]["candidate"] is None
    assert "saved failure" in rows[1]["error"]


def test_replay_refuses_a_sentence_that_is_not_in_the_saved_answers(tmp_path: Path):
    golden = _write_golden(tmp_path)
    _run_frame(tmp_path, golden, "live", _fake_parser)
    parse = sf.replay_parser(tmp_path / "live" / "live.predictions.jsonl")
    with pytest.raises(ValueError, match="저장된 답에 없는 문장"):
        parse("처음 보는 다른 문장", settings=_frame_settings())


def test_diff_against_baseline_says_same_or_lists_the_different_paths():
    base = {"transaction_exact": {"correct": 39, "total": 42, "rate": 0.9286}, "amount_exact": {"correct": 40}}
    assert sf.diff_against_baseline(base, json.loads(json.dumps(base))) == []
    changed = {"transaction_exact": {"correct": 38, "total": 42, "rate": 0.9286}, "amount_exact": {"correct": 40}}
    assert sf.diff_against_baseline(changed, base) == ["transaction_exact.correct"]
