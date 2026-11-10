"""DL-11 본 측정 집계 (결과 폴더 전용 스크립트, 운영·측정 도구 아님).

입력: 이 폴더의 원 출력(predictions/summary, llm_postprocess=false) + Rule 채점.
처리: 실행마다 `eval/score.py --postprocess --attach-validation` 결과(rescored-*.summary.json)를 읽어
      모델별 3회 지표, 3회 흔들림, 모델 간 차이, 세 모델 같은 오답, 지어내기, D02 판정식 B 표를 만든다.
출력: aggregate.json (보고서 REPORT_dl11.md의 숫자 출처).
사용: python eval/results/<id>/aggregate_dl11.py  (저장소 루트에서)
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path[:0] = [str(REPO), str(REPO / "backend")]
from eval.run_baseline import _nearest_rank, _summarize_group  # noqa: E402

GOLDEN = REPO / "data" / "golden" / "golden_dev.jsonl"
TAGS = ("mini", "m41", "gem")
# r1 = pass1 (repeat 1), r2·r3 = pass2 (repeat 2) — PM 10/8: r1 뒤 누적 비용 확인 후 r2·r3
RUN_IDS = {tag: [f"{HERE.name}-pass1-{tag}-r1", f"{HERE.name}-pass2-{tag}-r1", f"{HERE.name}-pass2-{tag}-r2"]
           for tag in TAGS}
DEADLINE_MS = 5000


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _rescored_path(run_id: str) -> Path:
    return next(HERE.glob(f"{run_id}.rescored-*.summary.json"))


def _binom_two_sided(b: int, c: int) -> float | None:
    """Exact McNemar p (참고값, D02 §2)."""
    n = b + c
    if n == 0:
        return None
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)


def _core(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    """채점 필수 필드만 (거래처·총액·외상·품목명·수량). 선택 필드·1과 1.0 차이는 답 비교에서 뺀다."""
    if not candidate:
        return None
    def num(value: Any) -> Any:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value
    return {"counterparty": candidate.get("counterparty"), "total_amount": num(candidate.get("total_amount")),
            "credit": candidate.get("credit"),
            "items": sorted(([item.get("name"), num(item.get("qty"))] for item in candidate.get("items") or []), key=str)}


def _answer_key(row: dict[str, Any]) -> str:
    """후처리·검증 뒤 사용자에게 갈 답(필수 필드 기준): 200이면 후보, 400이면 오류 코드·보완 필드·부분 후보."""
    if row.get("error"):
        return "ERROR"
    response = row.get("validation_response") or {}
    if response.get("status_code") == 400:
        return json.dumps({"code": response.get("error_code"), "fields": sorted(response.get("problem_fields") or []),
                           "partial": _core(response.get("partial_candidate"))}, ensure_ascii=False, sort_keys=True)
    return json.dumps(_core(row.get("candidate")), ensure_ascii=False, sort_keys=True)


def _processed_rows(run_id: str, golden: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """score.py와 같은 순서(후처리 → 검증)로 행을 다시 만든다 — 답 비교용."""
    from eval.score import _attach_validation, _postprocess_rows

    pred_path = HERE / f"{run_id}.predictions.jsonl"
    rows = _jsonl(pred_path)
    raw = {row["id"]: row.get("candidate") for row in rows}
    processed, _, _ = _postprocess_rows(golden, rows, pred_path)
    processed = [{k: v for k, v in row.items() if k != "validation_response"}
                 if row.get("candidate") != raw[row["id"]] else row for row in processed]
    return {row["id"]: row for row in _attach_validation(golden, processed, pred_path)}


def _run_point(run_id: str, golden: list[dict[str, Any]]) -> dict[str, Any]:
    summary = json.loads((HERE / f"{run_id}.summary.json").read_text(encoding="utf-8"))
    rescored = json.loads(_rescored_path(run_id).read_text(encoding="utf-8"))
    preds = _jsonl(HERE / f"{run_id}.predictions.jsonl")
    metrics = rescored["metrics"]
    ok = [row["processing_time_ms"] for row in preds if not row.get("error")]
    walls = [row["wall_time_ms"] for row in preds]
    cost = summary["cost_estimate_usd"]
    # 정답 값이 비어 있는 되묻기 칸만 (AMOUNT_MISMATCH처럼 정답 총액이 있는 칸은 지어내기가 아님)
    clar = {record["id"]: {f for f in record.get("required_clarifications") or []
                           if f in ("total_amount", "credit") and record["expected_json"].get(f) is None}
            for record in golden}
    raw = {row["id"]: (row.get("candidate") or {}) for row in preds if not row.get("error")}
    return {
        "run_id": run_id,
        "response_models": summary["run"]["response_models"],
        "executed_at": summary["run"]["executed_at"],
        "transaction_correct": metrics["transaction_exact"]["correct"],
        "transaction_total": metrics["transaction_exact"]["total"],
        "amount_correct": metrics["amount_exact"]["overall"]["correct"],
        "amount_total": metrics["amount_exact"]["overall"]["total"],
        "required_f1_pct": round(metrics["required_fields_micro"]["f1"] * 100, 2),
        "appropriate_correct": metrics["confirmation_appropriateness"]["correct"],
        "appropriate_total": metrics["confirmation_appropriateness"]["total"],
        "credit_wrongly_confirmed_ids": metrics["credit_wrongly_confirmed"]["ids"],
        "normal_rejected_400_ids": metrics["normal_rejected_400"]["ids"],
        "latency_p50": _nearest_rank(ok, 0.50),
        "latency_p95": _nearest_rank(ok, 0.95),
        "latency_max": max(ok) if ok else None,
        "over_deadline": sum(value > DEADLINE_MS for value in walls),
        "over_deadline_rate": round(sum(value > DEADLINE_MS for value in walls) / len(walls), 6),
        # LLM 호출 실패(마감 초과·오류) = 운영에서 Rule로 대체되는 건
        "rule_fallback_ids": sorted(row["id"] for row in preds if row.get("error")),
        "errors": sorted({row["error"] for row in preds if row.get("error")}),
        "tokens_in": summary["measurements"]["tokens"]["prompt"],
        "tokens_out": summary["measurements"]["tokens"]["completion"],
        "cost_usd": cost,
        "cost_per_call_usd": None if cost is None or not ok else cost / len(ok),
        "postprocess_version": rescored["run"]["postprocess_version"],
        "postprocess_fixed": rescored.get("postprocess", {}).get("fixed_ids"),
        "postprocess_broken": rescored.get("postprocess", {}).get("broken_ids"),
        # 지어내기(원 출력 기준): 정답이 '되묻기'인 칸에 모델이 값을 채움
        "raw_invented_total_ids": sorted(i for i, c in raw.items()
                                         if "total_amount" in clar[i] and c.get("total_amount") is not None),
        "raw_invented_credit_ids": sorted(i for i, c in raw.items()
                                          if "credit" in clar[i] and c.get("credit") is not None),
        "records": {row["id"]: (row["transaction_exact"], row["amount_exact"], row.get("confirmation_appropriate"))
                    for row in metrics["records"]},
    }


def _after_pp_invented(rows: dict[str, dict[str, Any]], golden: list[dict[str, Any]]) -> dict[str, list[str]]:
    """후처리·검증 뒤 사용자에게 보이는 값 기준 지어내기 (200 후보 또는 400 partial_candidate)."""
    total, credit = [], []
    for record in golden:
        clar = {f for f in record.get("required_clarifications") or []
                if f in ("total_amount", "credit") and record["expected_json"].get(f) is None}
        row = rows[record["id"]]
        if row.get("error") or not clar:
            continue
        response = row.get("validation_response") or {}
        shown = (response.get("partial_candidate") if response.get("status_code") == 400 else row.get("candidate")) or {}
        if "total_amount" in clar and shown.get("total_amount") is not None:
            total.append(record["id"])
        if "credit" in clar and shown.get("credit") is not None:
            credit.append(record["id"])
    return {"total_amount": total, "credit": credit}


def _tier(delta: float, m: float) -> str:
    return "우위" if delta >= m else "열위" if delta <= -m else "동등"


def main() -> None:
    golden = _jsonl(GOLDEN)
    truth_wrong: dict[str, dict[str, Any]] = {}
    points = {tag: [_run_point(run_id, golden) for run_id in RUN_IDS[tag]] for tag in TAGS}
    rows = {tag: [_processed_rows(run_id, golden) for run_id in RUN_IDS[tag]] for tag in TAGS}

    groups, reps = {}, {}
    for tag in TAGS:
        summaries = [json.loads((HERE / f"{run_id}.summary.json").read_text(encoding="utf-8")) for run_id in RUN_IDS[tag]]
        # 대표 실행은 후처리·검증 뒤 지표로 고른다 (원 출력 summary의 metrics를 재채점 결과로 바꿔서)
        for summary, run_id in zip(summaries, RUN_IDS[tag]):
            summary["metrics"] = json.loads(_rescored_path(run_id).read_text(encoding="utf-8"))["metrics"]
        groups[tag] = _summarize_group(f"{HERE.name}-{tag}", summaries)
        reps[tag] = RUN_IDS[tag].index(groups[tag]["median_run_id"])
        # 3회 흔들림: 거래·금액·적합성 결과가 실행마다 다른 ID
        seen: dict[str, set] = {}
        for point in points[tag]:
            for rid, outcome in point["records"].items():
                seen.setdefault(rid, set()).add(outcome)
        groups[tag]["unstable_ids_with_appropriateness"] = sorted(r for r, s in seen.items() if len(s) > 1)
        answers: dict[str, set] = {}
        for run_rows in rows[tag]:
            for rid, row in run_rows.items():
                answers.setdefault(rid, set()).add(_answer_key(row))
        groups[tag]["answer_changed_ids"] = sorted(r for r, s in answers.items() if len(s) > 1)

    # 모델 간 비교는 응답이 온 실행만 (Gemini 마감 초과는 '다른 답'이 아니라 '응답 없음')
    rep_rows = {tag: rows[tag][reps[tag]] for tag in TAGS}
    rep_points = {tag: points[tag][reps[tag]] for tag in TAGS}
    disagree, same_wrong = [], []
    for record in golden:
        rid = record["id"]
        answered = {tag: [(r[rid], p["records"][rid]) for r, p in zip(rows[tag], points[tag])
                          if not r[rid].get("error")] for tag in TAGS}
        keys = {tag: {_answer_key(row) for row, _ in answered[tag]} for tag in TAGS}
        all_keys = set().union(*keys.values())
        # 대표 실행끼리 답이 다른 ID (마감 초과는 따로 표시)
        rep_keys = {tag: _answer_key(rep_rows[tag][rid]) for tag in TAGS}
        answered_rep = {k for k in rep_keys.values() if k != "ERROR"}
        if len(answered_rep) > 1:
            disagree.append({"id": rid, "correct": {tag: rep_points[tag]["records"][rid] for tag in TAGS},
                             "timeout": [tag for tag in TAGS if rep_keys[tag] == "ERROR"]})
        # 세 모델이 응답한 모든 실행에서 같은 답이고 그 답이 오답인 ID (PM 정답 재검수용)
        if all(answered[tag] for tag in TAGS) and len(all_keys) == 1:
            outcome = answered["mini"][0][1]
            if False in outcome:
                same_wrong.append({"id": rid, "expected_outcome": record["expected_outcome"],
                                   "transaction_exact": outcome[0], "amount_exact": outcome[1],
                                   "confirmation_appropriate": outcome[2],
                                   "answered_runs": {tag: len(answered[tag]) for tag in TAGS},
                                   "answer": all_keys.pop(), "expected_json": record["expected_json"],
                                   "expected_error_code": record.get("expected_error_code"),
                                   "required_clarifications": record.get("required_clarifications")})

    # 완화: 세 모델 모두 응답한 모든 실행에서 오답인 ID와 모델별 답 (정답 재검수 후보 — 답이 완전히 같은지는 표에서 판단)
    all_wrong = []
    for record in golden:
        rid = record["id"]
        per = {tag: [(_answer_key(r[rid]), p["records"][rid]) for r, p in zip(rows[tag], points[tag])
                     if not r[rid].get("error")] for tag in TAGS}
        if all(per[tag] for tag in TAGS) and all(False in o for tag in TAGS for _, o in per[tag]):
            all_wrong.append({"id": rid, "expected_outcome": record["expected_outcome"],
                              "expected_error_code": record.get("expected_error_code"),
                              "required_clarifications": record.get("required_clarifications"),
                              "expected_core": _core(record["expected_json"]),
                              "answers": {tag: sorted({k for k, _ in per[tag]}) for tag in TAGS}})

    invented = {tag: {"raw": {"total_amount": [p["raw_invented_total_ids"] for p in points[tag]],
                              "credit": [p["raw_invented_credit_ids"] for p in points[tag]]},
                      "after_pp": [_after_pp_invented(r, golden) for r in rows[tag]]} for tag in TAGS}

    rule = json.loads((HERE / "rule.summary.json").read_text(encoding="utf-8"))
    rule_m = rule["metrics"]
    d02 = {}
    mini = rep_points["mini"]
    for tag in TAGS:
        p = rep_points[tag]
        spread = groups[tag]["spread"]
        # ① 자격: 거래 80% + 판정식 A(Rule 대비 LLM 우위). Rule은 1회라 편차 0
        m_cnt = max(2, spread["amount_correct"])
        m_f1 = max(2.0, spread["required_f1_pct"])
        a_amount = _tier(p["amount_correct"] - rule_m["amount_exact"]["correct"], m_cnt)
        a_f1 = _tier(p["required_f1_pct"] - round(rule_m["required_fields_micro_f1"] * 100, 2), m_f1)
        a_tx = _tier(p["transaction_correct"] - rule_m["transaction_exact"]["correct"], max(2, spread["transaction_correct"]))
        if a_amount == "열위" or (a_amount == "동등" and a_f1 == "열위"):
            judge_a = "LLM 열위"
        elif (a_amount == "우위" and a_f1 in ("우위", "동등")) or (a_amount == "동등" and a_f1 == "우위"):
            judge_a = "LLM 우위"
        elif a_amount == "우위" and a_f1 == "열위":
            judge_a = {"우위": "LLM 우위", "동등": "동등", "열위": "LLM 열위"}[a_tx]
        else:
            judge_a = "동등"
        app_vs_rule = _tier(p["appropriate_correct"] - rule_m["confirmation_appropriateness"]["correct"], m_cnt)
        if judge_a == "LLM 우위" and app_vs_rule == "열위":
            judge_a = "동등 (적합성 Rule 열위로 하향, §3.3)"
        tx_rate = p["transaction_correct"] / p["transaction_total"]
        row = {"tx_rate": round(tx_rate, 4), "judge_a": judge_a,
               "a_detail": {"amount": a_amount, "f1": a_f1, "transaction": a_tx, "appropriateness": app_vs_rule},
               "qualified": tx_rate >= 0.80 and judge_a == "LLM 우위"}
        if tag != "mini":
            ms = groups["mini"]["spread"]
            m_tx = max(2, spread["transaction_correct"], ms["transaction_correct"])
            m_am = max(2, spread["amount_correct"], ms["amount_correct"])
            app_spread = lambda t: max(x["appropriate_correct"] for x in points[t]) - min(x["appropriate_correct"] for x in points[t])  # noqa: E731
            m_app = max(2, app_spread(tag), app_spread("mini"))
            b = sum(1 for rid, o in p["records"].items() if o[0] is True and mini["records"][rid][0] is False)
            c = sum(1 for rid, o in p["records"].items() if o[0] is False and mini["records"][rid][0] is True)
            reject = {
                "amount_inferior": p["amount_correct"] - mini["amount_correct"] <= -m_am,
                "appropriateness_inferior": p["appropriate_correct"] - mini["appropriate_correct"] <= -m_app,
                "p95_over_4000": p["latency_p95"] is not None and p["latency_p95"] > 4000,
                "over_deadline_over_5pct": p["over_deadline_rate"] > 0.05,
                "credit_wrong_more": len(p["credit_wrongly_confirmed_ids"]) > len(mini["credit_wrongly_confirmed_ids"]),
            }
            tx = _tier(p["transaction_correct"] - mini["transaction_correct"], m_tx)
            p95_ratio = p["latency_p95"] / mini["latency_p95"]
            cost_ratio = p["cost_usd"] / mini["cost_usd"]
            if not row["qualified"]:
                step, verdict = "①", "교체 안 함 (자격 미달)"
            elif any(reject.values()):
                step, verdict = "②", "교체 안 함 (거부 조건: " + ", ".join(k for k, v in reject.items() if v) + ")"
            elif tx == "우위":
                step, verdict = "③", "교체"
            elif tx == "열위":
                step, verdict = "③", "교체 안 함 (정확도 열위)"
            elif (p95_ratio <= 0.80 and cost_ratio <= 1.5) or (cost_ratio <= 0.50 and p95_ratio <= 1.10):
                step, verdict = "④", "동등 — 교체 제안 조건 충족"
            else:
                step, verdict = "④", "동등 — mini 유지"
            row.update({"m": {"transaction": m_tx, "amount": m_am, "appropriateness": m_app},
                        "delta_vs_mini": {"transaction": p["transaction_correct"] - mini["transaction_correct"],
                                          "amount": p["amount_correct"] - mini["amount_correct"],
                                          "appropriateness": p["appropriate_correct"] - mini["appropriate_correct"],
                                          "credit_wrong": len(p["credit_wrongly_confirmed_ids"]) - len(mini["credit_wrongly_confirmed_ids"])},
                        "transaction_tier": tx, "b_cand_only": b, "c_mini_only": c, "mcnemar_p": _binom_two_sided(b, c),
                        "reject": reject, "p95_ratio": round(p95_ratio, 3), "cost_ratio": round(cost_ratio, 3),
                        "step": step, "verdict": verdict})
        d02[tag] = row

    out = {"run_ids": RUN_IDS, "representative": {t: RUN_IDS[t][reps[t]] for t in TAGS},
           "points": {t: [{k: v for k, v in p.items() if k != "records"} for p in points[t]] for t in TAGS},
           "groups": groups, "model_disagree": disagree, "same_answer_wrong": same_wrong, "all_models_wrong": all_wrong,
           "invented": invented, "rule": {"transaction": rule_m["transaction_exact"], "amount": rule_m["amount_exact"],
                                          "f1": rule_m["required_fields_micro_f1"],
                                          "appropriateness": rule_m["confirmation_appropriateness"],
                                          "credit_wrongly_confirmed": rule_m["credit_wrongly_confirmed"]},
           "d02": d02}
    (HERE / "aggregate.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"representative": out["representative"], "d02": d02}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
