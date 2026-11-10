"""측정 틀 2단계: 저장된 AI 출력을 재생해 단계별 기여도·시간을 잰다 (PM 10/30 범위, 보고서 마감 11/5).

steps_frame의 단계(정규화 → AI → 후처리 → 검증) 중 AI는 저장된 답을 그대로 쓰고(API 호출 없음, 0원),
나머지 단계는 steps_frame의 단계 함수를 이 PC에서 다시 돌린다.
- 기여도: 같은 문장·같은 AI 답에서 세 지점을 채점한다.
  (A) AI 원 출력 그대로 (후처리·검증 없음 = 생성된 것은 모두 저장된 것으로 본다)
  (B) A + 후처리 (지금 코드의 후처리)
  (C) B + 서버 검증 (200 저장 / 400 되묻기) — score.py --postprocess --attach-validation과 같은 점수
- 시간: 정규화·후처리·검증은 같은 문장을 반복해 재고(문장별 중앙값 → 전체 p50·p95),
  AI 시간은 저장된 processing_time_ms·wall_time_ms(측정 날짜 함께)를 쓴다.
- 정규화 기여도는 0원으로 잴 수 없어 기존 결과(ko-numeral-off-on-20261001.md)를 참고값으로 인용만 한다.
가드·로딩·채점은 run_baseline·score·scoring의 것을 그대로 쓴다. 결과 파일에는 문장 원문을 넣지 않고 ID만 쓴다.

사용 예 (레포 루트):
  python eval/steps_report.py --output-dir eval/results/steps-stage2-20261030 \\
    --set dev data/golden/golden_dev.jsonl <r1.predictions.jsonl> <r2…> <r3…> \\
    --set seed2 <seed_v2_improve.jsonl> <r1…> <r2…> <r3…>
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT, REPO_ROOT / "backend"):  # import 순서와 상관없이 eval·app을 찾도록
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.config import Settings
from app.services.normalize_ko import NORMALIZER_VERSION
from app.services.postprocess import postprocess_label

from eval import run_baseline as rb  # 재사용: 가드·로딩·백분위·json 쓰기
from eval import steps_frame as sf  # 재사용: 단계 함수
from eval.regression import dataset_sha256 as lf_dataset_sha256
from eval.score import _run_summary
from eval.scoring import NORMAL_OUTCOME, SCORING_VERSION, score_records

LOCAL_STEPS = [("normalize", sf.step_normalize), ("postprocess", sf.step_postprocess), ("validate", sf.step_validate)]
POINTS = ("A_raw", "B_postprocess", "C_validated")
# 검증 없는 지점(A·B): 생성된 후보는 묻지 않고 모두 저장된다고 본다 (외상 잘못 확정·틀린 값 저장을 같은 식으로 세기 위해)
SAVED_WITHOUT_CHECK = {"status_code": 200, "error_code": None, "problem_fields": [], "partial_candidate": None}
NOT_GENERATED = {"status_code": None, "error_code": None, "problem_fields": [], "partial_candidate": None}
CASE_ITEM_FIELDS = ("qty", "unit_price", "amount")
# 이번 결과의 기준 (PM 10/30): 후처리 버전이 바뀌면 동결 커밋에서 다시 재생한다
SOURCE_NOTE = "후처리 pp-1.5(main e33baa5) 기준. #241(pp-1.6) 머지 후 동결 커밋으로 다시 재생 예정."
# 따로 볼 문장의 원인 (사람이 저장된 답을 보고 적은 설명, 2026-10-30). 원문은 넣지 않는다.
# 같은 ID가 다시 나오면 REPORT에 붙고, 없으면 자동 사실(규칙·바뀐 값·검증 결과)만 나온다.
CASE_NOTES = {
    "g-dev-056": ("품목이 상한보다 많아 되물어야 하는 문장이다. AI가 품목마다 단가를 채웠고, 단가 표지가 없어 "
                  "unit_price_cleared가 지웠으며, 계산된 품목 금액이 말한 숫자가 아니라 item_amount_invented가 그 금액과 "
                  "맞던 총액을 지웠다 → 금액만 맞음→틀림. 검증은 품목 초과 400이라 어차피 저장되지 않는다 (3회 모두)."),
    "seed2-061": ("정답은 외상 여부를 되물어야 하는 문장이다 (외상 말이 부정으로만 나온다, Seed v2 PM 10/8 부정 표현). "
                  "r3에서 AI가 credit=false로 답했다. 후처리 pp-1.3.1은 부정어만 있을 때 AI가 부정된 쪽(외상)을 고른 "
                  "경우만 지우므로(credit_cleared_negated) false는 그대로 남고, 검증은 외상 칸이 차 있어 200으로 통과했다. "
                  "r1·r2는 AI가 true로 답해 credit_cleared_negated가 지우고 400으로 되물었다. "
                  "→ 부정어만 있는 외상 표현에서 후처리 규칙과 Seed v2 정답이 어긋나는 곳."),
    "seed2-041": ("AI가 첫 품목의 품목 금액을 단가로 읽고 품목 금액을 수량×단가로 계산했다. 문장에 단가 표지가 없어 "
                  "unit_price_cleared가 단가를 지웠고, 계산된 품목 금액은 말한 숫자가 아니라 item_amount_invented가 "
                  "그 금액과 함께 말한 합계(총액)까지 지웠다 (다른 품목 금액은 말한 숫자라 총액을 남기는 예외에 해당하지 않음). "
                  "→ 검증은 총액 누락 400. 틀린 값이 저장되지는 않지만 맞던 총액이 되묻기로 바뀌었다 (3회 모두)."),
    "seed2-093": ("r1·r3에서 AI가 용량이 붙은 품목명과 팩 수를 무게 수량·단가로 잘못 풀고 품목을 둘로 나눴다 (거래는 (A)부터 틀림). "
                  "unit_price_cleared가 단가를 지우고 item_amount_invented가 계산된 품목 금액과 함께 맞던 총액(말한 숫자)을 "
                  "지웠다 → 금액만 맞음→틀림, 검증은 총액 누락 400. r2는 AI가 품목 하나로 답해 규칙이 붙지 않았다."),
}
# 정규화 off/on 참고값 (인용만, 재측정 아님): Dev 60건 · gpt-4o-mini · 대표 실행 · ko-num-1.1 · 2026-10-01
KO_NUMERAL_REFERENCE = {
    "source": "eval/results/ko-numeral-off-on-20261001.md",
    "transaction_exact": {"off": "23/42 (54.76%)", "on": "40/42 (95.24%)", "delta": "+17건"},
    "amount_exact": {"off": "39/56 (69.64%)", "on": "55/56 (98.21%)", "delta": "+16건"},
    "required_f1": {"off": "89.94%", "on": "98.78%", "delta": "+8.84%p"},
}


def dataset_hashes(golden_path: Path) -> set[str]:
    """같은 데이터로 볼 해시: 원본 바이트, LF로 맞춘 것(regression.py와 같음), CRLF로 맞춘 것.

    실행 기록은 측정한 PC의 바이트 해시라 Windows 측정은 CRLF 기준이고, CI는 LF로 체크아웃한다.
    """
    raw = golden_path.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    return {hashlib.sha256(raw).hexdigest(), lf_dataset_sha256(golden_path),
            hashlib.sha256(lf.replace(b"\n", b"\r\n")).hexdigest()}


def load_run(golden_path: Path, predictions_path: Path) -> tuple[list[dict], dict[str, dict], dict[str, Any], str]:
    """Golden·저장된 답·실행 summary를 읽고 가드를 건다. Seed v1·최종 Golden Test·다른 데이터 파일은 거부."""
    records = rb._load_jsonl(golden_path)
    split = rb._require_measurable_split(records)  # 최종 Golden Test 거부
    rb._require_gemini_dev_only(records)  # Dev Golden·Seed v2(seed2-*)만 — Seed v1 거부
    score_records(records, {})  # 중복 ID 거부
    run = _run_summary(predictions_path)["run"]
    if run.get("llm_postprocess") is True:
        raise ValueError("이미 후처리된 출력 — 원 출력(llm_postprocess=false)만 재생할 수 있다")
    if run.get("dataset_sha256") not in dataset_hashes(golden_path):
        raise ValueError(f"데이터 파일이 실행 때와 다르다 (dataset_sha256): {golden_path.name}")
    rows = {row["id"]: row for row in rb._load_jsonl(predictions_path)}
    golden_ids = {record["id"] for record in records}
    if set(rows) != golden_ids:
        raise ValueError(f"Golden과 저장된 답의 ID가 다르다: {sorted(set(rows) ^ golden_ids)[:5]}")
    return records, rows, run, split


def replay(records: list[dict], rows: dict[str, dict], run: dict[str, Any], repeats: int) -> dict[str, dict]:
    """문장마다 정규화→(저장된 AI 답)→후처리→검증을 repeats번 돌려 결과와 단계별 시간(ms 목록)을 모은다."""
    settings = Settings(_env_file=None, app_env="development",
                        ko_numeral_normalize=bool(run.get("ko_numeral_normalize", False)))
    out: dict[str, dict] = {}
    for record in records:
        row = rows[record["id"]]
        candidate = None if row.get("error") or not isinstance(row.get("candidate"), dict) else row["candidate"]
        step_ms: dict[str, list[float]] = {name: [] for name, _ in LOCAL_STEPS}
        for _ in range(repeats):
            ctx = sf.Ctx(utterance=record["utterance_text"], settings=settings, validation_settings=settings,
                         postprocess=True, candidate=candidate, llm_transaction_type=row.get("transaction_type"))
            for name, fn in LOCAL_STEPS:
                started = time.perf_counter()
                fn(ctx)
                step_ms[name].append((time.perf_counter() - started) * 1000)
        out[record["id"]] = {
            "raw": ctx["raw_candidate"], "pp": ctx["candidate"], "applied": ctx["applied"],
            "validation_response": ctx["validation_response"], "error": row.get("error"), "step_ms": step_ms,
            "sent_text_match": hashlib.sha256(ctx["sent_text"].encode("utf-8")).hexdigest()
            == row.get("sent_text_sha256"),
        }
    return out


def point_rows(replayed: dict[str, dict]) -> dict[str, dict[str, dict]]:
    """세 지점의 채점 입력(score_records의 predictions)."""
    def unchecked(candidate: Any) -> dict:
        return SAVED_WITHOUT_CHECK if candidate is not None else NOT_GENERATED
    return {
        "A_raw": {i: {"candidate": r["raw"], "error": r["error"], "validation_response": unchecked(r["raw"])}
                  for i, r in replayed.items()},
        "B_postprocess": {i: {"candidate": r["pp"], "error": r["error"], "validation_response": unchecked(r["pp"])}
                          for i, r in replayed.items()},
        "C_validated": {i: {"candidate": r["pp"], "error": r["error"], "validation_response": r["validation_response"]}
                        for i, r in replayed.items()},
    }


def point_metrics(records: list[dict], predictions: dict[str, dict]) -> dict[str, Any]:
    """한 지점의 지표. 저장 = 200 (A·B는 생성된 후보 전부).

    - wrong_saved_normal: 정상 문장인데 거래가 틀린 채 저장 (#205 run_cascade의 "틀린 값 저장"과 같은 정의)
    - saved_without_asking: 추가 확인 문장인데 묻지 않고 저장
    """
    scored = score_records(records, predictions)
    per_id = {r["id"]: r for r in scored["records"]}
    status = {i: (p.get("validation_response") or {}).get("status_code") for i, p in predictions.items()}
    wrong_normal = sorted(r["id"] for r in records if r.get("expected_outcome") == NORMAL_OUTCOME
                          and status[r["id"]] == 200 and not per_id[r["id"]]["transaction_exact"])
    not_asked = sorted(r["id"] for r in records if r.get("expected_outcome") != NORMAL_OUTCOME
                       and status[r["id"]] == 200)
    asked = sorted(i for i in per_id if status[i] == 400)
    return {
        "transaction_exact": scored["transaction_exact"],
        "amount_exact": scored["amount_exact"]["overall"],
        "required_f1": scored["required_fields_micro"]["f1"],
        "wrong_saved_normal": {"count": len(wrong_normal), "ids": wrong_normal},
        "saved_without_asking": {"count": len(not_asked), "ids": not_asked},
        "asked_400": {"count": len(asked), "ids": asked},
        "credit_wrongly_confirmed": scored["credit_wrongly_confirmed"],
        "credit_flip_saved": scored["credit_flip_saved"],
        "generation_failed": scored["generation"]["failed"],
        "_records": per_id,
    }


def rule_effects(records: list[dict], replayed: dict[str, dict], a: dict, b: dict) -> dict[str, dict[str, Any]]:
    """후처리 규칙(applied 라벨)별: 적용 문장 수, 그 문장의 거래 판정 A→B 변화. 한 문장에 규칙이 둘이면 둘 다 센다."""
    outcome = {r["id"]: r.get("expected_outcome") for r in records}
    effects: dict[str, dict[str, Any]] = {}
    for record_id, item in replayed.items():
        for rule in item["applied"]:
            e = effects.setdefault(rule, {"applied": 0, "wrong_to_right": [], "right_to_wrong": [], "unchanged": 0,
                                          "confirmation_sentences": [], "amount_wrong_to_right": [],
                                          "amount_right_to_wrong": []})
            e["applied"] += 1
            # 금액 Exact 변화는 추가 확인 문장까지 본다 (정답 총액이 있는 문장만)
            amount_before = a["_records"][record_id]["amount_exact"]
            amount_after = b["_records"][record_id]["amount_exact"]
            if amount_before is False and amount_after is True:
                e["amount_wrong_to_right"].append(record_id)
            elif amount_before is True and amount_after is False:
                e["amount_right_to_wrong"].append(record_id)
            if outcome[record_id] != NORMAL_OUTCOME:  # 추가 확인 문장은 A·B에 거래 판정이 없다
                e["confirmation_sentences"].append(record_id)
                continue
            before, after = a["_records"][record_id]["transaction_exact"], b["_records"][record_id]["transaction_exact"]
            key = "wrong_to_right" if not before and after else "right_to_wrong" if before and not after else None
            if key:
                e[key].append(record_id)
            else:
                e["unchanged"] += 1
    return dict(sorted(effects.items()))


def _value_changes(raw: dict | None, pp: dict | None) -> list[str]:
    """후처리가 바꾼 값. 숫자·외상 칸만 적는다 (이름·단위는 문장 조각이라 넣지 않는다)."""
    if raw is None or pp is None:
        return []
    pairs = [(key, raw.get(key), pp.get(key)) for key in ("total_amount", "credit")]
    for index, (before, after) in enumerate(zip(raw.get("items") or [], pp.get("items") or [], strict=False)):
        pairs.extend((f"items[{index}].{key}", before.get(key), after.get(key)) for key in CASE_ITEM_FIELDS)
    return [f"{key}: {before} → {after}" for key, before, after in pairs if before != after]


def case_facts(records: list[dict], replayed: dict[str, dict], rows: dict[str, dict], points: dict[str, dict],
               effects: dict[str, dict]) -> dict[str, dict[str, Any]]:
    """따로 볼 문장: (C)까지 남은 외상 잘못 확정, 후처리로 거래·금액이 맞음→틀림. 원문 없이 ID·규칙·바뀐 값만."""
    reasons: dict[str, list[str]] = {}
    for record_id in points["C_validated"]["credit_wrongly_confirmed"].get("ids", []):
        reasons.setdefault(record_id, []).append("(C)까지 외상 잘못 확정")
    for effect in effects.values():
        for record_id in effect["right_to_wrong"]:
            reasons.setdefault(record_id, []).append("후처리로 거래 맞음→틀림")
        for record_id in effect["amount_right_to_wrong"]:
            reasons.setdefault(record_id, []).append("후처리로 금액 맞음→틀림")
    by_id = {r["id"]: r for r in records}
    facts = {}
    for record_id in sorted(reasons):
        item, response = replayed[record_id], replayed[record_id]["validation_response"]
        facts[record_id] = {
            "reasons": sorted(set(reasons[record_id])),
            "expected_outcome": by_id[record_id].get("expected_outcome"),
            "required_clarifications": by_id[record_id].get("required_clarifications") or [],
            "ai_credit": (item["raw"] or {}).get("credit"), "ai_transaction_type": rows[record_id].get("transaction_type"),
            "applied": item["applied"], "changes": _value_changes(item["raw"], item["pp"]),
            "validation": {"status_code": response["status_code"], "error_code": response["error_code"],
                           "problem_fields": response["problem_fields"]},
        }
    return facts


def timing(replayed: dict[str, dict], rows: dict[str, dict]) -> dict[str, Any]:
    """단계별 시간. 로컬 단계는 문장별 중앙값, AI는 저장값. 합계 = 정규화+AI(wall)+후처리+검증."""
    def dist(values: list[float]) -> dict[str, Any]:
        present = [v for v in values if v is not None]
        return {"p50": _round(rb._nearest_rank(present, 0.5)), "p95": _round(rb._nearest_rank(present, 0.95)),
                "max": _round(max(present)) if present else None, "n": len(present)}
    local = {i: {name: statistics.median(ms) for name, ms in r["step_ms"].items()} for i, r in replayed.items()}
    totals = {i: (sum(local[i].values()) + rows[i]["wall_time_ms"]) if rows[i].get("wall_time_ms") is not None
              else None for i in replayed}
    over = sorted(i for i, t in totals.items() if t is not None and t > sf.DEADLINE_MS)
    total_dist = dist(list(totals.values()))
    return {
        "local_steps_ms": {name: dist([local[i][name] for i in local]) for name, _ in LOCAL_STEPS},
        "ai_processing_time_ms": dist([rows[i].get("processing_time_ms") for i in replayed]),
        "ai_wall_time_ms": dist([rows[i].get("wall_time_ms") for i in replayed]),
        "total_ms": total_dist,
        "ratio_of_deadline": {k: _round(total_dist[k] / sf.DEADLINE_MS, 4) if total_dist[k] is not None else None
                              for k in ("p50", "p95", "max")},
        "over_deadline": {"count": len(over), "total": len(totals), "ids": over,
                          "detail": [{"id": i, "processing_time_ms": rows[i].get("processing_time_ms"),
                                      "wall_time_ms": rows[i].get("wall_time_ms"),
                                      "error_type": (rows[i].get("error") or "").split(":")[-1].strip() or None}
                                     for i in over]},
    }


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def analyze_run(golden_path: Path, predictions_path: Path, repeats: int) -> dict[str, Any]:
    """저장된 실행 하나: 세 지점 지표, 규칙별 효과, 단계별 시간, 정규화가 바뀐 문장(따로 표시)."""
    records, rows, run, split = load_run(golden_path, predictions_path)
    replayed = replay(records, rows, run, repeats)
    inputs = point_rows(replayed)
    points = {name: point_metrics(records, inputs[name]) for name in POINTS}
    mismatch = sorted(i for i, r in replayed.items() if not r["sent_text_match"])
    effects = rule_effects(records, replayed, points["A_raw"], points["B_postprocess"])
    result: dict[str, Any] = {
        "run_id": run["id"], "split": split, "ai_measured_at": run.get("executed_at"),
        "golden_file": _source_path(golden_path), "golden_sha256": run["dataset_sha256"],
        "predictions_file": _source_path(predictions_path), "scoring_version_at_run": run.get("scoring_version"),
        "model": run.get("model"), "prompt_file": run.get("prompt_file"), "run_git_commit": run.get("git_commit"),
        "ko_numeral_normalize": bool(run.get("ko_numeral_normalize", False)),
        "postprocess_version": postprocess_label(True, bool(run.get("ko_numeral_normalize", False))),
        "records": len(records),
        # 지금 정규화로 만든 글자가 AI가 받은 글자와 다른 문장: 후처리·검증은 지금 글자 기준으로 돌렸다
        "sent_text_mismatch_ids": mismatch,
        "points": points,
        "rule_effects": effects,
        "cases": case_facts(records, replayed, rows, points, effects),
        "time": timing(replayed, rows),
    }
    if mismatch:
        kept = [r for r in records if r["id"] not in set(mismatch)]
        result["points_excluding_mismatch"] = {
            name: point_metrics(kept, {r["id"]: inputs[name][r["id"]] for r in kept}) for name in POINTS}
    return result


def _source_path(path: Path) -> str:
    """레포 안이면 레포 기준, 옆 폴더(다른 worktree·handoff)면 그 위 폴더 기준 상대 경로. 개인 절대 경로는 남기지 않는다."""
    resolved = path.resolve()
    for base in (REPO_ROOT, REPO_ROOT.parent):
        try:
            return resolved.relative_to(base).as_posix()
        except ValueError:
            continue
    return path.name


def _strip_private(value: Any) -> Any:
    """summary.json에서 문장별 내부 판정(_records)을 뺀다 (ID 목록은 남는다)."""
    if isinstance(value, dict):
        return {k: _strip_private(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [_strip_private(v) for v in value]
    return value


def _ratio_text(metric: dict[str, Any]) -> str:
    return f"{metric['correct']}/{metric['total']}"


def _joined(values: list[Any]) -> str:
    """회차별 값을 `r1 · r2 · r3`로."""
    return " · ".join(str(v) for v in values)


def _rule_count(effect: dict[str, Any] | None, key: str) -> int:
    if not effect:
        return 0
    return len(effect[key]) if isinstance(effect[key], list) else effect[key]


POINT_ROWS = (
    ("거래 전체 정확도", lambda p: _ratio_text(p["transaction_exact"])),
    ("금액 Exact", lambda p: _ratio_text(p["amount_exact"])),
    ("필수 F1", lambda p: f"{p['required_f1'] * 100:.2f}%"),
    ("정상 문장 틀린 값 저장(200)", lambda p: p["wrong_saved_normal"]["count"]),
    ("추가 확인 문장 묻지 않고 저장(200)", lambda p: p["saved_without_asking"]["count"]),
    ("되묻기(400)", lambda p: p["asked_400"]["count"]),
    ("외상 잘못 확정", lambda p: p["credit_wrongly_confirmed"]["count"]),
)
RULE_COLUMNS = ("applied", "wrong_to_right", "right_to_wrong", "unchanged", "confirmation_sentences",
                "amount_wrong_to_right", "amount_right_to_wrong")
TIME_ROWS = (
    ("정규화", "이 PC 반복 측정", lambda t: t["local_steps_ms"]["normalize"]),
    ("AI (processing_time_ms)", "저장값", lambda t: t["ai_processing_time_ms"]),
    ("AI (wall_time_ms)", "저장값", lambda t: t["ai_wall_time_ms"]),
    ("후처리", "이 PC 반복 측정", lambda t: t["local_steps_ms"]["postprocess"]),
    ("검증", "이 PC 반복 측정", lambda t: t["local_steps_ms"]["validate"]),
    ("합계 (정규화+AI wall+후처리+검증)", "합", lambda t: t["total_ms"]),
)


def _render_cases(runs: list[dict]) -> list[str]:
    """따로 볼 문장: 회차별 자동 사실(규칙·바뀐 값·검증) + CASE_NOTES 설명. 원문은 넣지 않는다."""
    ids = sorted({i for r in runs for i in r["cases"]})
    if not ids:
        return []
    lines = ["", "### 따로 볼 문장 (원인)", ""]
    for record_id in ids:
        seen = [(n, r["cases"][record_id]) for n, r in enumerate(runs, 1) if record_id in r["cases"]]
        head = seen[0][1]
        clarify = ", ".join(head["required_clarifications"]) or "없음"
        lines.append(f"- **{record_id}** ({head['expected_outcome']}, 되물을 칸: {clarify}) — "
                     + " · ".join(f"r{n}: {', '.join(fact['reasons'])}" for n, fact in seen))
        for n, fact in seen:
            v = fact["validation"]
            verdict = f"{v['status_code']} {v['error_code'] or ''} {v['problem_fields'] or ''}".strip()
            lines.append(f"  - r{n}: AI 외상={fact['ai_credit']} · 거래 종류={fact['ai_transaction_type']} · "
                         f"규칙 {', '.join(fact['applied']) or '없음'} · 바뀐 값 {'; '.join(fact['changes']) or '없음'}"
                         f" · 검증 {verdict}")
        if record_id in CASE_NOTES:
            lines.append(f"  - 원인: {CASE_NOTES[record_id]}")
    return lines


def _render_sources(summary: dict[str, Any]) -> list[str]:
    """맨 위 데이터 출처: 정답 파일·sha, 예측 파일·날짜·프롬프트, 채점·후처리 버전."""
    first_runs = [runs[0] for runs in summary["datasets"].values()]
    versions = ", ".join(sorted({r["postprocess_version"] for r in first_runs}))
    lines = ["## 데이터 출처", "",
             (f"- 채점 {summary['scoring_version']} · 후처리 {versions} · 정규화기 {summary['normalizer_version']} "
              f"(모두 지금 main `{summary['git_commit'][:7]}` 코드)"),
             f"- {SOURCE_NOTE}"]
    for name, runs in summary["datasets"].items():
        first = runs[0]
        lines.append(f"- **{name}** 정답 `{first['golden_file']}` (sha256 `{first['golden_sha256']}`)")
        lines += [f"  - 예측 r{n} `{r['predictions_file']}` · {r['ai_measured_at']} · {r['model']} · "
                  f"프롬프트 `{r['prompt_file']}`" for n, r in enumerate(runs, 1)]
    return lines + [""]


def _render_dataset(name: str, runs: list[dict]) -> list[str]:
    """데이터 하나(회차 여러 개)의 기여도·규칙·시간 표."""
    first = runs[0]
    mismatch = first["sent_text_mismatch_ids"]
    lines = [f"## {name} ({first['split']}, {first['records']}건)", "",
             f"- AI 출력: {first['model']} · 프롬프트 `{first['prompt_file']}` · 측정 "
             + _joined([(r["ai_measured_at"] or "?")[:16] for r in runs]),
             f"- 후처리 버전(지금 코드): {first['postprocess_version']}"]
    if mismatch:
        lines.append(f"- ⚠ 정규화가 바뀐 문장 {len(mismatch)}건 ({', '.join(mismatch)}): AI는 옛 글자를 받았고 "
                     "후처리·검증은 지금 글자로 돌렸다. 아래 표는 포함, 제외 수치는 표 밑에 따로.")
    lines += ["", "### 단계별 기여도", "", "| 지표 | (A) AI 원 출력 | (B) +후처리 | (C) +검증 |", "|---|---|---|---|"]
    for label, fn in POINT_ROWS:
        cells = [_joined([fn(r["points"][pt]) for r in runs]) for pt in POINTS]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    if "points_excluding_mismatch" in first:
        parts = []
        for pt in POINTS:
            excluded = [r["points_excluding_mismatch"][pt] for r in runs]
            parts.append(f"{pt[0]}: 거래 {_joined([_ratio_text(e['transaction_exact']) for e in excluded])}, "
                         f"금액 {_joined([_ratio_text(e['amount_exact']) for e in excluded])}")
        lines += ["", f"정규화 바뀐 문장 제외 ({first['records'] - len(mismatch)}건) — " + " / ".join(parts)]
    lines += ["", "### 후처리 규칙별 (applied 라벨, A→B 거래 판정)", "",
              ("| 규칙 | 적용 | 틀림→맞음 | 맞음→틀림 | 변화 없음 | 추가 확인 문장(거래 판정 없음) "
               "| 금액 틀림→맞음 | 금액 맞음→틀림 |"),
              "|---|---|---|---|---|---|---|---|"]
    for rule in sorted({rule for r in runs for rule in r["rule_effects"]}):
        cells = [_joined([_rule_count(r["rule_effects"].get(rule), key) for r in runs]) for key in RULE_COLUMNS]
        lines.append(f"| `{rule}` | " + " | ".join(cells) + " |")
    ids = {key: ", ".join(sorted({i for r in runs for e in r["rule_effects"].values() for i in e[key]})) or "없음"
           for key in RULE_COLUMNS[1:3] + RULE_COLUMNS[5:]}
    lines += ["", "- 한 문장에 규칙이 여럿 붙으면 각 규칙에 같은 변화를 센다.",
              f"- 거래 틀림→맞음 ID: {ids['wrong_to_right']} · 맞음→틀림 ID: {ids['right_to_wrong']}",
              f"- 금액 틀림→맞음 ID: {ids['amount_wrong_to_right']} · 맞음→틀림 ID: {ids['amount_right_to_wrong']}"]
    lines += _render_cases(runs)
    lines += ["", "### 단계별 시간 (ms, 문장별 → p50 / p95)", "",
              "| 단계 | 근거 | p50 | p95 | 최대 |", "|---|---|---|---|---|"]
    for label, src, fn in TIME_ROWS:
        cells = [_joined([fn(r["time"])[k] for r in runs]) for k in ("p50", "p95", "max")]
        lines.append(f"| {label} | {src} | " + " | ".join(cells) + " |")
    ratio = {k: _joined([f"{r['time']['ratio_of_deadline'][k]:.1%}" for r in runs]) for k in ("p50", "p95")}
    over = _joined([f"{r['time']['over_deadline']['count']}/{r['time']['over_deadline']['total']}" for r in runs])
    over_ids = [f"r{n} {d['id']} (wall {d['wall_time_ms']} ms · processing {d['processing_time_ms']} · "
                f"오류 {d['error_type'] or '없음'})"
                for n, r in enumerate(runs, 1) for d in r["time"]["over_deadline"].get("detail", [])]
    if over_ids:
        over += " — " + "; ".join(over_ids)
    lines += ["", f"- 합계 / 5초: p50 {ratio['p50']} · p95 {ratio['p95']}", f"- 5초 초과 문장: {over}",
              ("- AI wall_time_ms는 실행 당시 호출 앞뒤로 잰 값(파서 안의 정규화 포함)이라 합계는 정규화를 약간 "
               "겹쳐 센다 (정규화는 1ms 미만)."), ""]
    return lines


def render_report(summary: dict[str, Any]) -> str:
    """REPORT.md. 숫자는 회차별 `r1 · r2 · r3`로 적는다. 문장 원문은 넣지 않는다."""
    lines = [
        "# 측정 틀 2단계 — 단계별 기여도·시간 (저장된 출력 재생)", "",
        f"- 만든 시각: {summary['generated_at']} · 코드 `{summary['git_commit']}` (dirty={summary['git_dirty']})",
        (f"- 채점 {summary['scoring_version']} · 정규화기 {summary['normalizer_version']} · 로컬 단계 반복 "
         f"{summary['repeats']}회 · {summary['machine']}"),
        "- API·AI 호출 없음 (저장된 답만 재생, 0원). 값은 회차별 `r1 · r2 · r3`.",
        "- 지점: (A) AI 원 출력 · (B) A+후처리 · (C) B+서버 검증. A·B는 검증이 없으니 생성된 후보를 모두 저장으로 본다.",
        ("- 정상 문장 틀린 값 저장 = 정상 문장인데 거래가 틀린 채 200 (#205 run_cascade와 같은 정의) · "
         "추가 확인 문장 묻지 않고 저장 = 되물어야 할 문장이 200."),
        "- 후처리·검증은 지금 코드로 다시 돌린다 (저장된 답에 실행 당시 validation_response가 있어도 쓰지 않는다).", "",
    ]
    lines += _render_sources(summary)
    for name, runs in summary["datasets"].items():
        lines += _render_dataset(name, runs)
    ref = summary["ko_numeral_reference"]
    lines += ["## 정규화 기여도 (참고값 — 인용, 재측정 아님)", "",
              (f"출처: `{ref['source']}` (Dev 60건 · gpt-4o-mini · 대표 실행 · ko-num-1.1 · 2026-10-01). "
               "AI를 다시 불러야 잴 수 있어 이번(0원)에는 재측정하지 않았다."), "",
              "| 지표 | 정규화 off | 정규화 on | 차이 |", "|---|---|---|---|"]
    for key, label in (("transaction_exact", "거래 전체 정확도"), ("amount_exact", "금액 Exact"),
                       ("required_f1", "필수 F1")):
        lines.append(f"| {label} | {ref[key]['off']} | {ref[key]['on']} | {ref[key]['delta']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--set", nargs="+", action="append", required=True, metavar="NAME GOLDEN PRED",
                    help="이름 Golden파일 저장된답(r1 r2 r3 …). 여러 번 줄 수 있다")
    ap.add_argument("--repeats", type=int, default=30, help="로컬 단계 반복 측정 횟수 (문장별 중앙값)")
    args = ap.parse_args()
    if args.repeats < 1:
        raise ValueError("--repeats must be >= 1")
    git = rb._git_state()
    datasets: dict[str, list[dict]] = {}
    for spec in args.set:
        if len(spec) < 3:
            raise ValueError(f"--set needs NAME GOLDEN PRED...: {spec}")
        name, golden, *preds = spec
        datasets[name] = [analyze_run(Path(golden), Path(p), args.repeats) for p in preds]
    summary = {
        "generated_at": datetime.now(rb.KST).isoformat(timespec="seconds"),
        "frame": "steps_report v1 (저장된 출력 재생: 정규화→AI(저장값)→후처리→검증)",
        "git_commit": git["git_commit"], "git_dirty": git["git_dirty"], "scoring_version": SCORING_VERSION,
        "normalizer_version": NORMALIZER_VERSION, "repeats": args.repeats, "deadline_ms": sf.DEADLINE_MS,
        "machine": f"{platform.system()} {platform.release()} · {platform.processor() or platform.machine()} · "
                   f"Python {platform.python_version()}",
        "ko_numeral_reference": KO_NUMERAL_REFERENCE,
        "datasets": datasets,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rb._write_json(args.output_dir / "summary.json", _strip_private(summary))
    (args.output_dir / "REPORT.md").write_text(render_report(summary), encoding="utf-8")
    print(f"[완료] {args.output_dir / 'summary.json'} · {args.output_dir / 'REPORT.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
