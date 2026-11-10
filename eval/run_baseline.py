"""Run a reproducible GPT-4o-mini baseline against a Golden JSONL file."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import hashlib
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import Settings  # noqa: E402
from app.services.parse_llm import (  # noqa: E402
    DEFAULT_SYSTEM_PROMPT_FILE,
    build_llm_input,
    load_system_prompt,
    parse_utterance_with_llm,
)
from app.services.normalize_ko import NORMALIZER_VERSION  # noqa: E402
from app.services.postprocess import postprocess_label  # noqa: E402
from eval.providers.claude_parse import (  # noqa: E402
    THINKING_SETTING as CLAUDE_THINKING_SETTING,
    ClaudeRateLimitError,
    parse_utterance_with_claude,
)
from eval.providers.gemini_parse import (  # noqa: E402
    THINKING_SETTING as GEMINI_THINKING_SETTING,
    GeminiRateLimitError,
    parse_utterance_with_gemini,
)
from eval.providers.hyperclova_parse import (  # noqa: E402
    THINKING_SETTING as HYPERCLOVA_THINKING_SETTING,
    HyperClovaRateLimitError,
    parse_utterance_with_hyperclova,
)
from eval.scoring import SCORING_VERSION, score_records  # noqa: E402
from eval.validation_bridge import validation_response_for  # noqa: E402


PRICES_FILE = REPO_ROOT / "configs" / "eval_prices.yaml"
# Measurement-only providers (DL-11); `provider` is a condition key handled here, not a Settings field.
PROVIDERS = frozenset({"openai", "gemini", "hyperclova", "claude"})
# Outside vendors for comparison only: Dev Golden and Seed v2 rows only, raw output, spacing between calls.
# HyperCLOVA X and Claude added PM 10/22 (same data rule as Gemini).
EXTERNAL_PROVIDERS = frozenset({"gemini", "hyperclova", "claude"})
THINKING_SETTINGS = {
    "gemini": GEMINI_THINKING_SETTING,
    "hyperclova": HYPERCLOVA_THINKING_SETTING,
    "claude": CLAUDE_THINKING_SETTING,
}
# Gemini (free tier may use inputs for training) only ever sees sentences the team wrote: the Dev Golden
# (PM 10/7) and, since PM 10/21, Seed v2 (split "Seed" + ID "seed2-"). Seed v1 and the final Golden stay refused.
GEMINI_ALLOWED_SPLIT = "개발용 Golden"
GEMINI_SEED_V2_SPLIT = "Seed"
GEMINI_SEED_V2_ID_PREFIX = "seed2-"
# Upper-bound estimate: one token per character of system prompt + sent text, plus message overhead.
ESTIMATE_OVERHEAD_TOKENS = 32
KST = timezone(timedelta(hours=9), name="KST")
# Data that may be measured. Final Golden (and anything else) stays refused — 불변 규칙 11.
MEASURABLE_SPLITS = frozenset({"개발용 Golden", "Seed"})
LLM_DEADLINE_MS = 5000


def _require_measurable_split(records: list[dict[str, Any]]) -> str:
    """Return the file's single split; refuse final Golden and files mixing Golden with Seed."""
    outside = [record.get("id") for record in records if record.get("split") not in MEASURABLE_SPLITS]
    if outside:
        raise ValueError(
            f"split not allowed for measurement (only {sorted(MEASURABLE_SPLITS)}; "
            f"final Golden is never measured): {outside[:5]}"
        )
    splits = {record["split"] for record in records}
    if len(splits) > 1:
        # One run per data kind, so Golden and Seed numbers are never blended into one score.
        raise ValueError(f"mixed splits in one file: {sorted(splits)}")
    return splits.pop()


def load_prices(path: Path = PRICES_FILE) -> dict[str, Any]:
    """Read configs/eval_prices.yaml: {"cost_cap_usd": float, "models": {model_id: {...}}}."""
    import yaml

    with path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    data.setdefault("models", {})
    return data


def _price_for(model: str, prices: dict[str, Any]) -> dict[str, Any] | None:
    """Input/output $/1M for a model, or None when the file leaves it blank or unlisted."""
    entry = prices["models"].get(model) or {}
    if entry.get("input_usd_per_1m") is None or entry.get("output_usd_per_1m") is None:
        return None
    return entry


def _gemini_may_see(record: dict[str, Any]) -> bool:
    """Dev Golden, or Seed v2 (split "Seed" and an ID that starts with "seed2-")."""
    split = record.get("split")
    if split == GEMINI_ALLOWED_SPLIT:
        return True
    return split == GEMINI_SEED_V2_SPLIT and str(record.get("id", "")).startswith(GEMINI_SEED_V2_ID_PREFIX)


def _require_gemini_dev_only(records: list[dict[str, Any]]) -> None:
    """Refuse Gemini unless every row is Dev Golden or Seed v2 — Seed v1 and final Golden never leave for Google."""
    outside = [record.get("id") for record in records if not _gemini_may_see(record)]
    if outside:
        raise ValueError(
            f"gemini allows only {GEMINI_ALLOWED_SPLIT!r} rows and Seed v2 ({GEMINI_SEED_V2_ID_PREFIX}*) rows; "
            f"refused: {outside[:5]}"
        )


def _display_path(path: Path) -> str:
    """Repository-relative path so results do not leak local directory names."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def _git_state() -> dict[str, Any]:
    """Commit hash and whether tracked files have uncommitted changes; None/None if git fails."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True,
            timeout=10, check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return {"git_commit": None, "git_dirty": None}
    return {"git_commit": commit or None, "git_dirty": bool(status)}


def _require_committed_code(git: dict[str, Any], allow_dirty: bool) -> None:
    """Paid runs must be reproducible from a commit; --allow-dirty overrides (still recorded)."""
    if git["git_dirty"] and not allow_dirty:
        raise RuntimeError(
            "working tree has uncommitted changes; commit first or pass --allow-dirty "
            f"(HEAD {git['git_commit']})"
        )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _nearest_rank(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _percentage(metric: dict[str, Any]) -> str:
    rate = metric.get("rate")
    return "N/A" if rate is None else f"{rate * 100:.2f}%"


def _render_report(summary: dict[str, Any]) -> str:
    run = summary["run"]
    metrics = summary["metrics"]
    measurements = summary["measurements"]
    review = run["review_status"]
    is_provisional = any(status != "검수 완료" for status in review)
    title = "잠정 기준선" if is_provisional else "기준선"
    required = metrics["required_fields_micro"]
    required_f1 = "N/A" if required["f1"] is None else f"{required['f1'] * 100:.2f}%"
    optional = metrics["optional_field_errors"]
    generation_failures = [row["id"] for row in metrics["records"] if not row["generation_success"]]
    transaction_failures = [row["id"] for row in metrics["records"] if row["transaction_exact"] is False]
    amount_failures = [row["id"] for row in metrics["records"] if row["amount_exact"] is False]
    lines = [
        f"# {run['model']} {title}",
        "",
        f"- 실행 ID: `{run['id']}`",
        f"- 실행 시각: `{run['executed_at']}`",
        f"- 모델: `{run['model']}` (provider {run['provider']}, thinking {run['thinking']})",
        f"- 한글 수사 정규화: {'on (' + run['normalizer_version'] + ')' if run['ko_numeral_normalize'] else 'off'}",
        f"- 코드: `{run['git_commit']}`" + (" (커밋 안 된 변경 포함)" if run["git_dirty"] else ""),
        f"- 데이터: `{run['golden_file']}` ({run['split']}, {run['dataset_version']}, {metrics['dataset']['total']}건)",
        f"- 검수 상태: " + ", ".join(f"{key} {value}건" for key, value in review.items()),
        "",
    ]
    if is_provisional:
        lines.extend([
            "> 검수 완료 전 레코드가 포함되어 있어 이 결과는 개발용 잠정 기준선입니다.",
            "",
        ])
    lines.extend([
        "## 핵심 지표",
        "",
        "| 지표 | 결과 | 분모 |",
        "|---|---:|---:|",
        f"| 생성 성공 | {metrics['generation']['succeeded']}건 | {metrics['generation']['total']}건 |",
        f"| 거래 전체 정확도 | {_percentage(metrics['transaction_exact'])} | {metrics['transaction_exact']['total']}건 |",
        f"| 금액 Exact Match | {_percentage(metrics['amount_exact']['overall'])} | {metrics['amount_exact']['overall']['total']}건 |",
        f"| 필수 필드 micro F1 | {required_f1} | TP {required['tp']} / FP {required['fp']} / FN {required['fn']} |",
        "| 추가 확인 적합성 | 측정 불가 | 검증 응답 미구현 |",
        "",
        "## 세부 결과",
        "",
        f"- 발화 명시 총액 Exact: {_percentage(metrics['amount_exact']['stated'])} ({metrics['amount_exact']['stated']['correct']}/{metrics['amount_exact']['stated']['total']})",
        f"- 계산 도출 총액 Exact: {_percentage(metrics['amount_exact']['derived'])} ({metrics['amount_exact']['derived']['correct']}/{metrics['amount_exact']['derived']['total']})",
        f"- 선택 필드 FP: {optional['total_fp']}건 (unit {optional['by_field']['unit']}, unit_price {optional['by_field']['unit_price']}, amount {optional['by_field']['amount']})",
        _credit_flip_line(metrics["credit_flip_saved"]),
        f"- 공백 제거로 통과: 필드 {metrics['space_normalization']['field_matches']}건 / 거래 {metrics['space_normalization']['transaction_matches']}건",
        f"- 지연시간: p50 {measurements['latency_ms']['p50']}ms / p95 {measurements['latency_ms']['p95']}ms (성공 {measurements['latency_ms']['count']}건)",
        f"- 5초 초과 (실패 포함 전체 시간): {measurements['over_deadline']['count']}건 / {measurements['over_deadline']['total']}건",
        f"- 응답 모델: {', '.join(run['response_models']) or '기록 없음'}",
        f"- 토큰: 입력 {measurements['tokens']['prompt']}, 출력 {measurements['tokens']['completion']}, 합계 {measurements['tokens']['total']}",
        _cost_line(summary),
        "",
        "## 실패 사례",
        "",
        f"- 생성 실패: {', '.join(generation_failures) if generation_failures else '없음'}",
        f"- 거래 전체 불일치: {', '.join(transaction_failures) if transaction_failures else '없음'}",
        f"- 총액 불일치: {', '.join(amount_failures) if amount_failures else '없음'}",
        "",
        "## 해석 제한",
        "",
        "- 추가 확인 적합성은 공통 검증 모듈과 400 응답이 구현된 뒤 측정합니다.",
        "- 호출 실패는 분모에서 제외하지 않고 전 필드 실패로 반영했습니다.",
        "- 비용은 관측된 성공 응답 토큰의 추정치입니다. 실패·재시도 비용을 포함한 청구액 상한이 아닙니다.",
        f"- 지연은 LLM 호출 기준이며 STT·Rule을 포함한 서비스 전체 응답시간이 아닙니다. 호출 전체 마감 {run['call_settings']['timeout_seconds']}초, SDK 재시도 {run['call_settings']['max_retries']}회로 실행했습니다.",
        "",
    ])
    return "\n".join(lines)


def _credit_flip_line(flip: dict[str, Any]) -> str:
    """외상 반대 저장 (PM 10/8, 보고만): 정상 건이 200으로 저장됐는데 credit이 정답과 반대인 건, 방향별."""
    if "cash_to_credit" not in flip:
        return "- 외상 반대 저장: 측정 불가 (검증 응답 없음)"
    ids = flip["ids"]
    return (f"- 외상 반대 저장: 현금→외상 {flip['cash_to_credit']}건 ({', '.join(ids['cash_to_credit']) or '없음'}) / "
            f"외상→현금 {flip['credit_to_cash']}건 ({', '.join(ids['credit_to_cash']) or '없음'})")


def _cost_line(summary: dict[str, Any]) -> str:
    price = summary["run"]["price"]
    if price is None or summary["cost_estimate_usd"] is None:
        return "- 추정 API 비용: 단가 없음 (configs/eval_prices.yaml)"
    return (f"- 추정 API 비용: ${summary['cost_estimate_usd']:.6f} "
            f"(입력 ${price['input_usd_per_1m']}/1M, 출력 ${price['output_usd_per_1m']}/1M 기준, "
            f"{price.get('checked_at')} 확인)")


def run_baseline(
    *,
    golden_path: Path,
    output_dir: Path,
    parser: Callable[..., Any] = parse_utterance_with_llm,
    run_id: str | None = None,
    settings: Any = None,
    tag: str = "",
    allow_dirty: bool = False,
    provider: str = "openai",
    min_interval_seconds: float = 0.0,
) -> dict[str, Any]:
    """Call the parser once per record and persist raw and summarized results.

    provider="gemini" refuses anything but Dev Golden and waits `min_interval_seconds` between
    calls (outside the measured latency). A 429 is recorded as a failure and stops the run.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; expected one of {sorted(PROVIDERS)}")
    records = _load_jsonl(golden_path)
    split = _require_measurable_split(records)
    if provider in EXTERNAL_PROVIDERS:
        _require_gemini_dev_only(records)
    score_records(records, {})  # Reject duplicate IDs before any paid call.
    git = _git_state()
    _require_committed_code(git, allow_dirty)
    settings = settings or Settings(_env_file=BACKEND_ROOT / ".env")
    system_prompt = load_system_prompt(settings)
    run_id = run_id or _default_id(tag)
    ko_numeral_normalize = bool(getattr(settings, "ko_numeral_normalize", False))
    # 추가 확인 적합성: the shared BE validator needs full Settings (ranges); tests may pass a lightweight object
    validation_settings = settings if isinstance(settings, Settings) else Settings(
        _env_file=None, app_env="development", ko_numeral_normalize=ko_numeral_normalize)
    output_dir.mkdir(parents=True, exist_ok=True)

    predictions: dict[str, dict[str, Any]] = {}
    latencies: list[int] = []
    wall_times: list[int] = []
    response_models: set[str] = set()
    prompt_tokens = 0
    completion_tokens = 0
    predictions_path = output_dir / f"{run_id}.predictions.jsonl"
    predictions_path.open('x', encoding='utf-8').close()
    interval = min_interval_seconds if provider in EXTERNAL_PROVIDERS else 0.0
    last_call: float | None = None

    for record in records:
        row: dict[str, Any] = {
            "id": record["id"],
            "candidate": None,
            "error": None,
            "model": settings.llm_model,
            "processing_time_ms": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "response_model": None,
            "system_fingerprint": None,
            # Hash of the text actually sent (sanitized and, if on, numeral-normalized).
            "sent_text_sha256": hashlib.sha256(
                build_llm_input(record["utterance_text"], settings).encode("utf-8")
            ).hexdigest(),
        }
        if interval and last_call is not None:
            # Rate-limit spacing happens before `started`, so it never counts as latency.
            wait = interval - (time.perf_counter() - last_call)
            if wait > 0:
                time.sleep(wait)
        started = last_call = time.perf_counter()
        rate_limited = False
        try:
            parsed = parser(record["utterance_text"], settings=settings)
            row.update(
                candidate=parsed.candidate.model_dump(mode="json"),
                model=parsed.model,
                processing_time_ms=parsed.processing_time_ms,
                prompt_tokens=parsed.prompt_tokens,
                completion_tokens=parsed.completion_tokens,
                response_model=getattr(parsed, "response_model", None),
                system_fingerprint=getattr(parsed, "system_fingerprint", None),
            )
            # DL-30: only LLM_TRANSACTION_TYPE runs have it, so flag-off rows keep the old shape
            if getattr(parsed, "transaction_type", None) is not None:
                row["transaction_type"] = parsed.transaction_type
            if row["response_model"]:
                response_models.add(row["response_model"])
            latencies.append(parsed.processing_time_ms)
            prompt_tokens += parsed.prompt_tokens or 0
            completion_tokens += parsed.completion_tokens or 0
        except Exception as exc:  # Each Golden row must remain in the denominator.
            row["error"] = f"{type(exc).__name__}: {exc}"
            rate_limited = isinstance(exc, (GeminiRateLimitError, HyperClovaRateLimitError, ClaudeRateLimitError))
        row['wall_time_ms'] = round((time.perf_counter() - started) * 1000)
        # Validation of the raw output (measurements keep post-processing off; score.py redoes it after --postprocess)
        row["validation_response"] = validation_response_for(
            None if row["error"] else row["candidate"], record["utterance_text"], validation_settings,
            row.get("transaction_type"))
        wall_times.append(row['wall_time_ms'])
        with predictions_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        predictions[record["id"]] = row
        if rate_limited:
            # No retry (PM 10/7): stop here and report; rows so far stay in the predictions file.
            raise RuntimeError(f"run {run_id} stopped: 429 rate limited at record {record['id']}")

    metrics = score_records(records, predictions)
    price = _price_for(settings.llm_model, load_prices())
    review_status = Counter(record.get("review_status", "unknown") for record in records)
    versions = sorted({str(record.get("version", "unknown")) for record in records})
    summary = {
        "run": {
            "id": run_id,
            "executed_at": datetime.now(KST).isoformat(timespec="seconds"),
            "model": settings.llm_model,
            "provider": provider,
            "requested_model": settings.llm_model,
            "thinking": THINKING_SETTINGS.get(provider, "n/a (non-reasoning model)"),
            "price": price,
            "golden_file": _display_path(golden_path),
            "dataset_version": ",".join(versions),
            "scoring_version": SCORING_VERSION,
            "response_models": sorted(response_models),
            "dataset_sha256": hashlib.sha256(golden_path.read_bytes()).hexdigest(),
            "prompt_sha256": hashlib.sha256(system_prompt.encode('utf-8')).hexdigest(),
            "scorer_sha256": hashlib.sha256((REPO_ROOT / 'eval/scoring.py').read_bytes()).hexdigest(),
            "call_settings": {"temperature": getattr(settings, 'llm_temperature', None),
                              "timeout_seconds": getattr(settings, 'llm_timeout_seconds', None),
                              "max_tokens": getattr(settings, 'llm_max_completion_tokens', None),
                              # Record the actual setting so runs with different retry configs stay distinguishable.
                              "max_retries": getattr(settings, 'llm_max_retries', None),
                              "deadline": "whole-call deadline = timeout_seconds (parse_llm)"},
            "prompt_file": _display_path(
                Path(getattr(settings, "llm_system_prompt_file", None) or DEFAULT_SYSTEM_PROMPT_FILE)
            ),
            "review_status": dict(review_status),
            "split": split,
            "ko_numeral_normalize": ko_numeral_normalize,
            "normalizer_version": NORMALIZER_VERSION if ko_numeral_normalize else None,
            "llm_postprocess": bool(getattr(settings, "llm_postprocess", False)),
            # DL-30: the model also answered transaction_type (rows then carry it, PM 10/14)
            "llm_transaction_type": bool(getattr(settings, "llm_transaction_type", False)),
            # PM #37 리뷰: 실제로 적용됐을 때만 버전을 적는다 (정규화 off면 후처리는 적용되지 않음)
            "postprocess_version": postprocess_label(
                bool(getattr(settings, "llm_postprocess", False)), ko_numeral_normalize),
            "git_commit": git["git_commit"],
            "git_dirty": git["git_dirty"],
        },
        "measurements": {
            "latency_ms": {
                "count": len(latencies),
                "p50": _nearest_rank(latencies, 0.50),
                "p95": _nearest_rank(latencies, 0.95),
            },
            "over_deadline": {
                "deadline_ms": LLM_DEADLINE_MS,
                "count": sum(value > LLM_DEADLINE_MS for value in wall_times),
                "total": len(wall_times),
            },
            "tokens": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": prompt_tokens + completion_tokens,
            },
        },
        "cost_estimate_usd": None if price is None else round(
            prompt_tokens / 1_000_000 * price["input_usd_per_1m"]
            + completion_tokens / 1_000_000 * price["output_usd_per_1m"],
            8,
        ),
        "metrics": metrics,
    }

    _write_json(output_dir / f"{run_id}.summary.json", summary)
    (output_dir / f"{run_id}.report.md").write_text(_render_report(summary), encoding="utf-8")
    return summary


def _summarize_group(group_id: str, summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the median run by transaction accuracy and report run-to-run spread (D02 §1-4)."""
    def point(summary: dict[str, Any]) -> dict[str, Any]:
        metrics = summary["metrics"]
        f1 = metrics["required_fields_micro"]["f1"]
        over = summary["measurements"]["over_deadline"]
        return {
            "run_id": summary["run"]["id"],
            "transaction_correct": metrics["transaction_exact"]["correct"],
            "amount_correct": metrics["amount_exact"]["overall"]["correct"],
            "required_f1_pct": None if f1 is None else round(f1 * 100, 4),
            # D02 formula B rejects a model whose over-deadline rate exceeds 5%.
            "over_deadline_rate": round(over["count"] / over["total"], 6) if over["total"] else None,
        }

    # `summaries` is in execution order. Median value first, then the earliest run holding it
    # (PM rule 2026-09-30); comparing run IDs as strings would put "r10" before "r2".
    points = [point(summary) for summary in summaries]
    values = sorted(row["transaction_correct"] for row in points)
    median_value = values[(len(values) - 1) // 2]
    median_run = next(row for row in points if row["transaction_correct"] == median_value)
    spread = {}
    for key in ("transaction_correct", "amount_correct", "required_f1_pct"):
        values = [row[key] for row in points if row[key] is not None]
        spread[key] = round(max(values) - min(values), 4) if values else None
    outcomes: defaultdict[str, set[tuple[Any, Any]]] = defaultdict(set)
    for summary in summaries:
        for row in summary["metrics"]["records"]:
            outcomes[row["id"]].add((row["transaction_exact"], row["amount_exact"]))
    unstable = sorted(record_id for record_id, seen in outcomes.items() if len(seen) > 1)
    return {
        "group_id": group_id,
        "runs": points,
        "median_run_id": median_run["run_id"],
        "spread": spread,
        "unstable_record_ids": unstable,
        "response_models": sorted({m for s in summaries for m in s["run"]["response_models"]}),
    }


def run_repeated(
    *,
    repeat: int,
    golden_path: Path,
    output_dir: Path,
    parser: Callable[..., Any] = parse_utterance_with_llm,
    group_id: str | None = None,
    settings: Any = None,
    tag: str = "",
    allow_dirty: bool = False,
) -> dict[str, Any]:
    """Run the same baseline several times and write a group summary for comparison."""
    if repeat < 2:
        raise ValueError("repeat must be at least 2")
    group_id = group_id or _default_id(tag)
    summaries = [
        run_baseline(
            golden_path=golden_path,
            output_dir=output_dir,
            parser=parser,
            run_id=f"{group_id}-r{index}",
            settings=settings,
            allow_dirty=allow_dirty,
        )
        for index in range(1, repeat + 1)
    ]
    group = _summarize_group(group_id, summaries)
    _write_json(output_dir / f"{group_id}.group.json", group)
    return group


def estimate_cost(
    *,
    records: list[dict[str, Any]],
    settings_by_tag: dict[str, Any],
    repeat: int,
    prices: dict[str, Any],
) -> dict[str, Any]:
    """Upper-bound cost of `repeat` runs per condition, before any paid call.

    Input tokens ≤ characters of system prompt + sent text (+ overhead); output = max tokens per call.
    A model without a price in configs/eval_prices.yaml cannot be bounded, so it is refused.
    """
    by_tag: dict[str, float] = {}
    for tag, settings in settings_by_tag.items():
        price = _price_for(settings.llm_model, prices)
        if price is None:
            raise ValueError(f"no price for {settings.llm_model} in configs/eval_prices.yaml; cannot bound cost")
        prompt_chars = len(load_system_prompt(settings))
        input_tokens = sum(
            prompt_chars + len(build_llm_input(record["utterance_text"], settings)) + ESTIMATE_OVERHEAD_TOKENS
            for record in records
        )
        output_tokens = len(records) * settings.llm_max_completion_tokens
        per_run = (input_tokens * price["input_usd_per_1m"] + output_tokens * price["output_usd_per_1m"]) / 1_000_000
        by_tag[tag] = round(per_run * repeat, 6)
    return {
        "by_tag": by_tag,
        "total_usd": round(sum(by_tag.values()), 6),
        "cap_usd": prices.get("cost_cap_usd", 1.0),
        "basis": "upper bound: input chars as tokens, output = max tokens per call",
    }


# Run-level values that must match across conditions unless a condition overrides the key.
_SHARED_SETTINGS = {"model": "llm_model", "prompt": "llm_system_prompt_file", "temperature": "llm_temperature"}


def run_alternating(
    *,
    conditions: list[tuple[str, dict[str, Any]]],
    repeat: int,
    golden_path: Path,
    output_dir: Path,
    parser: Callable[..., Any] = parse_utterance_with_llm,
    group_prefix: str | None = None,
    allow_dirty: bool = False,
    gemini_parser: Callable[..., Any] = parse_utterance_with_gemini,
    hyperclova_parser: Callable[..., Any] = parse_utterance_with_hyperclova,
    claude_parser: Callable[..., Any] = parse_utterance_with_claude,
    min_interval_seconds: float = 0.0,
) -> dict[str, Any]:
    """Run conditions interleaved (A r1, B r1, A r2, …) in one time window (D02 §1-7).

    Each condition gets Settings built from backend/.env plus its overrides, injected directly
    instead of via environment variables. Writes `{prefix}-{tag}.group.json` per condition and
    `{prefix}.alternating.json` with the actual order and timestamps.

    The measurement-only key `provider` (openai default / gemini) picks the parser per condition.
    With a gemini condition every condition stores raw output (llm_postprocess=false) and the file
    must be all Dev Golden. The estimated cost of all runs is printed first; above the cap nothing runs.
    """
    tags = [tag for tag, _ in conditions]
    if len(conditions) < 2 or len(set(tags)) != len(tags):
        raise ValueError(f"need 2+ conditions with distinct tags, got {tags}")
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    requested = conditions
    providers = {tag: overrides.get("provider", "openai") for tag, overrides in conditions}
    bad = sorted({str(value) for value in providers.values() if value not in PROVIDERS})
    if bad:
        raise ValueError(f"unknown provider {bad}; expected one of {sorted(PROVIDERS)}")
    conditions = [(tag, {k: v for k, v in overrides.items() if k != "provider"}) for tag, overrides in conditions]
    if EXTERNAL_PROVIDERS & set(providers.values()):
        _require_gemini_dev_only(_load_jsonl(golden_path))
        if any(overrides.get("llm_postprocess") is True for _, overrides in conditions):
            raise ValueError("outside-vendor comparison stores raw output; llm_postprocess must stay false")
        # Same offline post-processing for all models (score.py --postprocess), so raw output only.
        conditions = [(tag, {**overrides, "llm_postprocess": False}) for tag, overrides in conditions]
    unknown = sorted({key for _, overrides in conditions for key in overrides} - set(Settings.model_fields))
    if unknown:
        # Settings ignores unknown keys, so a typo would silently run identical conditions.
        raise ValueError(f"unknown setting keys in conditions: {unknown}")
    git = _git_state()
    _require_committed_code(git, allow_dirty)

    settings_by_tag = {
        tag: Settings(_env_file=BACKEND_ROOT / ".env", **overrides) for tag, overrides in conditions
    }
    overridden = {key for _, overrides in conditions for key in overrides}
    shared = {
        tag: {
            "model": settings.llm_model,
            "prompt": hashlib.sha256(load_system_prompt(settings).encode("utf-8")).hexdigest(),
            "temperature": settings.llm_temperature,
        }
        for tag, settings in settings_by_tag.items()
    }
    for name, key in _SHARED_SETTINGS.items():
        if len({values[name] for values in shared.values()}) > 1 and key not in overridden:
            raise ValueError(f"conditions differ in {name} without an explicit {key} override")

    estimate = estimate_cost(
        records=_load_jsonl(golden_path), settings_by_tag=settings_by_tag, repeat=repeat, prices=load_prices())
    for tag in tags:
        print(f"[예상 비용] {tag} ({settings_by_tag[tag].llm_model}) {repeat}회: ${estimate['by_tag'][tag]:.4f}")
    print(f"[예상 비용] 합계: ${estimate['total_usd']:.4f} (상한 ${estimate['cap_usd']})")
    if estimate["total_usd"] > estimate["cap_usd"]:
        raise RuntimeError(f"estimated cost ${estimate['total_usd']:.4f} exceeds cap ${estimate['cap_usd']}; not run")

    group_prefix = group_prefix or _default_id("")
    order: list[dict[str, Any]] = []
    summaries: dict[str, list[dict[str, Any]]] = {tag: [] for tag in tags}
    for index in range(1, repeat + 1):
        for tag in tags:
            run_id = f"{group_prefix}-{tag}-r{index}"
            started_at = datetime.now(KST).isoformat(timespec="seconds")
            summaries[tag].append(run_baseline(
                golden_path=golden_path, output_dir=output_dir,
                parser={"gemini": gemini_parser, "hyperclova": hyperclova_parser,
                        "claude": claude_parser}.get(providers[tag], parser),
                run_id=run_id,
                settings=settings_by_tag[tag], allow_dirty=allow_dirty,
                provider=providers[tag], min_interval_seconds=min_interval_seconds,
            ))
            order.append({
                "run_id": run_id, "tag": tag, "repeat_index": index,
                "started_at": started_at,
                "finished_at": datetime.now(KST).isoformat(timespec="seconds"),
            })

    representatives = {}
    for tag in tags:
        group = _summarize_group(f"{group_prefix}-{tag}", summaries[tag])
        _write_json(output_dir / f"{group_prefix}-{tag}.group.json", group)
        representatives[tag] = group["median_run_id"]
    record = {
        "group_prefix": group_prefix,
        "conditions": [{"tag": tag, "overrides": overrides} for tag, overrides in requested],
        "repeat": repeat,
        "cost_estimate": estimate,
        "order": order,
        "representative_run_ids": representatives,
        "git_commit": git["git_commit"],
        "git_dirty": git["git_dirty"],
    }
    _write_json(output_dir / f"{group_prefix}.alternating.json", record)
    return record


def _parse_condition(values: list[str]) -> tuple[str, dict[str, Any]]:
    """CLI `--condition TAG KEY=VALUE ...` → (tag, overrides); values read as bool/int/float/str."""
    tag, *pairs = values
    overrides: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep or not key:
            raise ValueError(f"condition {tag}: expected KEY=VALUE, got {pair!r}")
        overrides[key] = _parse_value(raw)
    return tag, overrides


def _parse_value(raw: str) -> Any:
    """Interpret a CLI value: true/false, then int, then float, else the string itself."""
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            continue
    return raw


def _default_id(tag: str) -> str:
    """Timestamped run/group ID; a tag such as "ko-on" is appended to tell conditions apart."""
    base = datetime.now(KST).strftime("gpt-4o-mini-%Y%m%d-%H%M%S-kst")
    return f"{base}-{tag}" if tag else base


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--golden",
        type=Path,
        default=REPO_ROOT / "data" / "golden" / "golden_dev.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "results",
    )
    parser.add_argument("--repeat", type=int, default=1, help="run count; 3 for D02 comparisons")
    parser.add_argument("--tag", default="", help='suffix for the run/group ID, e.g. "ko-on"')
    parser.add_argument(
        "--condition", nargs="+", action="append", metavar="TAG KEY=VALUE",
        help="repeatable; 2+ conditions run interleaved, e.g. --condition ko-on ko_numeral_normalize=true",
    )
    parser.add_argument("--allow-dirty", action="store_true",
                        help="run even with uncommitted changes (recorded as git_dirty)")
    parser.add_argument("--min-interval-seconds", type=float, default=0.0,
                        help="gemini·hyperclova·claude conditions only: wait between calls (not counted as latency)")
    args = parser.parse_args()
    if args.condition:
        if len(args.condition) < 2:
            parser.error("--condition needs at least two conditions to alternate")
        result = run_alternating(
            conditions=[_parse_condition(values) for values in args.condition], repeat=args.repeat,
            golden_path=args.golden, output_dir=args.output_dir, allow_dirty=args.allow_dirty,
            min_interval_seconds=args.min_interval_seconds,
        )
    elif args.repeat > 1:
        result = run_repeated(repeat=args.repeat, golden_path=args.golden,
                              output_dir=args.output_dir, tag=args.tag, allow_dirty=args.allow_dirty)
    else:
        result = run_baseline(golden_path=args.golden, output_dir=args.output_dir, tag=args.tag,
                              allow_dirty=args.allow_dirty)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
