import json
from pathlib import Path

import pytest

from eval import regression
from eval.regression import RegressionError, run

BASE_IDS = {"transaction_exact": ["a", "b"], "amount_exact": ["a", "b", "c"],
            "confirmation_appropriate": ["x"], "credit_wrongly_confirmed": []}


def _metrics(ids: dict) -> dict:
    """Fake score_records output whose four ID sets are `ids`."""
    every = sorted({i for key, value in ids.items() if key != "credit_wrongly_confirmed" for i in value} | {"z"})
    records = [{"id": i, **{key: i in ids[key] for key in regression.CORRECT_METRICS}} for i in every]
    return {"records": records, "credit_wrongly_confirmed": {"count": len(ids["credit_wrongly_confirmed"]),
                                                             "ids": ids["credit_wrongly_confirmed"]},
            "transaction_exact": {"total": 42}, "amount_exact": {"overall": {"total": 56}},
            "confirmation_appropriateness": {"total": 18}}


def _golden(tmp_path: Path, split: str = "개발용 Golden") -> Path:
    path = tmp_path / "golden.jsonl"
    path.write_text(json.dumps({"id": "a", "split": split, "utterance_text": "무 세 개"}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def _setup(tmp_path: Path, *, model: str = "gpt-4o-mini") -> dict:
    """Fake golden, prompt and a baseline written from BASE_IDS for rule + one LLM run."""
    golden = _golden(tmp_path)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("시스템 프롬프트\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    env = regression.environment(golden, prompt)
    tracks = {name: regression._summary(_metrics(BASE_IDS), BASE_IDS) for name in ("rule", "llm-r1")}
    regression.write_baseline(baseline, {**env, "llm_model": model}, tracks)
    return {"golden_path": golden, "prompt_path": prompt, "baseline_path": baseline,
            "llm_runs": {"llm-r1": tmp_path / "r1.predictions.jsonl"}}


def _run(paths: dict, *, rule_ids=BASE_IDS, llm_ids=BASE_IDS, llm=None, update=False) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = run(update, **paths, rule=lambda _g: _metrics(rule_ids),
               llm=llm or (lambda _g, _p: _metrics(llm_ids)), out=lines.append)
    return code, lines


def test_same_as_baseline_passes(tmp_path: Path):
    code, lines = _run(_setup(tmp_path))
    assert code == 0
    assert all("통과" in line for line in lines) and not any("::warning::" in line for line in lines)


@pytest.mark.parametrize("metric", regression.CORRECT_METRICS)
def test_a_baseline_correct_id_now_wrong_fails(tmp_path: Path, metric: str):
    ids = {**BASE_IDS, metric: BASE_IDS[metric][1:]}
    code, lines = _run(_setup(tmp_path), llm_ids=ids)
    assert code == 1
    assert any(f"{regression.LABELS[metric]} 새로 틀림: ['{BASE_IDS[metric][0]}']" in line for line in lines)


def test_a_new_credit_wrongly_confirmed_id_fails(tmp_path: Path):
    code, lines = _run(_setup(tmp_path), rule_ids={**BASE_IDS, "credit_wrongly_confirmed": ["y"]})
    assert code == 1
    assert any("외상 잘못 확정 새로 생김: ['y']" in line for line in lines)


def test_a_newly_correct_id_passes_with_a_warning(tmp_path: Path):
    code, lines = _run(_setup(tmp_path), rule_ids={**BASE_IDS, "transaction_exact": ["a", "b", "c"]})
    assert code == 0
    assert any(line.startswith("::warning::[rule] 새로 맞힘: ['c']") and "--update-baseline" in line
               for line in lines)


def test_sent_text_mismatch_fails_llm_as_unrescorable_but_rule_is_still_reported(tmp_path: Path):
    def mismatch(_g, _p):
        raise RegressionError(f"{regression.UNRESCORABLE}: 정규화 변경 (sent_text_sha256 mismatch for a)")

    code, lines = _run(_setup(tmp_path), llm=mismatch)
    assert code == 1
    assert any(line.startswith("[llm-r1] 실패 — 재채점 불가") for line in lines)
    assert any(line.startswith("[rule] 거래 2/42") and line.endswith("통과") for line in lines)


def test_llm_metrics_turns_a_sent_text_mismatch_into_unrescorable(tmp_path: Path, monkeypatch):
    def rescore(*_args, **_kwargs):
        raise ValueError("sent_text_sha256 mismatch for a: rebuilt LLM input differs from the run")

    monkeypatch.setattr(regression, "rescore", rescore)
    with pytest.raises(RegressionError, match="재채점 불가 — 새 측정 필요"):
        regression.llm_metrics(_golden(tmp_path), tmp_path / "r1.predictions.jsonl")


def test_changed_prompt_fails_llm_as_unrescorable(tmp_path: Path):
    paths = _setup(tmp_path)
    paths["prompt_path"].write_text("바뀐 프롬프트\n", encoding="utf-8")
    code, lines = _run(paths, llm=lambda _g, _p: pytest.fail("LLM must not be rescored"))
    assert code == 1
    assert any(line.startswith("[llm-r1] 실패 — 재채점 불가") and "기본 프롬프트 변경" in line for line in lines)
    assert any(line.startswith("[rule]") and line.endswith("통과") for line in lines)


def test_changed_model_fails_llm_as_unrescorable(tmp_path: Path):
    code, lines = _run(_setup(tmp_path, model="gpt-old"), llm=lambda _g, _p: pytest.fail("not rescored"))
    assert code == 1
    assert any("기본 모델 변경" in line for line in lines)
    assert any(line.startswith("[rule]") and line.endswith("통과") for line in lines)


def test_update_baseline_refuses_to_write_when_an_id_broke(tmp_path: Path):
    paths = _setup(tmp_path)
    before = paths["baseline_path"].read_text(encoding="utf-8")
    code, lines = _run(paths, rule_ids={**BASE_IDS, "transaction_exact": ["a"]}, update=True)
    assert code == 1
    assert paths["baseline_path"].read_text(encoding="utf-8") == before
    assert any("기준선을 쓰지 않음" in line for line in lines)


def test_update_baseline_writes_sorted_ids_one_per_line(tmp_path: Path):
    paths = _setup(tmp_path)
    code, _ = _run(paths, rule_ids={**BASE_IDS, "transaction_exact": ["c", "a", "b"]}, update=True)
    assert code == 0
    text = paths["baseline_path"].read_text(encoding="utf-8")
    assert json.loads(text)["tracks"]["rule"]["ids"]["transaction_exact"] == ["a", "b", "c"]
    assert '\n          "c"\n' in text



def _flip(cash_to_credit: list[str], credit_to_cash: list[str]) -> dict:
    """Fake score_records 외상 반대 저장 block."""
    return {"cash_to_credit": len(cash_to_credit), "credit_to_cash": len(credit_to_cash),
            "ids": {"cash_to_credit": cash_to_credit, "credit_to_cash": credit_to_cash}}


def _run_flip(paths: dict, rule_flip: dict, *, update: bool = False) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = run(update, **paths, rule=lambda _g: {**_metrics(BASE_IDS), "credit_flip_saved": rule_flip},
               llm=lambda _g, _p: _metrics(BASE_IDS), out=lines.append)
    return code, lines


@pytest.mark.parametrize("direction,label", [("cash_to_credit", "현금→외상"), ("credit_to_cash", "외상→현금")])
def test_a_new_credit_flip_id_fails_in_either_direction(tmp_path: Path, direction: str, label: str):
    """PM 10/8: 외상 반대 저장도 외상 잘못 확정처럼 기준선에 없던 ID가 생기면 실패."""
    paths = _setup(tmp_path)
    before = paths["baseline_path"].read_text(encoding="utf-8")
    flip = _flip(**{"cash_to_credit": [], "credit_to_cash": [], direction: ["y"]})
    code, lines = _run_flip(paths, flip)
    assert code == 1
    assert any(f"외상 반대 저장({label}) 새로 생김: ['y']" in line for line in lines)
    assert any(line.startswith("[rule]") and f"외상 반대 저장 현금→외상 {int(direction == 'cash_to_credit')}" in line
               and line.endswith("실패") for line in lines)
    code, lines = _run_flip(paths, flip, update=True)
    assert code == 1 and any("기준선을 쓰지 않음" in line for line in lines)
    assert paths["baseline_path"].read_text(encoding="utf-8") == before


def test_credit_flip_ids_are_written_and_a_gone_id_passes_with_a_warning(tmp_path: Path):
    paths = _setup(tmp_path)
    flipped = {**_metrics(BASE_IDS), "credit_flip_saved": _flip(["b", "a"], ["c"])}
    regression.write_baseline(paths["baseline_path"], regression.environment(paths["golden_path"], paths["prompt_path"]),
                              {"rule": regression._summary(flipped, BASE_IDS),
                               "llm-r1": regression._summary(_metrics(BASE_IDS), BASE_IDS)})
    track = json.loads(paths["baseline_path"].read_text(encoding="utf-8"))["tracks"]["rule"]
    assert track["credit_flip_saved"] == {"cash_to_credit": ["a", "b"], "credit_to_cash": ["c"]}
    code, lines = _run_flip(paths, _flip(["a", "b"], []))
    assert code == 0
    assert any(line.startswith("[rule]") and line.endswith("통과") for line in lines)
    assert any(line.startswith("::warning::[rule] 새로 맞힘: ['c']") and "--update-baseline" in line
               for line in lines)


def test_an_old_baseline_without_credit_flip_is_read_as_empty(tmp_path: Path):
    paths = _setup(tmp_path)
    assert "credit_flip_saved" not in paths["baseline_path"].read_text(encoding="utf-8")
    code, lines = _run_flip(paths, _flip([], []))
    assert code == 0 and not any("::warning::" in line for line in lines)
    code, lines = _run_flip(paths, _flip(["a"], []))
    assert code == 1 and any("외상 반대 저장(현금→외상) 새로 생김: ['a']" in line for line in lines)


def test_other_split_fails(tmp_path: Path):
    paths = _setup(tmp_path)
    _golden(tmp_path, split="측정 전용 Seed")
    lines: list[str] = []
    code = run(**paths, out=lines.append)
    assert code == 1
    assert any(line.startswith("[rule] 실패 — ") and "split" in line for line in lines)
    assert any(line.startswith("[llm-r1] 실패 — ") and "split" in line for line in lines)


def test_dataset_sha256_is_the_same_for_crlf_and_lf_files(tmp_path: Path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("시스템 프롬프트\n", encoding="utf-8")
    lf, crlf = tmp_path / "lf.jsonl", tmp_path / "crlf.jsonl"
    lf.write_bytes('{"id": "a"}\n{"id": "b"}\n'.encode("utf-8"))
    crlf.write_bytes('{"id": "a"}\r\n{"id": "b"}\r\n'.encode("utf-8"))
    assert (regression.environment(lf, prompt)["dataset_sha256"]
            == regression.environment(crlf, prompt)["dataset_sha256"])
