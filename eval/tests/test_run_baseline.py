import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import eval.run_baseline as runner
from eval.run_baseline import run_alternating, run_baseline, run_repeated
from app.services.normalize_ko import NORMALIZER_VERSION


@pytest.fixture(autouse=True)
def _clean_git_tree(monkeypatch: pytest.MonkeyPatch):
    """Runs refuse a dirty tree (2-7 a); tests pin a clean state so local edits do not matter."""
    monkeypatch.setattr(runner, "_git_state", lambda: {"git_commit": "abc1234", "git_dirty": False})


def test_runner_preserves_success_and_failure_and_writes_three_artifacts(tmp_path: Path):
    golden_path = tmp_path / "golden.jsonl"
    records = [
        {
            "id": "ok",
            "utterance_text": "가나상회에 무 세 개 현금 삼천 원",
            "expected_json": {
                "counterparty": "가나상회",
                "items": [{"name": "무", "qty": 3, "unit": "개", "unit_price": None, "amount": 3000}],
                "total_amount": 3000,
                "credit": False,
            },
            "expected_outcome": "정상 구조화 가능",
            "confirm_type": None,
            "required_clarifications": [],
            "difficulty_label": "명확한 표현",
            "review_status": "검수 완료",
            "notes": {"total_source": "stated"},
            "version": "v2.1",
            "split": "개발용 Golden",
        },
        {
            "id": "bad",
            "utterance_text": "실패 입력",
            "expected_json": {
                "counterparty": "실패상회",
                "items": [{"name": "배추", "qty": 1, "unit": None, "unit_price": None, "amount": 1000}],
                "total_amount": 1000,
                "credit": True,
            },
            "expected_outcome": "정상 구조화 가능",
            "confirm_type": None,
            "required_clarifications": [],
            "difficulty_label": "복잡·생략 표현",
            "review_status": "미검수",
            "notes": {"total_source": "derived"},
            "version": "v2.1",
            "split": "개발용 Golden",
        },
    ]
    golden_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in records), encoding="utf-8")

    def fake_parser(utterance: str, **_kwargs):
        if utterance == "실패 입력":
            raise RuntimeError("timeout")
        candidate = records[0]["expected_json"]
        return SimpleNamespace(
            candidate=SimpleNamespace(model_dump=lambda mode=None: candidate),
            model="gpt-4o-mini",
            processing_time_ms=120,
            prompt_tokens=100,
            completion_tokens=20,
            response_model="gpt-4o-mini-2024-07-18",
            system_fingerprint="fp_test",
        )

    summary = run_baseline(
        golden_path=golden_path,
        output_dir=tmp_path / "results",
        parser=fake_parser,
        run_id="test-run",
        settings=SimpleNamespace(llm_model="gpt-4o-mini", llm_max_retries=0, llm_timeout_seconds=5.0),
    )

    assert summary["run"]["model"] == "gpt-4o-mini"
    assert summary["run"]["dataset_version"] == "v2.1"
    assert summary["run"]["review_status"] == {"검수 완료": 1, "미검수": 1}
    assert summary["measurements"]["latency_ms"] == {"count": 1, "p50": 120, "p95": 120}
    assert summary["measurements"]["tokens"] == {"prompt": 100, "completion": 20, "total": 120}
    assert summary["measurements"]["over_deadline"] == {"deadline_ms": 5000, "count": 0, "total": 2}
    assert summary["run"]["response_models"] == ["gpt-4o-mini-2024-07-18"]
    assert summary["run"]["golden_file"] == "golden.jsonl"
    assert summary["metrics"]["generation"] == {"succeeded": 1, "failed": 1, "total": 2}
    assert summary["metrics"]["transaction_exact"]["total"] == 2

    prediction_rows = [
        json.loads(line)
        for line in (tmp_path / "results" / "test-run.predictions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert prediction_rows[0]["candidate"] == records[0]["expected_json"]
    assert prediction_rows[0]["system_fingerprint"] == "fp_test"
    assert prediction_rows[1]["candidate"] is None
    assert prediction_rows[1]["error"] == "RuntimeError: timeout"
    assert prediction_rows[1]['wall_time_ms'] >= 0
    sent_hash = hashlib.sha256("가나상회에 무 세 개 현금 삼천 원".encode("utf-8")).hexdigest()
    assert prediction_rows[0]["sent_text_sha256"] == sent_hash
    assert len(summary['run']['dataset_sha256']) == 64
    assert len(summary['run']['prompt_sha256']) == 64
    assert summary['run']['prompt_file'] == 'configs/prompts/parse_llm_system.txt'
    assert summary['run']['call_settings']['max_retries'] == 0
    assert 'retry_policy' not in summary['run']['call_settings']
    assert (tmp_path / "results" / "test-run.summary.json").exists()
    report = (tmp_path / "results" / "test-run.report.md").read_text(encoding="utf-8")
    assert "잠정 기준선" in report
    assert "추가 확인 적합성" in report
    assert "측정 불가" in report
    assert "외상 반대 저장:" in report
    assert "호출 전체 마감 5.0초, SDK 재시도 0회" in report
    assert "SDK 기본 재시도" not in report


def _dev_record(record_id: str, split: str = "개발용 Golden") -> dict:
    return {
        "id": record_id,
        "utterance_text": f"{record_id} 무 세 개 현금 삼천 원",
        "expected_json": {"counterparty": "가나상회", "items": [{"name": "무", "qty": 3}],
                          "total_amount": 3000, "credit": False},
        "expected_outcome": "정상 구조화 가능",
        "review_status": "검수 완료",
        "notes": {"total_source": "stated"},
        "version": "v2.5",
        "split": split,
    }


def _result(candidate: dict) -> SimpleNamespace:
    return SimpleNamespace(
        candidate=SimpleNamespace(model_dump=lambda mode=None: candidate),
        model="gpt-4o-mini", processing_time_ms=10, prompt_tokens=1, completion_tokens=1,
        response_model="gpt-4o-mini-2024-07-18", system_fingerprint=None,
    )


def test_runner_refuses_final_golden_before_calling(tmp_path: Path):
    """Final Golden stays forbidden for measurement (불변 규칙 11), even after Seed was allowed."""
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(json.dumps(_dev_record("t-1", split="최종 평가용 Golden"), ensure_ascii=False), encoding="utf-8")
    calls = []

    with pytest.raises(ValueError, match="not allowed for measurement"):
        run_baseline(golden_path=golden_path, output_dir=tmp_path / "out",
                     parser=lambda *a, **k: calls.append(a), run_id="x",
                     settings=SimpleNamespace(llm_model="gpt-4o-mini"))
    assert calls == []


def test_repeated_runs_pick_median_and_report_spread_and_unstable_records(tmp_path: Path):
    golden_path = tmp_path / "golden.jsonl"
    rows = [_dev_record("a"), _dev_record("b")]
    golden_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    right = rows[0]["expected_json"]
    wrong = {**right, "total_amount": 5000}
    # correct counts per run: r1 = 2, r2 = 0, r3 = 1 -> median is r3; "a" and "b" both flip.
    plan = iter([right, right, wrong, wrong, right, wrong])

    group = run_repeated(repeat=3, golden_path=golden_path, output_dir=tmp_path / "out",
                         parser=lambda *a, **k: _result(next(plan)), group_id="grp",
                         settings=SimpleNamespace(llm_model="gpt-4o-mini"))

    assert [run["transaction_correct"] for run in group["runs"]] == [2, 0, 1]
    assert group["median_run_id"] == "grp-r3"
    assert group["spread"]["transaction_correct"] == 2
    assert group["unstable_record_ids"] == ["a", "b"]
    assert (tmp_path / "out" / "grp.group.json").exists()


def test_median_tie_picks_the_earliest_run_with_the_median_value(tmp_path: Path):
    """PM rule (D02 §1-4): among runs tied at the median accuracy, the earlier run is representative."""
    golden_path = tmp_path / "golden.jsonl"
    rows = [_dev_record("a"), _dev_record("b"), _dev_record("c")]
    golden_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    right = rows[0]["expected_json"]
    wrong = {**right, "total_amount": 5000}
    # correct counts per run: r1 = 3, r2 = 1, r3 = 1 -> median value 1 -> earliest such run is r2.
    plan = iter([right, right, right, right, wrong, wrong, right, wrong, wrong])

    group = run_repeated(repeat=3, golden_path=golden_path, output_dir=tmp_path / "out",
                         parser=lambda *a, **k: _result(next(plan)), group_id="tie",
                         settings=SimpleNamespace(llm_model="gpt-4o-mini"))

    assert [run["transaction_correct"] for run in group["runs"]] == [3, 1, 1]
    assert group["median_run_id"] == "tie-r2"
    assert all("over_deadline_rate" in run for run in group["runs"])


def _ko_record() -> dict:
    record = _dev_record("ko")
    record["utterance_text"] = "가나상회 무 세 개 현금 삼천 원"
    return record


def test_sent_text_hash_matches_normalized_text_when_flag_is_on(tmp_path: Path):
    """A-2 Step 2: the hash must describe what was actually sent (normalized text)."""
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(json.dumps(_ko_record(), ensure_ascii=False), encoding="utf-8")

    run_baseline(golden_path=golden_path, output_dir=tmp_path / "out",
                 parser=lambda *a, **k: _result(_ko_record()["expected_json"]), run_id="ko",
                 settings=SimpleNamespace(llm_model="gpt-4o-mini", ko_numeral_normalize=True))

    row = json.loads((tmp_path / "out" / "ko.predictions.jsonl").read_text(encoding="utf-8"))
    sent = "가나상회 무 3개 현금 3000원"
    assert row["sent_text_sha256"] == hashlib.sha256(sent.encode("utf-8")).hexdigest()


def test_summary_records_ko_numeral_setting(tmp_path: Path):
    """A-2 Step 2: on/off runs must be distinguishable from the summary alone."""
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(json.dumps(_ko_record(), ensure_ascii=False), encoding="utf-8")

    on = run_baseline(golden_path=golden_path, output_dir=tmp_path / "on",
                      parser=lambda *a, **k: _result(_ko_record()["expected_json"]), run_id="on",
                      settings=SimpleNamespace(llm_model="gpt-4o-mini", ko_numeral_normalize=True))
    off = run_baseline(golden_path=golden_path, output_dir=tmp_path / "off",
                       parser=lambda *a, **k: _result(_ko_record()["expected_json"]), run_id="off",
                       settings=SimpleNamespace(llm_model="gpt-4o-mini"))

    assert on["run"]["ko_numeral_normalize"] is True
    assert on["run"]["normalizer_version"] == NORMALIZER_VERSION
    assert off["run"]["ko_numeral_normalize"] is False
    assert off["run"]["normalizer_version"] is None
    # A-4c 2장: measurements run with post-processing off; the setting is recorded either way
    assert off["run"]["llm_postprocess"] is False
    assert off["run"]["postprocess_version"] is None
    assert "한글 수사 정규화: on" in (tmp_path / "on" / "on.report.md").read_text(encoding="utf-8")



def _two_records(tmp_path: Path) -> Path:
    golden_path = tmp_path / "golden.jsonl"
    rows = [_ko_record(), {**_dev_record("plain"), "utterance_text": "가나상회 무 3개 현금 3000원"}]
    golden_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return golden_path


# app_env="development" is explicit: without backend/.env (as in CI) Settings defaults to production
# and refuses the default JWT secret, so run_alternating could not build the condition settings.
KO_CONDITIONS = [
    ("ko-off", {"ko_numeral_normalize": False, "app_env": "development"}),
    ("ko-on", {"ko_numeral_normalize": True, "app_env": "development"}),
]


def test_alternating_runs_interleave_conditions_and_write_group_files(tmp_path: Path):
    """2-7 b: off r1, on r1, off r2, on r2, off r3, on r3 — same time window for both conditions."""
    seen = []

    def parser(utterance, *, settings):
        seen.append(settings.ko_numeral_normalize)
        return _result(_ko_record()["expected_json"])

    result = run_alternating(conditions=KO_CONDITIONS, repeat=3, golden_path=_two_records(tmp_path),
                             output_dir=tmp_path / "out", parser=parser, group_prefix="alt")

    order = [step["run_id"] for step in result["order"]]
    assert order == ["alt-ko-off-r1", "alt-ko-on-r1", "alt-ko-off-r2", "alt-ko-on-r2", "alt-ko-off-r3", "alt-ko-on-r3"]
    assert all(step["started_at"] and step["finished_at"] for step in result["order"])
    assert seen == [False, False, True, True] * 3  # 2 records per run, conditions alternate
    assert result["git_commit"] == "abc1234"
    assert set(result["representative_run_ids"]) == {"ko-off", "ko-on"}
    for tag in ("ko-off", "ko-on"):
        assert (tmp_path / "out" / f"alt-{tag}.group.json").exists()
    saved = json.loads((tmp_path / "out" / "alt.alternating.json").read_text(encoding="utf-8"))
    assert saved["conditions"] == [{"tag": t, "overrides": o} for t, o in KO_CONDITIONS]


def test_alternating_overrides_reach_the_sent_text(tmp_path: Path):
    """2-7 b: overrides are injected into Settings, so off sends the original and on the normalized text."""
    run_alternating(conditions=KO_CONDITIONS, repeat=1, golden_path=_two_records(tmp_path),
                    output_dir=tmp_path / "out", parser=lambda *a, **k: _result(_ko_record()["expected_json"]),
                    group_prefix="alt")

    def first_hash(run_id):
        return json.loads((tmp_path / "out" / f"{run_id}.predictions.jsonl").read_text(encoding="utf-8").splitlines()[0])["sent_text_sha256"]

    sha = lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert first_hash("alt-ko-off-r1") == sha("가나상회 무 세 개 현금 삼천 원")
    assert first_hash("alt-ko-on-r1") == sha("가나상회 무 3개 현금 3000원")


def test_dirty_tree_is_refused_unless_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """2-7 a: paid runs must use committed code; --allow-dirty runs but keeps the flag in the record."""
    monkeypatch.setattr(runner, "_git_state", lambda: {"git_commit": "abc1234", "git_dirty": True})
    calls = []
    golden_path = _two_records(tmp_path)

    with pytest.raises(RuntimeError, match="uncommitted"):
        run_baseline(golden_path=golden_path, output_dir=tmp_path / "a", run_id="x",
                     parser=lambda *a, **k: calls.append(a), settings=SimpleNamespace(llm_model="gpt-4o-mini"))
    with pytest.raises(RuntimeError, match="uncommitted"):
        run_alternating(conditions=KO_CONDITIONS, repeat=1, golden_path=golden_path,
                        output_dir=tmp_path / "b", parser=lambda *a, **k: calls.append(a), group_prefix="d")
    assert calls == []

    summary = run_baseline(golden_path=golden_path, output_dir=tmp_path / "c", run_id="y", allow_dirty=True,
                           parser=lambda *a, **k: _result(_ko_record()["expected_json"]),
                           settings=SimpleNamespace(llm_model="gpt-4o-mini"))
    assert summary["run"]["git_commit"] == "abc1234"
    assert summary["run"]["git_dirty"] is True
    assert "커밋 안 된 변경 포함" in (tmp_path / "c" / "y.report.md").read_text(encoding="utf-8")


def test_alternating_refuses_conditions_that_differ_without_an_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """2-7 b: model/prompt/temperature must match unless a condition overrides that key explicitly."""
    prompts = iter(["프롬프트 A", "프롬프트 B"])
    monkeypatch.setattr(runner, "load_system_prompt", lambda settings: next(prompts))
    calls = []

    with pytest.raises(ValueError, match="prompt"):
        run_alternating(conditions=KO_CONDITIONS, repeat=1, golden_path=_two_records(tmp_path),
                        output_dir=tmp_path / "out", parser=lambda *a, **k: calls.append(a), group_prefix="p")
    assert calls == []


def test_alternating_rejects_unknown_setting_keys(tmp_path: Path):
    """A typo such as ko_numeral_normalise would be silently ignored by Settings and waste a paid run."""
    with pytest.raises(ValueError, match="ko_numeral_normalise"):
        run_alternating(conditions=[("a", {"ko_numeral_normalise": True}), ("b", {})], repeat=1,
                        golden_path=_two_records(tmp_path), output_dir=tmp_path / "out",
                        parser=lambda *a, **k: None, group_prefix="t")



def test_seed_split_is_measurable_and_recorded(tmp_path: Path):
    """Q2: Seed data may be measured; the summary and report say which data was used."""
    golden_path = tmp_path / "seed.jsonl"
    rows = [_dev_record("s-1", split="Seed"), _dev_record("s-2", split="Seed")]
    golden_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")

    summary = run_baseline(golden_path=golden_path, output_dir=tmp_path / "out", run_id="seed",
                           parser=lambda *a, **k: _result(rows[0]["expected_json"]),
                           settings=SimpleNamespace(llm_model="gpt-4o-mini"))

    assert summary["run"]["split"] == "Seed"
    assert "Seed" in (tmp_path / "out" / "seed.report.md").read_text(encoding="utf-8")


def test_dev_golden_split_is_recorded(tmp_path: Path):
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(json.dumps(_dev_record("d-1"), ensure_ascii=False), encoding="utf-8")
    summary = run_baseline(golden_path=golden_path, output_dir=tmp_path / "out", run_id="dev",
                           parser=lambda *a, **k: _result(_dev_record("d-1")["expected_json"]),
                           settings=SimpleNamespace(llm_model="gpt-4o-mini"))
    assert summary["run"]["split"] == "개발용 Golden"


def test_mixed_splits_in_one_file_are_refused_before_calling(tmp_path: Path):
    """Q2: Golden and Seed scored together would blend two numbers into one."""
    golden_path = tmp_path / "mixed.jsonl"
    rows = [_dev_record("d-1"), _dev_record("s-1", split="Seed")]
    golden_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    calls = []

    with pytest.raises(ValueError, match="mixed"):
        run_baseline(golden_path=golden_path, output_dir=tmp_path / "out", run_id="mix",
                     parser=lambda *a, **k: calls.append(a), settings=SimpleNamespace(llm_model="gpt-4o-mini"))
    assert calls == []


@pytest.mark.parametrize(
    ("llm_postprocess", "normalize", "recorded"),
    [(True, False, "credit"), (True, True, True), (False, True, False), (False, False, False)],
)
def test_summary_postprocess_version_follows_actual_application(tmp_path: Path, llm_postprocess, normalize, recorded):
    """PM #37 리뷰: postprocess_version only when post-processing actually ran (postprocess_active)."""
    from app.services.postprocess import POSTPROCESS_VERSION

    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(json.dumps(_ko_record(), ensure_ascii=False), encoding="utf-8")
    summary = run_baseline(golden_path=golden_path, output_dir=tmp_path / "out",
                           parser=lambda *a, **k: _result(_ko_record()["expected_json"]), run_id="pp",
                           settings=SimpleNamespace(llm_model="gpt-4o-mini", ko_numeral_normalize=normalize,
                                                    llm_postprocess=llm_postprocess))

    assert summary["run"]["llm_postprocess"] is llm_postprocess  # the raw setting is kept as is
    expected = (f"{POSTPROCESS_VERSION}-credit" if recorded == "credit"
                else POSTPROCESS_VERSION if recorded else None)
    assert summary["run"]["postprocess_version"] == expected


def test_predictions_store_the_validation_response(tmp_path: Path):
    """S-9 1부: each prediction row carries the shared BE validation result of the raw output."""
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(json.dumps(_ko_record(), ensure_ascii=False), encoding="utf-8")
    run_baseline(golden_path=golden_path, output_dir=tmp_path / "out",
                 parser=lambda *a, **k: _result(_ko_record()["expected_json"]), run_id="v",
                 settings=SimpleNamespace(llm_model="gpt-4o-mini", ko_numeral_normalize=True))

    row = json.loads((tmp_path / "out" / "v.predictions.jsonl").read_text(encoding="utf-8"))
    assert row["validation_response"]["status_code"] == 200


def test_failed_generation_stores_a_validation_response_without_status(tmp_path: Path):
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(json.dumps(_ko_record(), ensure_ascii=False), encoding="utf-8")

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    run_baseline(golden_path=golden_path, output_dir=tmp_path / "out", parser=broken, run_id="f",
                 settings=SimpleNamespace(llm_model="gpt-4o-mini", ko_numeral_normalize=True))
    row = json.loads((tmp_path / "out" / "f.predictions.jsonl").read_text(encoding="utf-8"))
    assert row["validation_response"]["status_code"] is None



# --- DL-11: provider=gemini 번갈아 측정 (S-17) ---

GEM_MODEL = "gemini-3.1-flash-lite"
DL11_CONDITIONS = [
    ("mini", {"llm_model": "gpt-4o-mini", "app_env": "development"}),
    ("gem", {"provider": "gemini", "llm_model": GEM_MODEL, "app_env": "development"}),
]


def _gemini_result(candidate: dict) -> SimpleNamespace:
    result = _result(candidate)
    result.model, result.response_model = GEM_MODEL, "gemini-3.1-flash-lite-05-2026"
    return result


def test_provider_picks_the_parser_and_is_recorded(tmp_path: Path):
    """S-17 3장: provider is a measurement-only key — gemini conditions go to the Gemini adapter."""
    calls = []

    def openai_parser(utterance, *, settings):
        calls.append(("openai", settings.llm_model, settings.llm_postprocess))
        return _result(_ko_record()["expected_json"])

    def gemini_parser(utterance, *, settings):
        calls.append(("gemini", settings.llm_model, settings.llm_postprocess))
        return _gemini_result(_ko_record()["expected_json"])

    result = run_alternating(conditions=DL11_CONDITIONS, repeat=1, golden_path=_two_records(tmp_path),
                             output_dir=tmp_path / "out", parser=openai_parser, gemini_parser=gemini_parser,
                             group_prefix="dl11")

    # Raw output stored for every model (llm_postprocess fixed false) so score.py post-processes all alike.
    assert calls == [("openai", "gpt-4o-mini", False)] * 2 + [("gemini", GEM_MODEL, False)] * 2
    gem = json.loads((tmp_path / "out" / "dl11-gem-r1.summary.json").read_text(encoding="utf-8"))["run"]
    mini = json.loads((tmp_path / "out" / "dl11-mini-r1.summary.json").read_text(encoding="utf-8"))["run"]
    assert (gem["provider"], gem["requested_model"], gem["thinking"]) == ("gemini", GEM_MODEL, "thinking_budget=0")
    assert gem["response_models"] == ["gemini-3.1-flash-lite-05-2026"]
    assert gem["executed_at"]
    assert mini["provider"] == "openai"
    assert result["conditions"] == [{"tag": t, "overrides": o} for t, o in DL11_CONDITIONS]
    assert set(result["cost_estimate"]["by_tag"]) == {"mini", "gem"}


@pytest.mark.parametrize("split", ["Seed", "최종 평가용 Golden"])
def test_gemini_refuses_anything_but_dev_golden_before_any_call(tmp_path: Path, split):
    """S-17 3장: one non-Dev row stops the whole comparison before OpenAI or Gemini is called."""
    golden_path = tmp_path / "golden.jsonl"
    rows = [_dev_record("s-1", split=split), _dev_record("s-2", split=split)]
    golden_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    calls = []

    with pytest.raises(ValueError, match="not allowed|gemini allows only"):
        run_alternating(conditions=DL11_CONDITIONS, repeat=1, golden_path=golden_path, output_dir=tmp_path / "a",
                        parser=lambda *a, **k: calls.append(a), gemini_parser=lambda *a, **k: calls.append(a),
                        group_prefix="g")
    with pytest.raises(ValueError, match="not allowed|gemini allows only"):
        run_baseline(golden_path=golden_path, output_dir=tmp_path / "b", run_id="g", provider="gemini",
                     parser=lambda *a, **k: calls.append(a), settings=SimpleNamespace(llm_model=GEM_MODEL))
    assert calls == []


def test_unknown_provider_is_refused(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown provider"):
        run_alternating(conditions=[("a", {"provider": "gemni"}), ("b", {})], repeat=1,
                        golden_path=_two_records(tmp_path), output_dir=tmp_path / "out",
                        parser=lambda *a, **k: None, group_prefix="u")


def test_min_interval_is_waited_but_not_counted_as_latency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """S-17 3장: spacing for the per-minute limit sits outside wall time and latency."""
    clock = {"now": 0.0}
    sleeps = []
    monkeypatch.setattr(runner.time, "perf_counter", lambda: clock["now"])

    def fake_sleep(seconds):
        sleeps.append(round(seconds, 6))
        clock["now"] += seconds

    monkeypatch.setattr(runner.time, "sleep", fake_sleep)

    def gemini_parser(utterance, *, settings):
        clock["now"] += 0.2  # each call takes 200 ms
        return _gemini_result(_ko_record()["expected_json"])

    run_baseline(golden_path=_two_records(tmp_path), output_dir=tmp_path / "out", run_id="iv", provider="gemini",
                 min_interval_seconds=4.0, parser=gemini_parser, settings=SimpleNamespace(llm_model=GEM_MODEL))

    rows = [json.loads(line) for line in (tmp_path / "out" / "iv.predictions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sleeps == [3.8]  # 4 s between call starts minus the 200 ms the first call took
    assert [row["wall_time_ms"] for row in rows] == [200, 200]


def test_min_interval_does_not_apply_to_openai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sleeps = []
    monkeypatch.setattr(runner.time, "sleep", sleeps.append)
    run_baseline(golden_path=_two_records(tmp_path), output_dir=tmp_path / "out", run_id="o",
                 min_interval_seconds=4.0, parser=lambda *a, **k: _result(_ko_record()["expected_json"]),
                 settings=SimpleNamespace(llm_model="gpt-4o-mini"))
    assert sleeps == []


def test_rate_limit_is_recorded_and_stops_the_run(tmp_path: Path):
    """S-17 3장: 429 is not retried — the row is kept as a failure and the run stops for a report."""
    from eval.providers.gemini_parse import GeminiRateLimitError

    def limited(*args, **kwargs):
        raise GeminiRateLimitError("rate_limited status=429")

    with pytest.raises(RuntimeError, match="429"):
        run_baseline(golden_path=_two_records(tmp_path), output_dir=tmp_path / "out", run_id="rl",
                     provider="gemini", parser=limited, settings=SimpleNamespace(llm_model=GEM_MODEL))
    rows = (tmp_path / "out" / "rl.predictions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["error"] == "GeminiRateLimitError: rate_limited status=429"


def test_estimated_cost_over_the_cap_runs_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    """S-17 3장: the per-model 3-run estimate and total are printed; above the cap nothing is called."""
    prices = runner.load_prices()
    monkeypatch.setattr(runner, "load_prices", lambda: {**prices, "cost_cap_usd": 0.000001})
    calls = []

    with pytest.raises(RuntimeError, match="exceeds cap"):
        run_alternating(conditions=DL11_CONDITIONS, repeat=3, golden_path=_two_records(tmp_path),
                        output_dir=tmp_path / "out", parser=lambda *a, **k: calls.append(a),
                        gemini_parser=lambda *a, **k: calls.append(a), group_prefix="c")
    assert calls == []
    printed = capsys.readouterr().out
    assert "mini (gpt-4o-mini) 3회" in printed and f"gem ({GEM_MODEL}) 3회" in printed and "합계" in printed


def test_model_without_a_price_cannot_be_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner, "load_prices", lambda: {"cost_cap_usd": 1.0, "models": {
        "gpt-4o-mini": {"input_usd_per_1m": 0.15, "output_usd_per_1m": 0.60},
        GEM_MODEL: {"input_usd_per_1m": None, "output_usd_per_1m": None}}})

    with pytest.raises(ValueError, match="no price"):
        run_alternating(conditions=DL11_CONDITIONS, repeat=1, golden_path=_two_records(tmp_path),
                        output_dir=tmp_path / "out", parser=lambda *a, **k: None, group_prefix="n")


def test_price_file_is_read_for_the_cost_and_report(tmp_path: Path):
    """S-17 3장: unit prices come from configs/eval_prices.yaml (mini $0.15/$0.60 moved there)."""
    prices = runner.load_prices()
    mini = prices["models"]["gpt-4o-mini"]
    assert (mini["input_usd_per_1m"], mini["output_usd_per_1m"]) == (0.15, 0.60)
    assert prices["cost_cap_usd"] == 1.0
    for entry in prices["models"].values():
        assert entry["source"].startswith("https://") and entry["checked_at"]

    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(json.dumps(_ko_record(), ensure_ascii=False), encoding="utf-8")
    big = SimpleNamespace(candidate=SimpleNamespace(model_dump=lambda mode=None: _ko_record()["expected_json"]),
                          model="gpt-4o-mini", processing_time_ms=10, prompt_tokens=1_000_000,
                          completion_tokens=1_000_000, response_model=None)
    summary = run_baseline(golden_path=golden_path, output_dir=tmp_path / "out", run_id="p",
                           parser=lambda *a, **k: big, settings=SimpleNamespace(llm_model="gpt-4o-mini"))

    assert summary["cost_estimate_usd"] == 0.75
    assert "입력 $0.15/1M, 출력 $0.6/1M 기준" in (tmp_path / "out" / "p.report.md").read_text(encoding="utf-8")

    unpriced = run_baseline(golden_path=golden_path, output_dir=tmp_path / "out2", run_id="q",
                            parser=lambda *a, **k: big, settings=SimpleNamespace(llm_model="some-unlisted-model"))
    assert unpriced["cost_estimate_usd"] is None
    assert "단가 없음" in (tmp_path / "out2" / "q.report.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", [None, "purchase"])
def test_transaction_type_is_saved_only_when_the_model_answered_it(tmp_path: Path, kind):
    """DL-30 (PM 10/14): flag-off rows keep the old shape; a typed row is validated with its type."""
    golden_path = tmp_path / "golden.jsonl"
    record = {**_dev_record("d-1"), "utterance_text": "가나상회에서 무 세 개 삼천 원에 사왔어"}
    golden_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    result = _result(record["expected_json"])
    result.transaction_type = kind
    summary = run_baseline(golden_path=golden_path, output_dir=tmp_path / "out", run_id="t",
                           parser=lambda *a, **k: result,
                           settings=SimpleNamespace(llm_model="gpt-4o-mini", llm_transaction_type=kind is not None))
    row = json.loads((tmp_path / "out" / "t.predictions.jsonl").read_text(encoding="utf-8"))
    assert summary["run"]["llm_transaction_type"] is (kind is not None)
    if kind is None:
        assert "transaction_type" not in row
        assert row["validation_response"]["error_code"] != "UNSUPPORTED_TRANSACTION"
    else:
        assert row["transaction_type"] == "purchase"
        assert row["validation_response"]["error_code"] == "UNSUPPORTED_TRANSACTION"


def test_gemini_guard_allows_dev_golden_and_seed_v2_only():
    """PM 10/21: Seed v2 (split "Seed" + ID "seed2-") may go to Gemini; Seed v1 and final Golden stay refused."""
    runner._require_gemini_dev_only([_dev_record("g-dev-001")])
    runner._require_gemini_dev_only([_dev_record("seed2-001", split="Seed"), _dev_record("seed2-002", split="Seed")])
    for rows in (
        [_dev_record("seed-001", split="Seed")],  # Seed v1 format
        [_dev_record("seed2-001", split="Seed"), _dev_record("seed-002", split="Seed")],  # one v1 row stops it
        [_dev_record("seed2-001", split="최종 평가용 Golden")],  # the ID alone is not enough
        [_dev_record("seed2-001", split="개발용 Golden "), ],  # near-miss split name
    ):
        with pytest.raises(ValueError, match="gemini allows only"):
            runner._require_gemini_dev_only(rows)


@pytest.mark.parametrize("ids", [("seed-001", "seed-002"), ("seed2-001", "seed-002")])
def test_gemini_comparison_refuses_seed_files_that_are_not_all_seed_v2(tmp_path: Path, ids):
    golden_path = tmp_path / "seed.jsonl"
    rows = [_dev_record(record_id, split="Seed") for record_id in ids]
    golden_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    calls = []

    with pytest.raises(ValueError, match="gemini allows only"):
        run_alternating(conditions=DL11_CONDITIONS, repeat=1, golden_path=golden_path, output_dir=tmp_path / "out",
                        parser=lambda *a, **k: calls.append(a), gemini_parser=lambda *a, **k: calls.append(a),
                        group_prefix="s")
    assert calls == []
