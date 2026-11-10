"""Jev 10문장 연결 확인 (PM 10/22). 다음 주 측정 틀에 붙이기 전의 시험이라 멘토링 숫자로는 쓰지 않는다.

- POST https://api.typesafe.ai/v1/systemone, 모델 jev-1.13.0(버전 고정), 키는 환경변수 또는 backend/.env의 TYPESAFE_API_KEY.
  키 값은 화면·결과 파일에 절대 찍지 않는다.
- 문장 10개: seed2-131~136(지원하지 않는 거래 6) + Dev 판매 4(외상 2·현금 2). Dev·Seed v2 밖의 문장은 막는다
  (run_baseline의 데이터 규칙 그대로: Golden Test·Seed v1 거부).
- 질문 2개를 한 호출에: 거래 종류(판매/매입/반품), 외상 여부(외상/결제 완료/모름).
- 문장마다 고른 답·확률·응답 시간(직접 잼)·usage.input_tokens·정답 일치를 남긴다. 비용 = 입력 토큰 × $0.042/1M (출력 무료).
- 실행 전 예상 비용을 먼저 보여 주고, 상한($1.0, configs/eval_prices.yaml과 같은 값)을 넘으면 멈춘다.
  `--dry-run`은 호출 없이 문장 목록과 예상 비용만 본다.

사용 (PowerShell, 레포 루트):
  python eval\\jev_probe.py --seed-file <seed_v2_improve.jsonl 경로> --dry-run
  python eval\\jev_probe.py --seed-file <seed_v2_improve.jsonl 경로> --output-dir <새 폴더>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval import run_baseline as rb  # noqa: E402  (데이터 규칙·로딩 재사용)

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
INPUT_USD_PER_1M = 0.042  # 출력은 무료 (PM 10/22, 공식 문서)
KRW_PER_USD = 1300  # eval_prices.yaml과 같은 기준
COST_CAP_USD = 1.0
TIMEOUT_SECONDS = 30

# 질문 이름·보기 이름은 영문 키, 설명은 한국어 (문서: 설명 없는 보기는 이름만으로 해석)
QUESTIONS: dict[str, Any] = {
    "transaction_type": {
        "type": "choice",
        "instructions": "이 문장은 어떤 거래인가요? 사장님이 물건을 판 것이면 판매, 물건을 산 것이면 매입, 판 물건을 돌려받거나 환불해 준 것이면 반품입니다.",
        "criteria": {
            "sale": "판매: 사장님이 손님·거래처에 물건을 판 거래",
            "purchase": "매입: 사장님이 물건을 사 온 거래",
            "return": "반품: 물건을 돌려받거나 돈을 환불해 준 거래",
        },
    },
    "credit_status": {
        "type": "choice",
        "instructions": "대금을 어떻게 받기로 했나요? 문장에 근거가 없으면 모름을 고르세요.",
        "criteria": {
            "credit": "외상: 대금을 나중에 받기로 했다(달아 둠·미수 포함)",
            "paid": "결제 완료: 현금·카드 등으로 대금을 다 받았다",
            "unknown": "모름: 문장만으로 알 수 없다",
        },
    },
}

# 정답(PM 10/22). 거래 종류: 131·132 매입, 133·134 반품, 135·136 판매(부분 결제).
# 외상 여부: 매입·반품 4개(131~134)는 외상 채점에서 뺀다(None = 채점 안 함, "모름"에 말 안 함과 해당 없음이 섞이므로).
# 135("32000원만 받았어")는 외상이라 말하지 않았으니 모름, 136("나머지는 외상")은 외상.
GOLD: dict[str, tuple[str, str | None]] = {
    "seed2-131": ("purchase", None),
    "seed2-132": ("purchase", None),
    "seed2-133": ("return", None),
    "seed2-134": ("return", None),
    "seed2-135": ("sale", "unknown"),
    "seed2-136": ("sale", "credit"),
    # Dev 판매 4: 외상 2·현금(결제 완료) 2. 모두 정상 구조화 가능한 단순 문장.
    "g-dev-010": ("sale", "credit"),
    "g-dev-027": ("sale", "credit"),
    "g-dev-002": ("sale", "paid"),
    "g-dev-008": ("sale", "paid"),
}
ORDER = list(GOLD)


def load_key() -> str:
    """환경변수 → backend/.env 순서. 값은 어디에도 출력하지 않는다."""
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    env_file = REPO_ROOT / "backend" / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("TYPESAFE_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY가 비어 있다 (backend/.env 또는 환경변수에 넣을 것)")
    return key


def load_sentences(seed_file: Path) -> list[dict[str, Any]]:
    """GOLD의 10문장을 Dev·Seed v2에서 찾는다. 하나라도 없거나 허용 데이터가 아니면 멈춘다."""
    pool = {r["id"]: r for r in rb._load_jsonl(REPO_ROOT / "data" / "golden" / "golden_dev.jsonl")}
    pool.update({r["id"]: r for r in rb._load_jsonl(seed_file)})
    missing = [i for i in ORDER if i not in pool]
    if missing:
        raise ValueError(f"문장을 찾지 못함: {missing}")
    records = [pool[i] for i in ORDER]
    outside = [r["id"] for r in records if not rb._gemini_may_see(r)]
    if outside:  # Dev Golden 또는 Seed v2(seed2-*)만. Seed v1·최종 Golden은 밖으로 못 나간다
        raise ValueError(f"Dev·Seed v2가 아닌 문장은 보내지 않는다: {outside}")
    return records


def request_body(text: str) -> dict[str, Any]:
    return {"model": MODEL, "state": text, "questions": QUESTIONS}


def estimate_cost(records: list[dict[str, Any]]) -> dict[str, float]:
    """상한 추정: 보내는 글자 수 × 1.5 = 토큰 수로 본다(10/22 실측: 한글·JSON은 글자보다 토큰이 약 12% 많았다). 출력은 무료."""
    questions_chars = len(json.dumps(QUESTIONS, ensure_ascii=False))
    tokens = round(1.5 * sum(len(r["utterance_text"]) + questions_chars for r in records))
    usd = tokens / 1_000_000 * INPUT_USD_PER_1M
    return {"input_tokens_upper": tokens, "usd": round(usd, 6), "krw": round(usd * KRW_PER_USD, 2), "cap_usd": COST_CAP_USD}


def call_jev(client: httpx.Client, key: str, text: str) -> tuple[dict[str, Any] | None, int, str | None]:
    """한 문장 호출. (응답 json, 걸린 ms, 오류 문자열). 재시도 없음, 걸린 시간은 직접 잰다."""
    started = time.perf_counter()
    try:
        resp = client.post(URL, json=request_body(text), headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        ms = round((time.perf_counter() - started) * 1000)
        if resp.status_code != 200:
            return None, ms, f"HTTP {resp.status_code}: {resp.text[:200]}"
        return resp.json(), ms, None
    except httpx.HTTPError as exc:
        return None, round((time.perf_counter() - started) * 1000), f"{type(exc).__name__}: {exc}"


def make_row(record: dict[str, Any], data: dict[str, Any] | None, ms: int, error: str | None) -> dict[str, Any]:
    gold_tx, gold_credit = GOLD[record["id"]]
    row: dict[str, Any] = {"id": record["id"], "utterance_text": record["utterance_text"], "wall_time_ms": ms,
                           "error": error, "gold": {"transaction_type": gold_tx, "credit_status": gold_credit}}
    if data is not None:
        answers = data.get("answers", {})
        for name, gold in (("transaction_type", gold_tx), ("credit_status", gold_credit)):
            a = answers.get(name) or {}
            row[name] = {"choice": a.get("choice"), "confidence": a.get("confidence"),
                         "probabilities": a.get("probabilities"),
                         "correct": None if gold is None else a.get("choice") == gold}  # None = 채점 안 함
        row["input_tokens"] = (data.get("usage") or {}).get("input_tokens")
        row["output_tokens"] = (data.get("usage") or {}).get("output_tokens")
        row["response_model"] = data.get("model")
    return row


def run(*, seed_file: Path, output_dir: Path | None, dry_run: bool, client: httpx.Client | None = None,
        key: str | None = None) -> dict[str, Any]:
    records = load_sentences(seed_file)
    est = estimate_cost(records)
    print(f"[예상 비용] Jev {MODEL} {len(records)}문장: 상한 기준 ${est['usd']:.6f} (약 {est['krw']}원), 상한 ${est['cap_usd']}")
    for r in records:
        print(f"  {r['id']}: {r['utterance_text']}  정답={GOLD[r['id']]}")
    if est["usd"] > est["cap_usd"]:
        raise RuntimeError("예상 비용이 상한을 넘어 실행하지 않는다")
    if dry_run:
        return {"dry_run": True, "estimate": est}
    if output_dir is None:
        raise ValueError("--output-dir이 필요하다")
    key = key or load_key()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(rb.KST).strftime("jev-%Y%m%d-%H%M%S-kst")
    rows_path = output_dir / f"{stamp}.rows.jsonl"
    rows_path.open("x", encoding="utf-8").close()
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    rows: list[dict[str, Any]] = []
    try:
        for record in records:
            data, ms, error = call_jev(client, key, record["utterance_text"])
            row = make_row(record, data, ms, error)
            rows.append(row)
            with rows_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            if error and error.startswith("HTTP 429"):
                raise RuntimeError(f"429 한도 초과로 {record['id']}에서 멈춤 (재시도 안 함)")
    finally:
        if own:
            client.close()
    done = [r for r in rows if not r["error"]]
    tokens = sum(r.get("input_tokens") or 0 for r in done)
    usd = tokens / 1_000_000 * INPUT_USD_PER_1M
    times = sorted(r["wall_time_ms"] for r in done)
    summary = {
        "run": {"id": stamp, "executed_at": datetime.now(rb.KST).isoformat(timespec="seconds"), "model": MODEL,
                "git_commit": rb._git_state()["git_commit"], "note": "연결 확인용, 멘토링 숫자로 쓰지 않음"},
        "calls": {"total": len(rows), "ok": len(done), "failed": len(rows) - len(done)},
        "correct": {name: f"{sum(r[name]['correct'] is True for r in done)}/{sum(r[name]['correct'] is not None for r in done)}"
                    for name in ("transaction_type", "credit_status")},
        "response_ms": {"p50": times[len(times) // 2] if times else None, "max": times[-1] if times else None},
        "usage": {"input_tokens": tokens, "usd": round(usd, 8), "krw": round(usd * KRW_PER_USD, 4),
                  "note": "OpenAI식 콘솔 Usage와 대조할 것"},
        "estimate": est,
    }
    rb._write_json(output_dir / f"{stamp}.summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed-file", type=Path, required=True, help="seed_v2_improve.jsonl 경로 (저장소 밖 파일)")
    ap.add_argument("--output-dir", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="호출 없이 문장 목록과 예상 비용만")
    args = ap.parse_args()
    run(seed_file=args.seed_file, output_dir=args.output_dir, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
