# #205 run_cascade.measure_run 을 감싸 토큰·비용·외상 잘못 확정·5초 초과를 기록한 측정 기록용 스크립트 (11/3, PM 승인 $0.40)
"""One run_cascade pass with token/cost, credit-wrongly-confirmed (off/on) and >5s recorded. Repo code unchanged.

argv: repo_root golden out_json
"""
import json
import sys
import time
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(root), str(root / "backend")]
from eval import run_cascade as rc  # noqa: E402
from app.services import cascade  # noqa: E402
from app.services.parse_llm import parse_utterance_with_llm  # noqa: E402
from eval.scoring import score_records  # noqa: E402

PRICE = {"mini": (0.15, 0.60), "second": (0.25, 1.50)}  # configs/eval_prices.yaml, USD / 1M tokens
golden, out = Path(sys.argv[2]), Path(sys.argv[3])
assert not out.exists(), out
records = rc.rb._load_jsonl(golden)
rc.require_data(records, False)
settings = rc.production_settings()
assert settings.llm_system_prompt_file.endswith("parse_llm_system_p3.txt")
ids = iter(r["id"] for r in records)
state = {"cur": None}
per: dict[str, dict] = {}

def parser(utterance, settings):
    state["cur"] = rid = next(ids)
    per[rid] = {"mini_in": None, "mini_out": None, "mini_error": None, "second": [], "validation": [], "tt": None}
    started = time.perf_counter()
    try:
        result = parse_utterance_with_llm(utterance, settings=settings)
    except Exception as exc:
        per[rid]["mini_error"] = type(exc).__name__
        per[rid]["mini_wall_ms"] = round((time.perf_counter() - started) * 1000)
        raise
    per[rid].update(mini_in=result.prompt_tokens, mini_out=result.completion_tokens, tt=result.transaction_type,
                    mini_wall_ms=round((time.perf_counter() - started) * 1000))
    return result

original_call = cascade._call_gemini
def call_gemini(body, s, client):
    rid = state["cur"]
    entry = {"in": None, "out": None}
    per[rid]["second"].append(entry)
    payload = original_call(body, s, client)
    usage = payload.get("usageMetadata") or {}
    entry.update(**{"in": usage.get("promptTokenCount"),
                    "out": (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0)})
    return payload
cascade._call_gemini = call_gemini

original_validation = rc.validation_response_for
def validation(candidate, utterance, s, *args):
    response = original_validation(candidate, utterance, s, *args)
    per[state["cur"]]["validation"].append({"candidate": candidate, "response": response})
    return response
rc.validation_response_for = validation

started = time.perf_counter()
result = rc.measure_run(records, settings, parser=parser)
result["wall_seconds"] = round(time.perf_counter() - started, 1)
time.sleep(cascade_wait := settings.cascade_timeout_seconds)  # late Gemini replies (deadline passed) still log usage

def preds(index):
    out_ = {}
    for rid, p in per.items():
        v = p["validation"][min(index, len(p["validation"]) - 1)]
        out_[rid] = {"candidate": v["candidate"], "error": p["mini_error"], "validation_response": v["response"],
                     "transaction_type": p["tt"]}
    return out_

credit = {}
for name, index in (("off", 0), ("on", 1)):
    metrics = score_records(records, preds(index))
    credit[name] = sorted(metrics["credit_wrongly_confirmed"].get("ids") or [])
rows = {r["id"]: r for r in result["rows"]}
over5 = sorted(rid for rid, p in per.items()
               if p["mini_error"] or (rows[rid].get("mini_ms") or 0) + (rows[rid].get("second_ms") or 0) > 5000)
tok = {"mini_in": sum(p["mini_in"] or 0 for p in per.values()), "mini_out": sum(p["mini_out"] or 0 for p in per.values()),
       "second_in": sum(e["in"] or 0 for p in per.values() for e in p["second"]),
       "second_out": sum(e["out"] or 0 for p in per.values() for e in p["second"]),
       "mini_calls_no_usage": sum(1 for p in per.values() if p["mini_in"] is None),
       "second_calls_no_usage": sum(1 for p in per.values() for e in p["second"] if e["in"] is None)}
cost = {k: round((tok[f"{k}_in"] * PRICE[k][0] + tok[f"{k}_out"] * PRICE[k][1]) / 1e6, 6) for k in ("mini", "second")}
cost["total"] = round(cost["mini"] + cost["second"], 6)
result.update(credit_wrong_off=credit["off"], credit_wrong_on=credit["on"], over_5s=over5, tokens=tok, cost_usd=cost,
              mini_error_types={rid: p["mini_error"] for rid, p in per.items() if p["mini_error"]},
              per_sentence={rid: {k: p[k] for k in ("mini_in", "mini_out", "mini_error", "second", "mini_wall_ms")}
                            for rid, p in per.items()},
              seed2_061={"off": per.get("seed2-061", {}).get("validation", [{}])[0].get("response", {}).get("status_code"),
                         "on": (per.get("seed2-061", {}).get("validation") or [{}])[-1].get("response", {}).get("status_code"),
                         "credit_off": (per.get("seed2-061", {}).get("validation") or [{}])[0].get("candidate", {}) and
                                       per["seed2-061"]["validation"][0]["candidate"].get("credit"),
                         "row": rows.get("seed2-061")} if "seed2-061" in per else None,
              base={"commit": "3e69e0e + merge refs/pull/205/head 779fb17 (no commit)", "prompt": settings.llm_system_prompt_file,
                    "second_model": settings.cascade_model, "golden": str(golden)})
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"sentences": result["sentences"], "mini_errors": result["mini_errors"],
                  "wrong_saved": [len(result["wrong_saved_off"]), len(result["wrong_saved_on"])],
                  "new_q": len(result["new_questions"]), "needless": len(result["needless_questions"]),
                  "credit_wrong": [len(credit["off"]), len(credit["on"])], "over5": len(over5),
                  "second_calls": result["second_calls"], "second_failures": len(result["second_failures"]),
                  "tokens": tok, "cost": cost, "wall_s": result["wall_seconds"]}, ensure_ascii=False))
