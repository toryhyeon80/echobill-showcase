"""Dev 파서 회귀 검사 (PM 10/6): python eval/regression.py [--update-baseline]

기준선(`eval/regression_baseline.json`)에서 맞던 개발용 Golden 문장이 지금 틀리면 실패한다. API 호출 없음.

- Rule 트랙: `eval.score_rule.score_rule`(정규화 on)로 Golden 전체를 운영 Rule 경로로 파싱·채점
- LLM 트랙: 저장된 현재 운영 조건 출력 193115 p0-on r1·r2·r3을 `eval.score.rescore`(후처리 + 서버 검증)로 재채점.
  결과 파일은 만들지 않는다
- 트랙·실행별 정답 ID 4종: 거래(transaction_exact)·금액(amount_exact)·추가 확인 적합(confirmation_appropriate)이
  True인 ID, 외상 잘못 확정(credit_wrongly_confirmed) ID
- 트랙·실행별 외상 반대 저장(credit_flip_saved, PM 10/8) ID — 방향별 cash_to_credit·credit_to_cash

판정
- 실패: 기준선 정답 ID가 지금 정답이 아님(3지표) / 외상 잘못 확정 또는 외상 반대 저장(어느 방향이든)에 기준선에
  없던 ID가 생김 / Golden split이 개발용 Golden이 아님 (Seed·최종 Golden 금지, DL-26)
- 통과 + 경고: 새로 맞힌 ID·없어진 외상 ID → 같은 PR에서 `--update-baseline` 후 기준선 파일 변경을 커밋 (diff에 보이게).
  외상 반대 저장 키가 없는 옛 기준선은 빈 목록으로 읽는다
- LLM 재채점 불가 → "재채점 불가 — 새 측정 필요(비용 승인)" 실패, Rule 트랙은 계속 검사한다:
  정규화 변경(`sent_text_sha256 mismatch`) / 기본 프롬프트 sha256 변경 / `Settings` 기본 `llm_model` 변경

`--update-baseline`은 로컬에서만 쓴다 (CI는 기준선을 쓰지 않음). 깨진 ID가 있으면 쓰지 않고 실패한다.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))
from app.config import Settings  # noqa: E402
from app.services.parse_llm import DEFAULT_SYSTEM_PROMPT_FILE, load_system_prompt  # noqa: E402
from app.services.postprocess import POSTPROCESS_VERSION  # noqa: E402
from eval.score import rescore  # noqa: E402
from eval.score_rule import ALLOWED_SPLIT, DEFAULT_GOLDEN, score_rule  # noqa: E402
from eval.scoring import SCORING_VERSION, score_records  # noqa: E402

BASELINE_FILE = REPO_ROOT / "eval" / "regression_baseline.json"
RESULTS_DIR = REPO_ROOT / "eval" / "results"
LLM_RUNS = {f"llm-r{n}": RESULTS_DIR / f"gpt-4o-mini-20261001-193115-kst-p0-on-r{n}.predictions.jsonl"
            for n in (1, 2, 3)}
CORRECT_METRICS = ("transaction_exact", "amount_exact", "confirmation_appropriate")
CREDIT = "credit_wrongly_confirmed"
LABELS = {"transaction_exact": "거래", "amount_exact": "금액", "confirmation_appropriate": "적합",
          CREDIT: "외상 잘못 확정"}
FLIP = "credit_flip_saved"
FLIP_LABELS = {"cash_to_credit": "현금→외상", "credit_to_cash": "외상→현금"}
UNRESCORABLE = "재채점 불가 — 새 측정 필요(비용 승인)"


class RegressionError(Exception):
    """A track that cannot be judged (wrong split, unrescorable LLM output)."""


def _load_golden(golden_path: Path) -> list[dict[str, Any]]:
    """Golden records; anything but the development Golden is refused (score.py has no such check)."""
    records = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    outside = [r.get("id") for r in records if r.get("split") != ALLOWED_SPLIT]
    if outside:
        raise RegressionError(f"split이 '{ALLOWED_SPLIT}'이 아닌 레코드 (Seed·최종 Golden 금지): {outside[:5]}")
    return records


def track_ids(metrics: dict[str, Any]) -> dict[str, list[str]]:
    """The four ID sets of one track/run from `score_records` output, sorted."""
    ids = {key: sorted(r["id"] for r in metrics["records"] if r.get(key) is True) for key in CORRECT_METRICS}
    ids[CREDIT] = sorted(metrics[CREDIT].get("ids") or [])
    return ids


def rule_metrics(golden_path: Path) -> dict[str, Any]:
    """Rule track (normalization on); its candidates are re-scored to get the per-record view."""
    records = _load_golden(golden_path)
    try:
        result = score_rule(golden_path, ko_normalize=True)
    except ValueError as exc:
        raise RegressionError(str(exc)) from exc
    return score_records(records, result["candidates"])


def llm_metrics(golden_path: Path, predictions_path: Path) -> dict[str, Any]:
    """LLM track for one saved run: post-processing + server validation, as /parse does."""
    _load_golden(golden_path)
    try:
        return rescore(golden_path, predictions_path, postprocess=True, attach_validation=True)["metrics"]
    except ValueError as exc:
        if "sent_text_sha256 mismatch" in str(exc):
            raise RegressionError(f"{UNRESCORABLE}: 정규화 변경 ({exc})") from exc
        raise


def dataset_sha256(golden_path: Path) -> str:
    """LF로 맞춘 바이트의 해시 — Windows 체크아웃(CRLF)에서도 커밋된 파일과 같은 해시 (PM #68)."""
    return hashlib.sha256(golden_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def environment(golden_path: Path, prompt_path: Path) -> dict[str, Any]:
    """What the saved LLM outputs depend on, plus the dataset and scorer versions."""
    # Same text the runs hashed (load_system_prompt strips the trailing newline) → comparable to run summaries
    prompt = load_system_prompt(SimpleNamespace(llm_system_prompt_file=str(prompt_path)))
    return {
        "scoring_version": SCORING_VERSION,
        "postprocess_version": POSTPROCESS_VERSION,
        "dataset_sha256": dataset_sha256(golden_path),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        # The code default, not .env / environment variables
        "llm_model": Settings.model_fields["llm_model"].default,
    }


def _summary(metrics: dict[str, Any], ids: dict[str, list[str]]) -> dict[str, Any]:
    totals = {"transaction_exact": metrics["transaction_exact"]["total"],
              "amount_exact": metrics["amount_exact"]["overall"]["total"],
              "confirmation_appropriate": metrics["confirmation_appropriateness"].get("total")}
    summary = {"ids": ids, "counts": {key: len(value) for key, value in ids.items()}, "totals": totals}
    flip = metrics.get(FLIP) or {}
    if "cash_to_credit" in flip:  # absent when no validation response
        summary[FLIP] = {direction: sorted(flip["ids"][direction]) for direction in FLIP_LABELS}
    return summary


def collect(golden_path: Path, llm_runs: dict[str, Path], env: dict[str, Any],
            baseline: dict[str, Any] | None,
            rule: Callable[[Path], dict] = None, llm: Callable[[Path, Path], dict] = None) -> dict[str, Any]:
    """Score every track/run; a track that cannot be judged gets {"error": reason} instead of IDs."""
    rule = rule or rule_metrics
    llm = llm or llm_metrics
    tracks: dict[str, Any] = {}
    try:
        metrics = rule(golden_path)
        tracks["rule"] = _summary(metrics, track_ids(metrics))
    except RegressionError as exc:
        tracks["rule"] = {"error": str(exc)}

    changed = [f"{label} 변경 (기준선 {baseline[key]} → 지금 {env[key]})"
               for key, label in (("prompt_sha256", "기본 프롬프트"), ("llm_model", "기본 모델"))
               if baseline and baseline.get(key) != env[key]]
    for name, path in llm_runs.items():
        if changed:
            tracks[name] = {"error": f"{UNRESCORABLE}: " + " · ".join(changed)}
            continue
        try:
            metrics = llm(golden_path, path)
            tracks[name] = _summary(metrics, track_ids(metrics))
        except RegressionError as exc:
            tracks[name] = {"error": str(exc)}
    return tracks


def judge(baseline_track: dict[str, Any] | None, current: dict[str, Any]) -> tuple[dict, dict]:
    """(broken, fixed) ID lists per metric; broken = newly wrong IDs or new 외상 잘못 확정 / 외상 반대 저장 IDs.

    외상 반대 저장 keys are `credit_flip_saved.<direction>`; a baseline without them reads as empty lists.
    """
    base = (baseline_track or {}).get("ids") or {key: [] for key in LABELS}
    now = current["ids"]
    broken, fixed = {}, {}
    for key in CORRECT_METRICS:
        broken[key] = sorted(set(base[key]) - set(now[key]))
        fixed[key] = sorted(set(now[key]) - set(base[key]))
    broken[CREDIT] = sorted(set(now[CREDIT]) - set(base[CREDIT]))
    fixed[CREDIT] = sorted(set(base[CREDIT]) - set(now[CREDIT]))
    base_flip = (baseline_track or {}).get(FLIP) or {}
    now_flip = current.get(FLIP) or {}
    for direction in FLIP_LABELS:
        key = f"{FLIP}.{direction}"
        broken[key] = sorted(set(now_flip.get(direction, [])) - set(base_flip.get(direction, [])))
        fixed[key] = sorted(set(base_flip.get(direction, [])) - set(now_flip.get(direction, [])))
    return {k: v for k, v in broken.items() if v}, {k: v for k, v in fixed.items() if v}


def _line(name: str, current: dict[str, Any]) -> str:
    counts, totals = current["counts"], current["totals"]
    parts = [f"{LABELS[key]} {counts[key]}/{totals[key]}" for key in CORRECT_METRICS]
    parts.append(f"{LABELS[CREDIT]} {counts[CREDIT]}")
    flip = current.get(FLIP)
    if flip:
        parts.append("외상 반대 저장 " + "·".join(f"{label} {len(flip[d])}" for d, label in FLIP_LABELS.items()))
    return f"[{name}] " + " · ".join(parts)


def report(baseline: dict[str, Any] | None, tracks: dict[str, Any], out=print) -> bool:
    """Print one summary line per track/run plus failure reasons and warnings; True when all pass."""
    ok = True
    for name, current in tracks.items():
        if "error" in current:
            out(f"[{name}] 실패 — {current['error']}")
            ok = False
            continue
        broken, fixed = judge((baseline or {}).get("tracks", {}).get(name), current)
        out(_line(name, current) + (" — 실패" if broken else " — 통과"))
        for key, ids in broken.items():
            if key == CREDIT:
                reason = "외상 잘못 확정 새로 생김"
            elif key.startswith(f"{FLIP}."):
                reason = f"외상 반대 저장({FLIP_LABELS[key.split('.', 1)[1]]}) 새로 생김"
            else:
                reason = f"{LABELS[key]} 새로 틀림"
            out(f"  실패: {reason}: {ids}")
            ok = False
        if fixed:
            ids = sorted({i for v in fixed.values() for i in v})
            out(f"::warning::[{name}] 새로 맞힘: {ids} — 같은 PR에서 --update-baseline 후 커밋")
    return ok


def write_baseline(path: Path, env: dict[str, Any], tracks: dict[str, Any]) -> None:
    """indent=2 · sorted keys · sorted IDs, so a changed ID shows as its own line in the PR diff."""
    data = {**env, "tracks": tracks}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(update_baseline: bool = False, *, golden_path: Path = DEFAULT_GOLDEN, llm_runs: dict[str, Path] = None,
        baseline_path: Path = BASELINE_FILE, prompt_path: Path = DEFAULT_SYSTEM_PROMPT_FILE,
        rule: Callable = None, llm: Callable = None, out=print) -> int:
    """Exit code: 0 pass, 1 fail. With update_baseline the file is written only when nothing broke."""
    llm_runs = LLM_RUNS if llm_runs is None else llm_runs
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else None
    if baseline is None and not update_baseline:
        out(f"실패 — 기준선 없음: {baseline_path.name} (로컬에서 --update-baseline으로 생성)")
        return 1
    env = environment(golden_path, prompt_path)
    tracks = collect(golden_path, llm_runs, env, baseline, rule=rule, llm=llm)
    ok = report(baseline, tracks, out=out)
    if not update_baseline:
        return 0 if ok else 1
    if not ok:
        out("기준선을 쓰지 않음 — 깨진 ID·재채점 불가를 먼저 해결 (회귀를 기준선 갱신으로 덮지 않음)")
        return 1
    write_baseline(baseline_path, env, tracks)
    out(f"기준선 갱신: {baseline_path.relative_to(REPO_ROOT).as_posix() if baseline_path.is_relative_to(REPO_ROOT) else baseline_path.name}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update-baseline", action="store_true",
                        help="rewrite eval/regression_baseline.json from the current result (local only)")
    args = parser.parse_args()
    sys.exit(run(args.update_baseline))


if __name__ == "__main__":
    main()
