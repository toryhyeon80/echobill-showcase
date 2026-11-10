"""STT vendor comparison (PM 10/20, DL-16) — 3 vendors × {그대로, 거래처 힌트, 힌트+목록 대조} on recorded Dev Golden.

    python eval/run_stt_compare.py --audio-dir /abs/rec --manifest /abs/rec/manifest.csv --out-dir /abs/out \
        [--vendors openai clova rtzr] [--conditions plain hint] [--repeat 3] [--limit N] [--delete-after]

Same input rules as run_voice_e2e.py (manifest, recordings and transcripts outside the repo, Dev only,
committed code, cost cap, no sentence content on the console or in eval/results/).

- openai·clova go through the production `transcribe()` (stt.py, STT_PROVIDER) so the measured code is the
  code we would switch on. Both run at once per recording, which also gives the "two STTs at once" wall time
  and the classes 일치·맞음 / 일치·틀림 / 불일치·A만 맞음 / 불일치·B만 맞음 / 불일치·둘 다 틀림.
  Conversion (webm·mp4 → wav) is timed on its own by wrapping `to_wav`; the STT time already includes it.
- rtzr uses the batch API (eval/providers/rtzr_stt.py): accuracy only — its wait is queue time.
- 거래처 힌트: each recording gets an owner list built like stt_hints (≤20 names, ≤120 chars): its own
  Golden counterparty plus other Dev counterparties; every 4th recording leaves its own name out (a new
  counterparty), to see whether list matching turns it into a similar saved name. 힌트+목록 대조 = the hint run with the parsed
  counterparty snapped to the closest list name (no extra STT or LLM call).
- Every STT call appends its billed seconds (CLOVA rounds each call up to 15 s) to <out-dir>/usage.jsonl.
  Before running, the planned billed seconds
  plus that ledger are checked against CLOVA's 1 h/day · 2 h/month and RTZR's 80 min budget.
- 새로 틀림: a recording right in ≥2 of 3 openai·그대로 runs and wrong in ≥2 of 3 runs of another cell.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT, REPO_ROOT / "backend"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import app.services.stt as stt_module
from app.services.audio_convert import WAV_HEADER_BYTES, AudioConvertError, to_wav
from app.services.parse_llm import parse_utterance_with_llm
from app.services.postprocess import CREDIT_SUBSTRINGS, CREDIT_WORDS
from app.services.stt import STTError, transcribe
from app.services.stt_hints import MAX_HINT_CHARS
from dotenv import dotenv_values

from eval import run_baseline as baseline
from eval import run_voice_e2e as voice
from eval import score
from eval.providers.rtzr_stt import RtzrClient
from eval.run_voice_e2e import AUDIO_MIME, VoiceE2EError
from eval.scoring import _match_items, score_records

VENDORS = ("openai", "clova", "rtzr")
SYNC_VENDORS = ("openai", "clova")  # answer in the request — the only ones with a servable response time
STT_CONDITIONS = ("plain", "hint")
CELL_LABELS = {"plain": "그대로", "hint": "거래처 힌트", "hint_list": "힌트+목록 대조"}
HINT_NAME_LIMIT = 20  # STT_COUNTERPARTY_HINT_LIMIT default
REFERENCE_CELL = ("openai", "plain")
# Price keys in configs/eval_prices.yaml stt_models; openai uses settings.stt_model. Blank → cost "미확인".
PRICE_KEYS = {"clova": "clova-speech-short", "rtzr": "rtzr-sommers"}
# Usage budgets (PM 10/20): CLOVA blocks calls past 1 h/day · 2 h/month; RTZR free 600 min, comparison ≤ 80 min.
BUDGET_SECONDS = {"clova": {"day": 3600, "month": 7200}, "rtzr": {"total": 80 * 60}}
# Billing rounds each call up to this unit (PM 10/21: CLOVA short sentence = 15 s) — budgets count billed seconds
BILLING_UNIT_SECONDS = {"clova": 15}
# Minimum billed per call (PM 10/21, RTZR pricing: "파일·세션당 최소 집계 단위는 10초") — longer calls by the second
BILLING_MIN_SECONDS = {"rtzr": 10}
RTZR_POLL_DEADLINE_SECONDS = 40 * 60
RTZR_IN_FLIGHT = 5
WAV_BYTES_PER_SECOND = 16_000 * 2  # to_wav output: 16 kHz, mono, 16-bit
NUMBER_FIELDS = ("qty", "amount", "unit_price", "total_amount")
# PM 10/21: units also scored with spelling variants as one ("키로" = "킬로"); the label stays as written
UNIT_SAME = {"키로": "킬로", "킬로그램": "킬로", "kg": "킬로", "㎏": "킬로"}
CREDIT_EXPRESSIONS = CREDIT_WORDS + CREDIT_SUBSTRINGS  # 외상·미수·달아… — the same words the parser rules read
# Per-call record kept for --merge (times and errors only — no transcript)
CALL_FIELDS = ("golden_id", "speaker", "usage_seconds", "stt_ms", "convert_ms", "together_ms", "error")
UNLISTED_EVERY = 4  # every 4th recording with a counterparty: its own name is left out of the owner list (PM 10/20)

# Conversion ms by the id() of the audio bytes being converted. stt.py runs each vendor call on its own
# deadline worker thread, so a thread-local set inside to_wav is never seen by the caller (10/21 CLOVA 10건)
_convert_ms: dict[int, int] = {}
_convert_lock = threading.Lock()


# ── owner lists · counterparty snapping ───────────────────────────────────────


def _rank(seed: str, name: str) -> str:
    return hashlib.sha256(f"{seed}|{name}".encode()).hexdigest()


def _own_counterparty(golden_id: str, golden: dict[str, dict[str, Any]]) -> str | None:
    return (golden[golden_id]["expected_json"] or {}).get("counterparty")


def unlisted_ids(golden_ids: set[str], golden: dict[str, dict[str, Any]]) -> set[str]:
    """Every UNLISTED_EVERY-th recorded ID with a counterparty (sorted) — a new counterparty the owner never saved."""
    with_name = sorted(gid for gid in golden_ids if _own_counterparty(gid, golden))
    return {gid for index, gid in enumerate(with_name) if index % UNLISTED_EVERY == UNLISTED_EVERY - 1}


def owner_list(golden_id: str, golden: dict[str, dict[str, Any]], *, include_own: bool = True) -> list[str]:
    """
    This recording's simulated owner list, capped like stt_hints: its own counterparty (unless
    `include_own` is False — a new counterparty) + other Dev names.
    """
    own = _own_counterparty(golden_id, golden)
    pool = sorted({_own_counterparty(gid, golden) for gid in golden} - {None, own})
    ordered = ([own] if own and include_own else []) + sorted(pool, key=lambda name: _rank(golden_id, name))
    kept: list[str] = []
    used = 0
    for name in ordered[:HINT_NAME_LIMIT]:
        cost = len(name) + (2 if kept else 0)
        if used + cost > MAX_HINT_CHARS:
            continue  # same as stt_hints after #168: a long name skips itself only
        kept.append(name)
        used += cost
    return sorted(kept, key=lambda name: _rank(golden_id + "#order", name))  # own name not always first


def snap_counterparty(value: Any, names: list[str]) -> Any:
    """Closest list name when within 1 edit (≤4 chars) or 2 edits (longer) and unique; otherwise unchanged."""
    if not isinstance(value, str) or not value.strip():
        return value
    spoken = "".join(value.split())
    distances = sorted((voice.edit_distance(spoken, "".join(name.split())), name) for name in names)
    if not distances:
        return value
    best, name = distances[0]
    allowed = 1 if len(spoken) <= 4 else 2
    if best == 0:
        return name
    if best > allowed or (len(distances) > 1 and distances[1][0] == best):
        return value
    return name


# ── recordings · usage ────────────────────────────────────────────────────────


def usage_seconds(rec: dict[str, Any], timeout: float) -> float:
    """
    Length measured by converting to 16 kHz mono 16-bit wav (32,000 bytes a second) — the header is not
    trusted: iPhone m4a headers read as ~2 s (#160) and webm has none. The byte count is used because a
    wav written to a pipe has no frame count in its header.
    """
    try:
        wav = to_wav(rec["path"].read_bytes(), timeout=timeout)
    except AudioConvertError as exc:
        raise VoiceE2EError(f"{rec['file']}: 길이를 읽을 수 없습니다 ({type(exc).__name__})") from exc
    return round((len(wav) - WAV_HEADER_BYTES) / WAV_BYTES_PER_SECOND, 2)


def billed_seconds(vendor: str, seconds: float) -> float:
    """Seconds as the vendor bills one call: rounded up to its unit (CLOVA) or raised to its minimum (RTZR)."""
    unit = BILLING_UNIT_SECONDS.get(vendor)
    if unit:
        return float(math.ceil(seconds / unit) * unit)
    return max(seconds, BILLING_MIN_SECONDS.get(vendor, 0))


def load_usage(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check_budget(planned: dict[str, float], ledger: list[dict[str, Any]], now: datetime) -> None:
    """Refuse when ledger + planned seconds would pass a vendor's day/month/total budget."""
    for vendor, limits in BUDGET_SECONDS.items():
        if not planned.get(vendor):
            continue
        rows = [row for row in ledger if row["vendor"] == vendor]
        spent = {"day": sum(r["seconds"] for r in rows if r["at"][:10] == now.date().isoformat()),
                 "month": sum(r["seconds"] for r in rows if r["at"][:7] == now.strftime("%Y-%m")),
                 "total": sum(r["seconds"] for r in rows)}
        for span, limit in limits.items():
            if spent[span] + planned[vendor] > limit:
                raise VoiceE2EError(f"{vendor} 사용량 한도 초과 예정: 기존 {spent[span]:.0f}초 + 이번 "
                                    f"{planned[vendor]:.0f}초 > {span} {limit}초")


# ── STT calls ─────────────────────────────────────────────────────────────────


def _timed_to_wav(audio: bytes, *, timeout: float) -> bytes:
    """to_wav that records its duration under the audio's id (read back by `_sync_call`)."""
    started = time.perf_counter()
    try:
        return to_wav(audio, timeout=timeout)
    finally:
        with _convert_lock:
            _convert_ms[id(audio)] = round((time.perf_counter() - started) * 1000)


def _error_of(exc: STTError) -> dict[str, Any]:
    cause = exc.__cause__ or exc
    return {"type": type(cause).__name__, "status": getattr(cause, "status_code", getattr(exc, "status_code", None))}


def _sync_call(stt: Callable[..., str], audio: bytes, rec: dict[str, Any], hints: list[str], settings: Any
               ) -> dict[str, Any]:
    """One production transcribe() call with STT time and (if any) conversion time."""
    started = time.perf_counter()
    out: dict[str, Any] = {"transcript": None, "error": None}
    try:
        out["transcript"] = stt(audio, rec["file"], AUDIO_MIME[rec["path"].suffix.lower()],
                                counterparty_hints=hints, settings=settings)
    except STTError as exc:
        out["error"] = _error_of(exc)
    out["stt_ms"] = round((time.perf_counter() - started) * 1000)
    with _convert_lock:  # only CLOVA converts; OpenAI reads the same bytes without converting
        out["convert_ms"] = _convert_ms.pop(id(audio), None) if settings.stt_provider == "clova" else None
    return out


def transcribe_pass(recordings: list[dict[str, Any]], vendors: list[str], hints_by_id: dict[str, list[str]],
                    settings_by_vendor: dict[str, Any], *, stt: Callable[..., str],
                    rtzr: RtzrClient | None) -> dict[str, list[dict[str, Any]]]:
    """All recordings through each vendor once; sync vendors run concurrently per recording."""
    sync = [v for v in vendors if v in SYNC_VENDORS]
    results: dict[str, list[dict[str, Any]]] = {v: [] for v in vendors}
    with ThreadPoolExecutor(max_workers=max(len(sync), 1)) as pool:
        for rec in recordings:
            audio = rec["path"].read_bytes()
            hints = hints_by_id.get(rec["golden_id"], [])
            started = time.perf_counter()
            futures = {v: pool.submit(_sync_call, stt, audio, rec, hints, settings_by_vendor[v]) for v in sync}
            outs = {v: f.result() for v, f in futures.items()}
            wall = round((time.perf_counter() - started) * 1000)
            del audio
            for v, out in outs.items():
                results[v].append({**_identity(rec), **out, "together_ms": wall})
            states = " ".join(f"{v}={'성공' if o['error'] is None else '실패 ' + str(o['error']['status'])}"
                              for v, o in outs.items())
            print(f"[STT] {rec['golden_id']} {rec['speaker']} {rec['usage_seconds']:.1f}s {states}")
    if "rtzr" in vendors:
        results["rtzr"] = _rtzr_pass(recordings, hints_by_id, rtzr)
    return results


def _identity(rec: dict[str, Any]) -> dict[str, Any]:
    return {key: rec[key] for key in ("golden_id", "speaker", "file", "seconds", "usage_seconds")}


def _rtzr_pass(recordings: list[dict[str, Any]], hints_by_id: dict[str, list[str]],
               client: RtzrClient | None) -> list[dict[str, Any]]:
    """Batch jobs, at most RTZR_IN_FLIGHT at once; time is queue + recognition (accuracy only)."""
    if client is None:
        raise VoiceE2EError("rtzr 클라이언트가 없습니다 (RTZR_CLIENT_ID·RTZR_CLIENT_SECRET)")
    out: list[dict[str, Any]] = []
    for start in range(0, len(recordings), RTZR_IN_FLIGHT):
        batch = []
        for rec in recordings[start:start + RTZR_IN_FLIGHT]:
            row = {**_identity(rec), "transcript": None, "error": None, "convert_ms": None, "together_ms": None}
            started = time.perf_counter()
            try:
                row["job"] = client.submit(rec["path"].read_bytes(), rec["file"], AUDIO_MIME[rec["path"].suffix.lower()],
                                           counterparty_hints=hints_by_id.get(rec["golden_id"], []))
            except STTError as exc:
                row["error"] = _error_of(exc)
            batch.append((rec, row, started))
        for rec, row, started in batch:
            if row["error"] is None:
                try:
                    row["transcript"] = client.poll(row.pop("job"), deadline_seconds=RTZR_POLL_DEADLINE_SECONDS)
                except STTError as exc:
                    row["error"] = _error_of(exc)
            row.pop("job", None)
            row["stt_ms"] = round((time.perf_counter() - started) * 1000)
            state = "성공" if row["error"] is None else f"실패 {row['error']['status']}"
            print(f"[STT] {rec['golden_id']} {rec['speaker']} rtzr={state} (대기열 포함)")
            out.append(row)
    return out


# ── parse · score ─────────────────────────────────────────────────────────────


def _rid(result: dict[str, Any]) -> str:
    return f"{result['golden_id']}@{result['speaker']}"


def parse_and_score(stt_results: list[dict[str, Any]], golden: dict[str, dict[str, Any]],
                    lists: dict[str, list[str]], *, snap: bool, out_dir: Path, run_id: str, settings: Any,
                    parser: Callable[..., Any]) -> dict[str, Any]:
    """Parse the transcripts once, then score as-is and (if `snap`) with counterparties snapped to the list."""
    records = voice._voice_records(stt_results, golden)
    if not records:
        return {"plain": None, "list": None}
    path = out_dir / f"{run_id}.transcripts.jsonl"
    with path.open("x", encoding="utf-8") as stream:
        stream.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    baseline.run_baseline(golden_path=path, output_dir=out_dir, parser=parser, run_id=run_id, settings=settings)
    return score_saved(records, out_dir / f"{run_id}.predictions.jsonl", golden, lists, snap=snap)


def score_saved(records: list[dict[str, Any]], predictions: Path, golden: dict[str, dict[str, Any]],
                lists: dict[str, list[str]], *, snap: bool) -> dict[str, Any]:
    """Score one saved parse run as-is and (if `snap`) with counterparties snapped to the owner list."""
    rows = baseline._load_jsonl(predictions)
    processed, _, _ = score._postprocess_rows(records, rows, predictions)
    out = {"plain": _score(records, processed, predictions)}
    if snap:
        snapped, changed_wrong, changed = [], [], 0
        for row in processed:
            candidate = row.get("candidate")
            if isinstance(candidate, dict):
                gid = row["id"].split("@")[0]
                heard = candidate.get("counterparty")
                fixed = snap_counterparty(heard, lists[gid])
                if fixed != heard:
                    changed += 1
                    if "".join(str(fixed).split()) != "".join(str(_own_counterparty(gid, golden)).split()):
                        changed_wrong.append(row["id"])  # e.g. a new counterparty turned into a similar saved one
                row = {**row, "candidate": {**candidate, "counterparty": fixed}, "validation_response": None}
            snapped.append(row)
        out["list"] = {**_score(records, snapped, predictions),
                       "snap": {"changed": changed, "changed_wrong_ids": changed_wrong}}
    return out


def _score(records: list[dict[str, Any]], rows: list[dict[str, Any]], predictions: Path) -> dict[str, Any]:
    """Per-record verdict, comparable key and parse time, plus field accuracy for this run."""
    attached = score._attach_validation(records, rows, predictions)
    metrics = score_records(records, {row["id"]: row for row in attached})
    verdicts = {rid: voice._verdict(row) for rid, row in voice._record_rows(metrics).items()}
    return {"verdict": verdicts, "key": {row["id"]: _answer_key(row) for row in attached},
            "parse_ms": {row["id"]: row.get("wall_time_ms") for row in attached},
            "fields": metrics["field_accuracy"]}


def _answer_key(row: dict[str, Any]) -> str:
    """What the user would see: the candidate's values, or the follow-up asked — used to compare two STTs."""
    response = row.get("validation_response") or {}
    if response.get("status_code") == 400:
        return json.dumps({"ask": response.get("error_code"), "fields": sorted(response.get("problem_fields") or [])})
    candidate = row.get("candidate") or {}
    items = sorted(json.dumps({k: item.get(k) for k in ("name", "qty", "unit", "unit_price", "amount")},
                              ensure_ascii=False, sort_keys=True).replace(" ", "")
                   for item in candidate.get("items") or [])
    return json.dumps({"cp": "".join(str(candidate.get("counterparty") or "").split()),
                       "total": candidate.get("total_amount"), "credit": candidate.get("credit"), "items": items},
                      ensure_ascii=False, sort_keys=True)


# ── summary ───────────────────────────────────────────────────────────────────


def _pct(values: list[int], q: float) -> int | None:
    return baseline._nearest_rank([v for v in values if v is not None], q)


def _field_group(runs: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[str, Any]:
    correct = sum(run["fields"].get(f, {}).get("correct", 0) for run in runs for f in fields)
    total = sum(run["fields"].get(f, {}).get("total", 0) for run in runs for f in fields)
    return {"correct": correct, "total": total, "rate": round(correct / total, 6) if total else None}


def _split_by_list(cell_runs: list[dict[str, Any]], unlisted: set[str]) -> dict[str, Any]:
    """Right/wrong for recordings whose counterparty is on the owner list vs. left out (a new counterparty)."""
    out = {}
    for name, want in (("listed", False), ("unlisted", True)):
        values = [ok for run in cell_runs for rid, ok in run["verdict"].items() if (rid.split("@")[0] in unlisted) is want]
        out[name] = {"correct": sum(values), "total": len(values),
                     "rate": round(sum(values) / len(values), 6) if values else None}
    return out


def summarize_cell(cell_runs: list[dict[str, Any]], *, vendor: str, price_per_min: float | None,
                   golden: dict[str, dict[str, Any]], unlisted: set[str] = frozenset()) -> dict[str, Any]:
    """One cell over its repeats: right/wrong, fields, key info in the transcript, times, usage and cost."""
    calls = [call for run in cell_runs for call in run["stt"]]
    scored = [run["scored"] for run in cell_runs if run["scored"]]
    verdict_rows = [run["verdict"] for run in cell_runs]
    right = sum(v for verdicts in verdict_rows for v in verdicts.values())
    n = sum(len(verdicts) for verdicts in verdict_rows)
    qualities = [voice.stt_quality(golden[c["golden_id"]]["utterance_text"], c["transcript"])
                 for c in calls if c["transcript"] is not None]
    parse_ms = [ms for s in scored for ms in s["parse_ms"].values()]
    stt_ms = [c.get("stt_ms") for c in calls]
    total_ms = [c["stt_ms"] + scored_ms for run in cell_runs if run["scored"]
                for c in run["stt"] for scored_ms in [run["scored"]["parse_ms"].get(_rid(c))]
                if scored_ms is not None and c.get("stt_ms") is not None]
    seconds = sum(c["usage_seconds"] for c in calls)
    billed = sum(billed_seconds(vendor, c["usage_seconds"]) for c in calls)
    cost = None if price_per_min is None else round(billed / 60 * price_per_min, 6)
    return {
        "calls": len(calls), "stt_failed": len([c for c in calls if c["error"]]),
        "stt_errors": dict(Counter(f"{c['error']['type']}:{c['error']['status']}" for c in calls if c["error"])),
        "correct": {"correct": right, "total": n, "rate": round(right / n, 6) if n else None},
        "fields": {"counterparty": _field_group(scored, ("counterparty",)),
                   "numbers": _field_group(scored, NUMBER_FIELDS), "unit": _field_group(scored, ("unit",)),
                   "credit": _field_group(scored, ("credit",))},
        "transcript_key_info": voice._key_info_summary(qualities) if qualities else None,
        "cer_normalized": voice._cer_summary(qualities, "cer_normalized"),
        "latency_ms": {"async_queue": vendor == "rtzr",
                       "stt": {"p50": _pct(stt_ms, .5), "p95": _pct(stt_ms, .95)},
                       "convert": {"p50": _pct([c.get("convert_ms") for c in calls], .5),
                                   "p95": _pct([c.get("convert_ms") for c in calls], .95),
                                   "count": len([c for c in calls if c.get("convert_ms") is not None])},
                       "parse": {"p50": _pct(parse_ms, .5), "p95": _pct(parse_ms, .95)},
                       "second_model": None,  # not in this pipeline (column kept for the stage table)
                       "stt_plus_parse": {"p50": _pct(total_ms, .5), "p95": _pct(total_ms, .95)}},
        "usage_seconds": round(seconds, 2), "billed_seconds": round(billed, 2),
        "cost_usd": cost, "cost_per_call_usd": round(cost / len(calls), 6) if cost is not None and calls else None,
        "by_owner_list": _split_by_list(cell_runs, unlisted),
        "snap": _snap_summary(scored),
    }


def _snap_summary(scored: list[dict[str, Any]]) -> dict[str, Any] | None:
    """힌트+목록 대조 only: how often the counterparty was changed, and changed into a wrong name."""
    snaps = [s["snap"] for s in scored if "snap" in s]
    if not snaps:
        return None
    wrong = [rid for snap in snaps for rid in snap["changed_wrong_ids"]]
    return {"changed": sum(snap["changed"] for snap in snaps), "changed_wrong": len(wrong),
            "changed_wrong_ids": sorted(set(wrong))}


def newly_wrong(cells: dict[tuple[str, str], list[dict[str, Any]]]) -> dict[str, list[str]]:
    """Per cell: recordings right in a majority of reference runs and wrong in a majority of that cell's runs."""
    def majority(runs: list[dict[str, Any]], want: bool) -> set[str]:
        counts: Counter = Counter()
        for run in runs:
            counts.update(rid for rid, ok in run["verdict"].items() if ok is want)
        need = len(runs) // 2 + 1
        return {rid for rid, c in counts.items() if c >= need}
    if REFERENCE_CELL not in cells:
        return {}
    was_right = majority(cells[REFERENCE_CELL], True)
    return {f"{v}/{c}": sorted(was_right & majority(runs, False))
            for (v, c), runs in cells.items() if (v, c) != REFERENCE_CELL}


def pair_class_names(a: str, b: str) -> tuple[str, ...]:
    """일치·맞음 / 일치·틀림 / 불일치·A만 맞음 / 불일치·B만 맞음 / 불일치·둘 다 틀림 (PM 10/20: 하나 맞음을 업체별로)."""
    return ("일치·맞음", "일치·틀림", f"불일치·{a}만 맞음", f"불일치·{b}만 맞음", "불일치·둘 다 틀림")


def pair_classes(cells: dict[tuple[str, str], list[dict[str, Any]]], vendors: list[str]) -> dict[str, Any]:
    """
    Two sync STTs on the same recording: agreement × correctness, and the wall time of calling both.
    `base_wrong_when_differ` = among disagreements, how often the first (base) vendor was wrong.
    """
    out = {}
    for a, b in combinations([v for v in vendors if v in SYNC_VENDORS], 2):
        names = pair_class_names(a, b)
        for cell in CELL_LABELS:
            if (a, cell) not in cells or (b, cell) not in cells:
                continue
            counts = Counter({name: 0 for name in names})
            together = []
            for run_a, run_b in zip(cells[(a, cell)], cells[(b, cell)]):
                keys_a, keys_b = (run["scored"]["key"] if run["scored"] else {} for run in (run_a, run_b))
                for rid, ok_a in run_a["verdict"].items():
                    ok_b = run_b["verdict"].get(rid, False)
                    if rid in keys_a and keys_a.get(rid) == keys_b.get(rid):
                        counts[names[0] if ok_a else names[1]] += 1
                    elif ok_a and not ok_b:
                        counts[names[2]] += 1
                    elif ok_b and not ok_a:
                        counts[names[3]] += 1
                    else:
                        counts[names[4]] += 1
                together += [c["together_ms"] for c in run_a["stt"] if c.get("together_ms") is not None]
            differ = counts[names[2]] + counts[names[3]] + counts[names[4]]
            base_wrong = counts[names[3]] + counts[names[4]]
            out[f"{a}+{b}/{cell}"] = {
                "classes": dict(counts),
                "base_wrong_when_differ": {"wrong": base_wrong, "differ": differ,
                                           "rate": round(base_wrong / differ, 6) if differ else None},
                "together_ms": {"p50": _pct(together, .5), "p95": _pct(together, .95)}}
    return out


def _unit_key(unit: Any) -> Any:
    return UNIT_SAME.get(unit.strip().lower(), unit.strip()) if isinstance(unit, str) else unit


def _ratio(correct: int, total: int) -> dict[str, Any]:
    return {"correct": correct, "total": total, "rate": round(correct / total, 6) if total else None}


def unit_and_credit(out_dir: Path, run_ids: str | list[str], golden: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """
    Per cell, from the run's saved transcripts and predictions (PM 10/21, no API call):
    - unit as written vs. with spelling variants as one (키로 = 킬로)
    - 외상 표현 보존: of the recordings whose sentence has a credit word (외상·미수·달아…), how many
      transcripts still have one — a lost word can save credit as cash
    - 외상 칸: credit=True answered for credit sentences, and 외상→현금 저장 (credit=False with a 200)
    힌트+목록 대조 only changes the counterparty, so its numbers are the hint run's.
    """
    counts: dict[str, Counter] = defaultdict(Counter)
    for vendor, condition, path in _saved_runs(out_dir, [run_ids] if isinstance(run_ids, str) else run_ids):
        records = baseline._load_jsonl(path.with_name(path.name.replace(".predictions.", ".transcripts.")))
        processed, _, _ = score._postprocess_rows(records, baseline._load_jsonl(path), path)
        rows = {row["id"]: row for row in score._attach_validation(records, processed, path)}
        cells = [f"{vendor}/{condition}"] + ([f"{vendor}/hint_list"] if condition == "hint" else [])
        for record in records:
            c = Counter()
            expected = record["expected_json"] or {}
            row = rows.get(record["id"], {})
            candidate = row.get("candidate") if isinstance(row.get("candidate"), dict) else {}
            matched, missing, _ = _match_items(expected.get("items") or [], candidate.get("items") or [])
            for want, got in matched + [(item, {}) for item in missing]:
                if want.get("unit") is not None:
                    c["unit_total"] += 1
                    c["unit_raw"] += want.get("unit") == got.get("unit")
                    c["unit_same"] += _unit_key(want.get("unit")) == _unit_key(got.get("unit"))
            reference = golden[record["voice"]["golden_id"]]["utterance_text"]
            if any(term in reference for term in CREDIT_EXPRESSIONS):
                c["credit_words_total"] += 1
                c["credit_words_kept"] += any(term in record["utterance_text"] for term in CREDIT_EXPRESSIONS)
            if expected.get("credit") is True:
                c["credit_total"] += 1
                c["credit_right"] += candidate.get("credit") is True
                status = (row.get("validation_response") or {}).get("status_code")
                c["credit_saved_as_cash"] += candidate.get("credit") is False and status == 200
            for cell in cells:
                counts[cell].update(c)
    return {cell: {"unit_as_written": _ratio(c["unit_raw"], c["unit_total"]),
                   "unit_variants_same": _ratio(c["unit_same"], c["unit_total"]),
                   "credit_words_kept": _ratio(c["credit_words_kept"], c["credit_words_total"]),
                   "credit_field": _ratio(c["credit_right"], c["credit_total"]),
                   "credit_saved_as_cash": c["credit_saved_as_cash"]}
            for cell, c in counts.items()}


def render_unit_and_credit(extra: dict[str, Any]) -> list[str]:
    lines = ["", "## 단위·외상 (PM 10/21)", "",
             "- 단위: 라벨은 표기 그대로라, \"키로\"를 \"킬로\"와 같게 본 줄을 따로 둠 (운영 코드는 그대로)",
             "- 외상 표현 보존: 문장에 외상·미수·달아… 가 있는 녹음 중 전사문에도 남은 비율. 사라지면 현금으로 저장될 수 있음",
             "- STT 실패(503) 녹음은 텍스트 입력으로 넘어가므로 여기서 빠짐", "",
             "| 칸 | 단위 표기 그대로 | 단위 킬로/키로 같게 | 외상 표현 보존 | 외상 칸 맞음 | 외상→현금 저장 |",
             "|---|---|---|---|---|---|"]
    for cell, e in extra.items():
        lines.append(f"| {cell} | {_r(e['unit_as_written'])} ({e['unit_as_written']['total']}) | "
                     f"{_r(e['unit_variants_same'])} | {_frac(e['credit_words_kept'])} | {_frac(e['credit_field'])} | "
                     f"{e['credit_saved_as_cash']}건 |")
    return lines


def _frac(metric: dict[str, Any]) -> str:
    return f"{metric['correct']}/{metric['total']} ({_r(metric)})"


def add_unit_and_credit(run_id: str, out_dir: Path, results_dir: Path = voice.RESULTS_DIR) -> dict[str, Any]:
    """Add the 단위·외상 section to a finished run's summary and report (for runs made before it existed)."""
    summary_path = results_dir / f"{run_id}.summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["unit_and_credit"] = unit_and_credit(out_dir, run_id, voice.load_dev_golden())
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (results_dir / f"{run_id}.report.md").write_text(render_report(summary), encoding="utf-8")
    return summary


def _saved_runs(out_dir: Path, run_ids: list[str]) -> list[tuple[str, str, Path]]:
    """(vendor, condition, predictions path) of each parse run saved under these run IDs, in run order."""
    found = []
    for run_id in run_ids:
        pattern = re.compile(rf"{re.escape(run_id)}-(\w+)-(plain|hint)-r(\d+)\.predictions\.jsonl")
        for path in out_dir.glob(f"{run_id}-*.predictions.jsonl"):
            match = pattern.fullmatch(path.name)
            if match:
                found.append((run_ids.index(run_id), int(match.group(3)), match.group(1), match.group(2), path))
    return [(vendor, condition, path) for *_, vendor, condition, path in sorted(found)]


def merge_runs(run_ids: list[str], out_dir: Path, *, results_dir: Path = voice.RESULTS_DIR,
               merged_id: str | None = None, prices: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    One result table from several finished runs of the same recordings (e.g. 1 run today + 1 tomorrow
    because CLOVA allows 1 h a day). Right/wrong, fields, pairs, 새로 틀림 and 단위·외상 are recomputed from
    the saved transcripts and predictions; times come from `<run>.calls.jsonl` when the run wrote one.
    """
    golden = voice.load_dev_golden()
    prices = prices if prices is not None else baseline.load_prices()
    summaries = [json.loads((results_dir / f"{rid}.summary.json").read_text(encoding="utf-8")) for rid in run_ids]
    usage = [row for row in load_usage(out_dir / "usage.jsonl") if row["run_id"] in run_ids]
    recorded = {row["golden_id"] for row in usage}
    unlisted = unlisted_ids(recorded, golden)
    lists = {gid: owner_list(gid, golden, include_own=gid not in unlisted) for gid in recorded}
    timings: dict[tuple, dict[str, Any]] = {}
    for rid in run_ids:
        for row in load_usage(out_dir / f"{rid}.calls.jsonl"):
            timings[(rid, row["vendor"], row["condition"], row["r"], row["golden_id"], row["speaker"])] = row
    order: Counter = Counter()
    cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for vendor, condition, path in _saved_runs(out_dir, run_ids):
        rid, r = path.name.split(f"-{vendor}-{condition}-r")[0], int(path.name.rsplit("-r", 1)[1].split(".")[0])
        records = baseline._load_jsonl(path.with_name(path.name.replace(".predictions.", ".transcripts.")))
        heard = {record["id"]: record["utterance_text"] for record in records}
        order[(rid, vendor, condition)] += 1
        rows = [row for row in usage if (row["run_id"], row["vendor"], row["condition"]) == (rid, vendor, condition)]
        per_pass = len(rows) // max(1, sum(1 for v, c, p in _saved_runs(out_dir, [rid]) if (v, c) == (vendor, condition)))
        rows = rows[(order[(rid, vendor, condition)] - 1) * per_pass: order[(rid, vendor, condition)] * per_pass]
        calls = []
        for row in rows:
            key = f"{row['golden_id']}@{row['speaker']}"
            timing = timings.get((rid, vendor, condition, r, row["golden_id"], row["speaker"]), {})
            calls.append({"golden_id": row["golden_id"], "speaker": row["speaker"], "transcript": heard.get(key),
                          "usage_seconds": row.get("audio_seconds", row["seconds"]),
                          "error": None if row["ok"] else (timing.get("error") or {"type": "STTError", "status": None}),
                          **{k: timing.get(k) for k in ("stt_ms", "convert_ms", "together_ms")}})
        scored = score_saved(records, path, golden, lists, snap=condition == "hint")
        for cell, key in ((condition, "plain"), ("hint_list", "list")):
            if key == "list" and condition != "hint":
                continue
            s = scored[key]
            verdict = {_rid(c): bool(s and s["verdict"].get(_rid(c), False)) for c in calls}
            cells[(vendor, cell)].append({"stt": calls, "scored": s, "verdict": verdict})
    # the runs' own vendor order — the base vendor (openai) stays first in pairs and 새로 틀림
    vendors = [v for v in dict.fromkeys(v for summ in summaries for v in summ["vendors"]) if any(v == cv for cv, _ in cells)]
    repeat = max(len(runs) for runs in cells.values())
    merged_id = merged_id or "sttcmp-merged-" + "+".join(rid.removeprefix("sttcmp-") for rid in run_ids)
    first = summaries[0]
    settings = SimpleNamespace(stt_model=first.get("stt_model_openai", "whisper-1"))
    summary = {
        "run_id": merged_id, "merged_from": run_ids, "git": first["git"], "recordings": len({(row["golden_id"], row["speaker"]) for row in usage}), "repeat": repeat, "vendors": vendors,
        "conditions": first["conditions"], "llm_model": first["llm_model"],
        "stt_model_openai": settings.stt_model, "stt_prompt": first["stt_prompt"],
        "cells": {f"{v}/{c}": summarize_cell(runs, vendor=v, price_per_min=_price(prices, v, settings), golden=golden,
                                               unlisted=unlisted) for (v, c), runs in cells.items()},
        "pairs": pair_classes(cells, vendors), "newly_wrong": newly_wrong(cells),
        "notes": {**first["notes"], "merged": "여러 측정을 합침 — 시간 칸은 호출별 기록(calls.jsonl)이 있는 측정만"},
        "unit_and_credit": unit_and_credit(out_dir, run_ids, golden),
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / f"{merged_id}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                                            encoding="utf-8")
    (results_dir / f"{merged_id}.report.md").write_text(render_report(summary), encoding="utf-8")
    return summary


def render_report(summary: dict[str, Any]) -> str:
    """Markdown with numbers and IDs only."""
    lines = [f"# STT 업체 비교 {summary['run_id']}", "",
             *([f"- 합친 측정: {', '.join(summary['merged_from'])}"] if summary.get("merged_from") else []),
             (f"- 커밋 {summary['git']['git_commit']} · 녹음 {summary['recordings']}건 · 반복 {summary['repeat']}회 · "
              f"LLM {summary['llm_model']}"),
             # PM 10/21: the OpenAI STT model must be visible — local default whisper-1 ≠ production gpt-4o-transcribe
             *([f"- OpenAI 음성 인식 모델: {summary['stt_model_openai']}"] if "openai" in summary["vendors"] else []),
             f"- {newly_wrong_rule(summary['repeat'])}", "", "## 9칸 (맞음 비율 · 거래처 · 숫자 · 단위)", "",
             "| 업체 | " + " | ".join(CELL_LABELS.values()) + " |", "|---|---|---|---|"]
    for vendor in summary["vendors"]:
        row = []
        for cell in CELL_LABELS:
            s = summary["cells"].get(f"{vendor}/{cell}")
            row.append("—" if s is None else " · ".join(_r(x) for x in (
                s["correct"], s["fields"]["counterparty"], s["fields"]["numbers"], s["fields"]["unit"])))
        lines.append(f"| {vendor} | " + " | ".join(row) + " |")
    lines += ["", "## 칸별 상세", "",
              "| 칸 | 호출 | STT 실패 | 음성 인식 p50/p95 | 변환 p50/p95 (건수) | 해석 p50/p95 | 합계 p50/p95 | 사용 초 (청구 초) | 건당 비용 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for name, s in summary["cells"].items():
        lat = s["latency_ms"]
        note = " (대기열 포함·운영 비교 불가)" if lat["async_queue"] else ""
        cost = "단가 미확인" if s["cost_per_call_usd"] is None else f"${s['cost_per_call_usd']:.5f}"
        lines.append(f"| {name}{note} | {s['calls']} | {s['stt_failed']} | {_ms(lat['stt'])} | "
                     f"{_ms(lat['convert'])} ({lat['convert']['count']}) | {_ms(lat['parse'])} | "
                     f"{_ms(lat['stt_plus_parse'])} | {s['usage_seconds']:.0f} ({s['billed_seconds']:.0f}) | {cost} |")
    lines += ["", "## 두 업체 동시 실행", ""]
    for name, p in summary["pairs"].items():
        classes = list(p["classes"])
        differ = p["base_wrong_when_differ"]
        lines += [f"| {name} | " + " | ".join(classes) + " | 다를 때 기본 업체 틀림 | 동시 p50/p95 |",
                  "|---" * (len(classes) + 3) + "|",
                  "| | " + " | ".join(str(p["classes"][c]) for c in classes)
                  + f" | {differ['wrong']}/{differ['differ']} | {_ms(p['together_ms'])} |", ""]
    lines += ["## 목록 대조 (정답 거래처가 목록에 있음 / 없음 · 다른 이름으로 바꿈)", "",
              "| 칸 | 목록에 있음 | 목록에 없음 | 바꾼 횟수 | 틀리게 바꿈 |", "|---|---|---|---|---|"]
    for name, s in summary["cells"].items():
        split, snap = s["by_owner_list"], s["snap"]
        lines.append(f"| {name} | {_r(split['listed'])} ({split['listed']['total']}) | "
                     f"{_r(split['unlisted'])} ({split['unlisted']['total']}) | "
                     f"{'—' if snap is None else snap['changed']} | "
                     f"{'—' if snap is None else str(snap['changed_wrong']) + ' ' + ', '.join(snap['changed_wrong_ids'])} |")
    lines += ["", f"## 새로 틀림 — {newly_wrong_rule(summary['repeat'])}", ""]
    lines += [f"- {name}: {len(ids)}건 {', '.join(ids)}" for name, ids in summary["newly_wrong"].items()] or ["- 기준 칸 없음"]
    if summary.get("unit_and_credit"):
        lines += render_unit_and_credit(summary["unit_and_credit"])
    return "\n".join(lines) + "\n"


def newly_wrong_rule(repeat: int) -> str:
    """The rule as words for the report, e.g. 2 → "음성 인식은 2회, 2번 모두 틀림 기준" (PM 10/21)."""
    need = repeat // 2 + 1
    times = f"{need}번 모두" if need == repeat else f"{repeat}번 중 {need}번 이상"
    return (f"음성 인식은 {repeat}회, {times} 틀림 기준 (기준 칸 {'/'.join(REFERENCE_CELL)}에서 "
            f"{times} 맞던 녹음이 다른 칸에서 {times} 틀린 경우)")


def _r(metric: dict[str, Any]) -> str:
    return "—" if metric["rate"] is None else f"{metric['rate'] * 100:.0f}%"


def _ms(stat: dict[str, Any]) -> str:
    return f"{stat['p50']}/{stat['p95']}" if stat.get("p50") is not None else "—"


# ── run ───────────────────────────────────────────────────────────────────────


def _price(prices: dict[str, Any], vendor: str, settings: Any) -> float | None:
    key = settings.stt_model if vendor == "openai" else PRICE_KEYS[vendor]
    return ((prices.get("stt_models") or {}).get(key) or {}).get("usd_per_minute")


def run(*, audio_dir: Path, manifest: Path, out_dir: Path, vendors: list[str], conditions: list[str],
        repeat: int = 3, limit: int | None = None, delete_after: bool = False, settings: Any = None,
        prices: dict[str, Any] | None = None, stt: Callable[..., str] = transcribe,
        parser: Callable[..., Any] = parse_utterance_with_llm, rtzr: RtzrClient | None = None,
        results_dir: Path = voice.RESULTS_DIR, run_id: str | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(baseline.KST)) -> dict[str, Any]:
    """Whole comparison; returns the summary written to `results_dir`. See the module docstring."""
    if repeat < 1 or not vendors or not conditions:
        raise VoiceE2EError("--repeat ≥ 1, 업체·조건은 하나 이상이어야 합니다")
    audio_dir = voice.require_outside_repo(audio_dir, "--audio-dir")
    out_dir = voice.require_outside_repo(out_dir, "--out-dir")
    golden = voice.load_dev_golden()
    rows = voice.load_manifest(manifest)[:limit] if limit else voice.load_manifest(manifest)
    unknown = sorted({row["golden_id"] for row in rows} - set(golden))
    if unknown:
        raise VoiceE2EError(f"Dev Golden에 없는 ID: {unknown[:5]}")
    git = baseline._git_state()
    if git["git_dirty"]:
        raise VoiceE2EError(f"커밋 안 된 변경이 있습니다 — 커밋한 뒤 실행하세요 (HEAD {git['git_commit']})")
    text = voice.load_text_baseline()
    settings = settings or voice.measurement_settings(text["run"])
    voice.require_comparable(settings, text["run"])
    voice.stt_prompt_of(settings)
    prices = prices if prices is not None else baseline.load_prices()
    by_vendor = {v: settings.model_copy(update={"stt_provider": v}) for v in vendors if v in SYNC_VENDORS}

    recordings = voice.check_recordings(rows, audio_dir, settings.stt_max_bytes)
    for rec in recordings:
        rec["usage_seconds"] = usage_seconds(rec, settings.stt_timeout_seconds)
    recorded = {r["golden_id"] for r in recordings}
    unlisted = unlisted_ids(recorded, golden)
    lists = {gid: owner_list(gid, golden, include_own=gid not in unlisted) for gid in recorded}
    passes = repeat * len(conditions)
    planned = {v: sum(billed_seconds(v, r["usage_seconds"]) for r in recordings) * passes for v in vendors}
    usage_path = out_dir / "usage.jsonl"
    check_budget(planned, load_usage(usage_path), now())
    stt_cost = sum((planned[v] / 60 * p) for v in vendors if (p := _price(prices, v, settings)) is not None)
    reference = [{**golden[r["golden_id"]],
                  "utterance_text": golden[r["golden_id"]]["utterance_text"] * voice.TRANSCRIPT_LENGTH_MARGIN}
                 for r in recordings]
    llm_cost = baseline.estimate_cost(records=reference, settings_by_tag={"voice": settings},
                                      repeat=passes * len(vendors), prices=prices)["total_usd"]
    voice._check_cap({"stt_usd": round(stt_cost, 6), "llm_usd": llm_cost, "total_usd": round(stt_cost + llm_cost, 6),
                      "cap_usd": prices.get("cost_cap_usd", 1.0)}, "실행 전 (단가 미확인 업체는 제외)")
    print(f"[계획] 녹음 {len(recordings)}건 × {passes}회 · 업체 {', '.join(vendors)} · 사용 예정(초) "
          + ", ".join(f"{v}={planned[v]:.0f}" for v in vendors) + " (CLOVA는 건당 15초 올림)")
    if "openai" in vendors:  # before any call: the model is the one thing .env can silently leave at its default
        print(f"[모델] OpenAI 음성 인식 = {settings.stt_model} (운영과 맞추려면 STT_MODEL)")
    if "clova" in vendors:
        print("[안내] CLOVA는 월 2시간·일 1시간 한도가 걸려 있어, 넘으면 호출이 막힙니다 (키 문제가 아닐 수 있음)")

    run_id = run_id or now().strftime("sttcmp-%Y%m%d-%H%M%S-kst")
    out_dir.mkdir(parents=True, exist_ok=True)
    cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    original_to_wav = stt_module.to_wav
    stt_module.to_wav = _timed_to_wav
    try:
        for index in range(1, repeat + 1):
            for condition in conditions:
                hints = lists if condition == "hint" else {}
                results = transcribe_pass(recordings, vendors, hints, by_vendor, stt=stt, rtzr=rtzr)
                with usage_path.open("a", encoding="utf-8") as stream:
                    for vendor, calls in results.items():
                        stream.writelines(json.dumps({"at": now().isoformat(timespec="seconds"), "run_id": run_id,
                                                      "vendor": vendor, "condition": condition,
                                                      "golden_id": c["golden_id"], "speaker": c["speaker"],
                                                      "seconds": billed_seconds(vendor, c["usage_seconds"]),
                                                      "audio_seconds": c["usage_seconds"],
                                                      "ok": c["error"] is None}) + "\n" for c in calls)
                with (out_dir / f"{run_id}.calls.jsonl").open("a", encoding="utf-8") as stream:  # for --merge
                    for vendor, calls in results.items():
                        stream.writelines(json.dumps({"vendor": vendor, "condition": condition, "r": index,
                                                      **{k: c.get(k) for k in CALL_FIELDS}}) + "\n" for c in calls)
                for vendor, calls in results.items():
                    scored = parse_and_score(calls, golden, lists, snap=condition == "hint", out_dir=out_dir,
                                             run_id=f"{run_id}-{vendor}-{condition}-r{index}", settings=settings,
                                             parser=parser)
                    all_ids = [_rid(c) for c in calls]
                    for cell, key in ((condition, "plain"), ("hint_list", "list")):
                        if key == "list" and condition != "hint":
                            continue
                        s = scored[key]
                        verdict = {rid: bool(s and s["verdict"].get(rid, False)) for rid in all_ids}
                        cells[(vendor, cell)].append({"stt": calls, "scored": s, "verdict": verdict})
                print(f"[구조화] r{index} {CELL_LABELS[condition]} 완료")
    finally:
        stt_module.to_wav = original_to_wav

    summary = {
        "run_id": run_id, "git": git, "recordings": len(recordings), "repeat": repeat, "vendors": vendors,
        "conditions": conditions, "llm_model": settings.llm_model, "stt_model_openai": settings.stt_model,
        "stt_prompt": voice._stt_prompt_info(voice.stt_prompt_of(settings)),
        "cells": {f"{v}/{c}": summarize_cell(runs, vendor=v, price_per_min=_price(prices, v, settings), golden=golden,
                                               unlisted=unlisted)
                  for (v, c), runs in cells.items()},
        "pairs": pair_classes(cells, vendors), "newly_wrong": newly_wrong(cells),
        "notes": {"pair_classes": "일치 = 두 업체의 후보 값(또는 되묻는 칸)이 같음. 일치·틀림이 되묻기로도 못 잡는 조용한 오류",
                  "owner_list": f"녹음마다 자기 Golden 거래처 + 다른 Dev 거래처 (≤20곳·120자). 거래처 있는 녹음 "
                                f"{UNLISTED_EVERY}건 중 1건은 자기 거래처를 뺌 (새 거래처)",
                  "unlisted_ids": sorted(unlisted)},
        "unit_and_credit": unit_and_credit(out_dir, run_id, golden),
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    with (results_dir / f"{run_id}.summary.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    with (results_dir / f"{run_id}.report.md").open("x", encoding="utf-8") as stream:
        stream.write(render_report(summary))
    print(f"[보고서] {baseline._display_path(results_dir / f'{run_id}.report.md')}")

    failed = any(s["stt_failed"] for s in summary["cells"].values())
    if delete_after and not failed:
        for rec in recordings:
            rec["path"].unlink()
        print(f"[삭제] 녹음 {len(recordings)}건 삭제")
    else:
        print("[삭제 남음] 녹음은 지우지 않았습니다" + (" (STT 실패가 있음)" if delete_after else ""))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_stt_compare", description=__doc__.splitlines()[0])
    parser.add_argument("--audio-dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--vendors", nargs="+", choices=VENDORS, default=list(VENDORS))
    parser.add_argument("--conditions", nargs="+", choices=STT_CONDITIONS, default=list(STT_CONDITIONS))
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--limit", type=int, help="manifest 앞 N건만 (CLOVA 첫 10건 확인용)")
    parser.add_argument("--delete-after", action="store_true")
    parser.add_argument("--add-unit-credit", metavar="RUN_ID",
                        help="끝난 측정에 단위·외상 칸만 다시 계산해 넣기 (--out-dir만 필요, API 호출 없음)")
    parser.add_argument("--merge", nargs="+", metavar="RUN_ID",
                        help="같은 녹음으로 끝낸 측정 여러 개를 결과표 하나로 합치기 (--out-dir만 필요, API 호출 없음)")
    args = parser.parse_args(argv)
    if args.merge:
        merged = merge_runs(args.merge, args.out_dir)
        print(f"[보고서] eval/results/{merged['run_id']}.report.md (반복 {merged['repeat']}회)")
        return 0
    if args.add_unit_credit:
        add_unit_and_credit(args.add_unit_credit, args.out_dir)
        print(f"[보고서] eval/results/{args.add_unit_credit}.report.md 에 단위·외상 칸 추가")
        return 0
    if args.audio_dir is None or args.manifest is None:
        parser.error("--audio-dir, --manifest가 필요합니다")
    os.environ.setdefault("APP_ENV", "development")
    try:
        rtzr = None
        if "rtzr" in args.vendors:
            env = dotenv_values(voice.BACKEND_ROOT / ".env")
            rtzr = RtzrClient(os.environ.get("RTZR_CLIENT_ID") or env.get("RTZR_CLIENT_ID") or "",
                              os.environ.get("RTZR_CLIENT_SECRET") or env.get("RTZR_CLIENT_SECRET") or "")
        run(audio_dir=args.audio_dir, manifest=args.manifest, out_dir=args.out_dir, vendors=args.vendors,
            conditions=args.conditions, repeat=args.repeat, limit=args.limit, delete_after=args.delete_after, rtzr=rtzr)
    except (VoiceE2EError, STTError) as exc:
        print(f"[중단] {exc}")
    except Exception as exc:  # noqa: BLE001 — may quote a sentence, so only the type is shown
        print(f"[중단] 예상하지 못한 오류 {type(exc).__name__} (문장 노출 방지로 내용은 출력하지 않음)")
    else:
        return 0
    print("[삭제 남음] 녹음은 지우지 않았습니다")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
