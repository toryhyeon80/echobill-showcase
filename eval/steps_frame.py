"""측정 틀 공통 부품 1단계: 운영 방식을 단계로 쪼개 그대로 재현한다 (PM 10/22).

단계: 정규화 → AI 해석 한 번 → 후처리 → 검증. 한 문장씩 직렬로 돌린다.
- run_baseline.py의 것을 그대로 가져다 쓴다: 데이터 막기(Golden Test·Seed v1 거부), 커밋 확인,
  실행 전 예상 비용·상한, 채점(score_records), 모델 연결 코드 4개(openai·gemini·hyperclova·claude).
- 운영 코드(parse_pipeline.py 등)는 건드리지 않는다. 이 파일은 eval/ 안에만 둔다.
- 이 틀의 점수가 run_baseline(+ score.py --postprocess --attach-validation)과 같게 나오면 틀이 맞는 것이고,
  그 점수가 비교 기준선이 된다. `--baseline-score <score.json>`으로 바로 대조한다.

사용 예 (PowerShell, 레포 루트):
  python eval\\steps_frame.py --output-dir <새 폴더> --tag mini llm_model=gpt-4o-mini llm_system_prompt_file=configs/prompts/parse_llm_system_p3.txt llm_transaction_type=true llm_timeout_seconds=30
  (조건은 run_baseline과 같은 KEY=VALUE, 외부 모델은 provider=claude 등)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval import run_baseline as rb  # noqa: E402  (재사용: 가드·비용·git·로딩)
from eval.scoring import SCORING_VERSION, score_records  # noqa: E402
from eval.validation_bridge import validation_response_for  # noqa: E402
from app.config import Settings  # noqa: E402
from app.services.parse_llm import build_llm_input, load_system_prompt, parse_utterance_with_llm  # noqa: E402
from app.services.postprocess import postprocess_candidate, unit_price_rules_active  # noqa: E402

DEADLINE_MS = rb.LLM_DEADLINE_MS  # 5000: 전체(모든 단계 합) 5초 이내


class Ctx(dict):
    """한 문장이 단계를 지나며 들고 다니는 값."""


# ── 단계 (각 단계는 ctx를 갱신한다. 2단계에서 쪼개거나 바꿔도 점수 계산은 그대로) ──────────────────
def step_normalize(ctx: Ctx) -> None:
    ctx["sent_text"] = build_llm_input(ctx["utterance"], ctx["settings"])


def step_llm(ctx: Ctx) -> None:
    """AI 해석 한 번. 원 출력(llm_postprocess=false)을 받는다. 실패하면 candidate=None으로 분모에 남긴다."""
    try:
        parsed = ctx["parser"](ctx["utterance"], settings=ctx["settings"])
    except Exception as exc:  # 한 문장 실패가 전체를 멈추지 않게 (rate limit만 위에서 멈춤)
        ctx["error"] = f"{type(exc).__name__}: {exc}"
        ctx["rate_limited"] = isinstance(exc, ctx["rate_limit_types"])
        ctx["candidate"] = None
        return
    ctx["candidate"] = parsed.candidate.model_dump(mode="json")
    ctx["llm_transaction_type"] = getattr(parsed, "transaction_type", None)
    ctx["model"] = parsed.model
    ctx["response_model"] = getattr(parsed, "response_model", None)
    ctx["prompt_tokens"] = parsed.prompt_tokens
    ctx["completion_tokens"] = parsed.completion_tokens
    ctx["llm_ms"] = parsed.processing_time_ms


def step_postprocess(ctx: Ctx) -> None:
    """후처리(pp). score.py --postprocess와 같은 호출: 보낸 글자(sent_text) 기준."""
    ctx["raw_candidate"] = ctx["candidate"]
    ctx["applied"] = []
    if ctx["candidate"] is None or not ctx["postprocess"]:
        return
    rules = unit_price_rules_active(True, bool(getattr(ctx["settings"], "ko_numeral_normalize", False)))
    ctx["candidate"], ctx["applied"] = postprocess_candidate(
        ctx["candidate"], ctx["sent_text"], unit_price_rules=rules)


def step_validate(ctx: Ctx) -> None:
    """검증(400 = 되묻기). 후처리된 후보에 대해, /parse와 같은 순서로."""
    ctx["validation_response"] = validation_response_for(
        ctx["candidate"], ctx["utterance"], ctx["validation_settings"], ctx.get("llm_transaction_type"))


STEPS: list[tuple[str, Callable[[Ctx], None]]] = [
    ("normalize", step_normalize),
    ("llm", step_llm),
    ("postprocess", step_postprocess),
    ("validate", step_validate),
]


def run_one(utterance: str, ctx_base: dict[str, Any], steps=STEPS) -> Ctx:
    """한 문장을 단계대로 직렬 실행하고 단계별 시간(ms)을 잰다. 호출 사이 대기는 시간에 넣지 않는다."""
    ctx = Ctx(ctx_base, utterance=utterance, step_ms={}, error=None, rate_limited=False)
    for name, fn in steps:
        started = time.perf_counter()
        fn(ctx)
        ctx["step_ms"][name] = round((time.perf_counter() - started) * 1000, 2)
    ctx["total_ms"] = round(sum(ctx["step_ms"].values()))
    return ctx


def _row(record: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    row = {
        "id": record["id"], "candidate": ctx["candidate"], "error": ctx["error"],
        "model": ctx.get("model", ctx["settings"].llm_model),
        "processing_time_ms": ctx.get("llm_ms"), "prompt_tokens": ctx.get("prompt_tokens"),
        "completion_tokens": ctx.get("completion_tokens"), "response_model": ctx.get("response_model"),
        "sent_text_sha256": hashlib.sha256(ctx["sent_text"].encode("utf-8")).hexdigest(),
        "wall_time_ms": ctx["total_ms"], "step_ms": ctx["step_ms"], "postprocess_applied": ctx["applied"],
        "validation_response": ctx["validation_response"],
    }
    if ctx.get("llm_transaction_type") is not None:
        row["transaction_type"] = ctx["llm_transaction_type"]
    return row


def run_frame(*, golden_path: Path, output_dir: Path, settings: Any, provider: str = "openai",
              parser: Callable[..., Any] | None = None, run_id: str | None = None, tag: str = "",
              postprocess: bool = True, allow_dirty: bool = False, min_interval_seconds: float = 0.0,
              steps=STEPS, check_cost: bool = True) -> dict[str, Any]:
    """한 파일을 직렬로 돌려 predictions·summary를 쓴다. 유료 호출 전에 데이터 가드·커밋·비용 상한을 먼저 본다."""
    if provider not in rb.PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; expected one of {sorted(rb.PROVIDERS)}")
    records = rb._load_jsonl(golden_path)
    split = rb._require_measurable_split(records)
    if provider in rb.EXTERNAL_PROVIDERS:
        rb._require_gemini_dev_only(records)
    score_records(records, {})  # 중복 ID는 유료 호출 전에 거부
    git = rb._git_state()
    rb._require_committed_code(git, allow_dirty)
    # 모델 호출 단계는 항상 원 출력. 후처리는 postprocess 단계가 따로 한다 (이중 적용 방지)
    llm_settings = settings.model_copy(update={"llm_postprocess": False}) if hasattr(settings, "model_copy") else settings
    if check_cost:
        est = rb.estimate_cost(records=records, settings_by_tag={tag or "run": llm_settings}, repeat=1,
                               prices=rb.load_prices())
        print(f"[예상 비용] {settings.llm_model} 1회: ${est['total_usd']:.4f} (상한 ${est['cap_usd']})")
        if est["total_usd"] > est["cap_usd"]:
            raise RuntimeError(f"estimated cost ${est['total_usd']:.4f} exceeds cap ${est['cap_usd']}; not run")
    if parser is None:
        parser = {"gemini": rb.parse_utterance_with_gemini, "hyperclova": rb.parse_utterance_with_hyperclova,
                  "claude": rb.parse_utterance_with_claude}.get(provider, parse_utterance_with_llm)
    validation_settings = settings if isinstance(settings, Settings) else Settings(
        _env_file=None, app_env="development",
        ko_numeral_normalize=bool(getattr(settings, "ko_numeral_normalize", False)))
    run_id = run_id or rb._default_id(tag or "frame")
    output_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = output_dir / f"{run_id}.predictions.jsonl"
    predictions_path.open("x", encoding="utf-8").close()
    interval = min_interval_seconds if provider in rb.EXTERNAL_PROVIDERS else 0.0
    rate_types = (rb.GeminiRateLimitError, rb.HyperClovaRateLimitError, rb.ClaudeRateLimitError)
    base = {"settings": llm_settings, "validation_settings": validation_settings, "parser": parser,
            "postprocess": postprocess, "rate_limit_types": rate_types}

    final_rows: dict[str, dict] = {}
    raw_rows: dict[str, dict] = {}
    totals: list[int] = []
    last_call: float | None = None
    for record in records:
        if interval and last_call is not None:
            wait = interval - (time.perf_counter() - last_call)
            if wait > 0:
                time.sleep(wait)
        last_call = time.perf_counter()
        ctx = run_one(record["utterance_text"], base, steps)
        row = _row(record, ctx)
        with predictions_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        final_rows[record["id"]] = row
        raw = {**row, "candidate": ctx["raw_candidate"]}
        raw.pop("validation_response", None)
        raw_rows[record["id"]] = raw
        totals.append(ctx["total_ms"])
        if ctx["rate_limited"]:
            raise RuntimeError(f"run {run_id} stopped: 429 rate limited at record {record['id']}")

    summary = {
        "run": {"id": run_id, "executed_at": datetime.now(rb.KST).isoformat(timespec="seconds"),
                "frame": "steps_frame v1 (정규화→AI 해석 1회→후처리→검증, 직렬)",
                "steps": [name for name, _ in steps], "model": settings.llm_model, "provider": provider,
                "split": split, "postprocess": postprocess, "scoring_version": SCORING_VERSION,
                # score.py·suggest_compare.py가 읽는 칸: --no-postprocess로 돌린 원 출력은 오프라인 후처리를 다시 할 수 있다
                "llm_postprocess": False, "llm_transaction_type": bool(getattr(settings, "llm_transaction_type", False)),
                "ko_numeral_normalize": bool(getattr(settings, "ko_numeral_normalize", False)),
                "golden_file": rb._display_path(golden_path),
                "dataset_sha256": hashlib.sha256(golden_path.read_bytes()).hexdigest(),
                "prompt_sha256": hashlib.sha256(load_system_prompt(settings).encode("utf-8")).hexdigest(),
                "git_commit": git["git_commit"], "git_dirty": git["git_dirty"]},
        "time": {"deadline_ms": DEADLINE_MS, "over_deadline": sum(v > DEADLINE_MS for v in totals),
                 "total": len(totals), "p50": rb._nearest_rank(totals, 0.5), "p95": rb._nearest_rank(totals, 0.95),
                 "step_ms_mean": {name: round(sum(r["step_ms"][name] for r in final_rows.values()) / len(final_rows), 2)
                                  for name, _ in steps}},
        # 단계별 필드만 채점: llm_raw = 원 출력, final = 후처리+검증 뒤 (기준선과 대조하는 값)
        "metrics_by_step": {"llm_raw": score_records(records, raw_rows), "final": score_records(records, final_rows)},
    }
    rb._write_json(output_dir / f"{run_id}.summary.json", summary)
    return summary


def replay_parser(predictions_path: Path) -> Callable[..., Any]:
    """저장된 AI 답(predictions)을 그대로 돌려주는 가짜 모델. API 호출 없음(0원).

    같은 글자를 보냈는지(sent_text_sha256)까지 맞춰 보고, 어긋나면 멈춘다 (score.py와 같은 안전장치).
    """
    from app.services.parse_llm import LedgerExtraction, LLMParseError, LLMParseResult

    rows = [json.loads(line) for line in predictions_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # 같은 글자를 보낸 행이 둘 이상이면 파일 순서대로 하나씩 꺼낸다 (Golden과 같은 순서로 돌기 때문)
    from collections import defaultdict, deque

    by_sha: dict[str, deque] = defaultdict(deque)
    for row in rows:
        by_sha[row["sent_text_sha256"]].append(row)

    def parse(utterance: str, *, settings: Any) -> Any:
        sha = hashlib.sha256(build_llm_input(utterance, settings).encode("utf-8")).hexdigest()
        if not by_sha.get(sha):
            raise ValueError("저장된 답에 없는 문장이거나 보낸 글자가 다르다 (정규화 설정 확인)")
        row = by_sha[sha].popleft()
        if row.get("error") or not isinstance(row.get("candidate"), dict):
            raise LLMParseError(f"saved failure: {row.get('error')}")
        return LLMParseResult(
            candidate=LedgerExtraction.model_validate(row["candidate"]), model=row.get("model") or settings.llm_model,
            processing_time_ms=row.get("processing_time_ms") or 0, prompt_tokens=row.get("prompt_tokens"),
            completion_tokens=row.get("completion_tokens"), response_model=row.get("response_model"),
            transaction_type=row.get("transaction_type"))

    return parse


def diff_against_baseline(frame_metrics: dict[str, Any], baseline_metrics: dict[str, Any]) -> list[str]:
    """기준선(score.py 출력의 metrics)과 숫자 하나하나 대조. 다른 항목 경로를 돌려준다. 빈 목록 = 같음."""
    def flat(value: Any, prefix: str = "") -> dict[str, Any]:
        if isinstance(value, dict):
            return {k: v for key, item in value.items() for k, v in flat(item, f"{prefix}{key}.").items()}
        if isinstance(value, list):
            return {k: v for i, item in enumerate(value) for k, v in flat(item, f"{prefix}{i}.").items()}
        return {prefix.rstrip("."): value}
    left, right = flat(frame_metrics), flat(baseline_metrics)
    return sorted(k for k in set(left) | set(right) if left.get(k) != right.get(k))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--golden", type=Path, default=REPO_ROOT / "data" / "golden" / "golden_dev.jsonl")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--tag", default="")
    ap.add_argument("--no-postprocess", action="store_true", help="후처리 단계 끄기 (원 출력 점수 보기)")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--min-interval-seconds", type=float, default=0.0)
    ap.add_argument("--baseline-score", type=Path,
                    help="score.py --postprocess --attach-validation 결과 json. 있으면 final 점수와 대조")
    ap.add_argument("--replay-predictions", type=Path,
                    help="저장된 AI 답을 그대로 넣고 후처리→검증→채점만 새 틀로 돌린다 (API 호출 없음, 0원)")
    ap.add_argument("overrides", nargs="*", help="KEY=VALUE (provider=claude llm_model=... 같은 run_baseline 조건)")
    args = ap.parse_args()
    _, ov = rb._parse_condition([args.tag or "run", *args.overrides])
    provider = ov.pop("provider", "openai")
    if provider in rb.EXTERNAL_PROVIDERS:
        ov["llm_postprocess"] = False
    unknown = sorted(set(ov) - set(Settings.model_fields))
    if unknown:
        raise ValueError(f"unknown setting keys: {unknown}")
    settings = Settings(_env_file=rb.BACKEND_ROOT / ".env", **ov)
    summary = run_frame(golden_path=args.golden, output_dir=args.output_dir, settings=settings, provider=provider,
                        tag=args.tag, postprocess=not args.no_postprocess, allow_dirty=args.allow_dirty,
                        min_interval_seconds=args.min_interval_seconds,
                        parser=replay_parser(args.replay_predictions) if args.replay_predictions else None,
                        check_cost=not args.replay_predictions)
    if args.baseline_score:
        base = json.loads(args.baseline_score.read_text(encoding="utf-8"))["metrics"]
        keys = [k for k in ("transaction_exact", "amount_exact", "required_fields_micro") if k in base]
        diff = diff_against_baseline({k: summary["metrics_by_step"]["final"][k] for k in keys},
                                     {k: base[k] for k in keys})
        print("[기준선 대조] " + ("같음 — 틀이 맞음" if not diff else f"다름: {diff}"))
    print(json.dumps({k: summary[k] for k in ("run", "time")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
