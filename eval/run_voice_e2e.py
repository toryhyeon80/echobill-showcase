"""Voice E2E on Dev Golden — recording → STT → structuring → scoring, compared with the text baseline.

    python eval/run_voice_e2e.py --audio-dir D:/rec --manifest D:/rec/manifest.csv --out-dir D:/rec-out \
        [--repeat 3] [--delete-after] [--gap-exclude g-dev-037]

Flow: read manifest.csv (golden_id, speaker, file) and check paths → `transcribe()` once per recording
→ copy the Dev Golden record with only `utterance_text` replaced by the transcript → `run_baseline()`
`--repeat` times (raw output) → offline post-processing + validation (`score.rescore`, same as DL-11)
→ compare per Golden ID with the DL-11 gpt-4o-mini text runs re-scored at this commit (no API).

Rules (recordings stay outside the repo and are deleted after measurement):
- `--audio-dir`·`--out-dir` must resolve outside the repository (symlinks included).
- Only manifest files are read; webm·m4a·mp4·mp3·wav·ogg, ≤ STT_MAX_BYTES; name `{speaker}_{golden_id}.{ext}`,
  speaker `S<number>` only. Audio is held in memory, never copied or converted.
- Console shows ID, speaker alias, length, success and error type/HTTP status only — never utterances,
  transcripts, candidates or exception messages. Transcripts go to `<out-dir>` only; `eval/results/` gets
  `voice-<ts>.summary.json`·`.report.md` with numbers and per-ID right/wrong.
- Estimated cost is printed first; above `cost_cap_usd`, with no Whisper price, or with uncommitted
  changes nothing runs; neither with STT_PROMPT_FILE set to a missing or blank file (the server would
  silently drop the hint). Recordings are deleted only with `--delete-after` after a fully successful run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import struct
import sys
import wave
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
for _path in (REPO_ROOT, BACKEND_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.config import Settings  # noqa: E402
from app.services.normalize_ko import NORMALIZER_VERSION, normalize_korean_numerals  # noqa: E402
from app.services.parse_llm import load_system_prompt, parse_utterance_with_llm  # noqa: E402
from app.services.postprocess import CREDIT_SUBSTRINGS, CREDIT_WORDS, PAYMENT_WORDS  # noqa: E402
from app.services.stt import STTError, transcribe  # noqa: E402
from eval import run_baseline as baseline  # noqa: E402
from eval.score import rescore  # noqa: E402
from eval.scoring import SCORING_VERSION  # noqa: E402

GOLDEN_DEV = REPO_ROOT / "data" / "golden" / "golden_dev.jsonl"
DEV_SPLIT = "개발용 Golden"
RESULTS_DIR = REPO_ROOT / "eval" / "results"
# Text baseline: DL-11 gpt-4o-mini r1·r2·r3 raw outputs (10/8), re-scored offline with this commit's code.
TEXT_BASELINE_DIR = RESULTS_DIR / "dl11-20261008-154020-kst"
TEXT_BASELINE_RUNS = (
    "dl11-20261008-154020-kst-pass1-mini-r1",
    "dl11-20261008-154020-kst-pass2-mini-r1",
    "dl11-20261008-154020-kst-pass2-mini-r2",
)
# Browser-style MIME per extension (same table as scripts/demo_core.py, inside the server allowlist).
AUDIO_MIME = {".webm": "audio/webm", ".m4a": "audio/mp4", ".mp4": "audio/mp4",
              ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg"}
SPEAKER_PATTERN = re.compile(r"^S\d+$")
GOLDEN_ID_PATTERN = re.compile(r"^g-dev-\d{3}$")
MANIFEST_COLUMNS = ["golden_id", "speaker", "file"]
# Cost upper bound for a recording whose length cannot be read from the file (webm·mp3).
ASSUMED_MAX_SECONDS = 60
# The LLM cost is bounded before STT with the reference sentence ×2 standing in for the transcript.
TRANSCRIPT_LENGTH_MARGIN = 2
# A transcript sharing this many consecutive Hangul characters with the STT prompt counts as prompt leakage.
PROMPT_LEAK_MIN_CHARS = 6
METRIC_KEYS = ("transaction_exact", "amount_exact", "confirmation_appropriate")
CREDIT_TERMS = PAYMENT_WORDS + CREDIT_WORDS + CREDIT_SUBSTRINGS


class VoiceE2EError(ValueError):
    """A refusal before or during the run; the message never holds sentence content."""


# ── inputs ────────────────────────────────────────────────────────────────────


def require_outside_repo(path: Path, name: str) -> Path:
    """Resolved absolute path, refused when it lies inside the repository (symlinks resolved first)."""
    if not path.is_absolute():
        raise VoiceE2EError(f"{name}: 레포 밖 절대경로가 필요합니다")
    resolved = path.resolve()
    repo = REPO_ROOT.resolve()
    if resolved == repo or repo in resolved.parents:
        raise VoiceE2EError(f"{name}: 레포 안 경로는 쓸 수 없습니다 (녹음·STT 결과는 레포 밖에 둔다)")
    return resolved


def load_manifest(path: Path) -> list[dict[str, str]]:
    """Rows of manifest.csv; refuses other columns, non-alias speakers, bad names and duplicates."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != MANIFEST_COLUMNS:
            raise VoiceE2EError(f"manifest 열은 {','.join(MANIFEST_COLUMNS)} 이어야 합니다")
        rows = [{key: (row[key] or "").strip() for key in MANIFEST_COLUMNS} for row in reader]
    seen: set[tuple[str, str]] = set()
    for line, row in enumerate(rows, start=2):
        if not SPEAKER_PATTERN.match(row["speaker"]):
            raise VoiceE2EError(f"manifest {line}행: speaker는 S1·S2… 가명만 허용합니다 (실명·이니셜 금지)")
        if not GOLDEN_ID_PATTERN.match(row["golden_id"]):
            raise VoiceE2EError(f"manifest {line}행: golden_id는 g-dev-000 형식의 Dev ID여야 합니다")
        ext = Path(row["file"]).suffix.lower()
        if ext not in AUDIO_MIME:
            raise VoiceE2EError(f"manifest {line}행: 허용 형식은 {', '.join(sorted(AUDIO_MIME))} 입니다")
        if row["file"] != f"{row['speaker']}_{row['golden_id']}{Path(row['file']).suffix}":
            raise VoiceE2EError(f"manifest {line}행: 파일명은 {{speaker}}_{{golden_id}}.{{확장자}} 여야 합니다")
        key = (row["golden_id"], row["speaker"])
        if key in seen:
            raise VoiceE2EError(f"manifest {line}행: {row['golden_id']} {row['speaker']} 가 두 번 있습니다")
        seen.add(key)
    if not rows:
        raise VoiceE2EError("manifest에 녹음이 없습니다")
    return rows


def load_dev_golden(path: Path = GOLDEN_DEV) -> dict[str, dict[str, Any]]:
    """Dev Golden records by ID; anything but Dev is refused (Test·Seed never measured here)."""
    records = baseline._load_jsonl(path)
    outside = [record["id"] for record in records if record.get("split") != DEV_SPLIT]
    if outside:
        raise VoiceE2EError(f"Dev Golden만 측정합니다: {outside[:5]}")
    return {record["id"]: record for record in records}


def check_recordings(rows: list[dict[str, str]], audio_dir: Path, max_bytes: int) -> list[dict[str, Any]]:
    """Each manifest file must sit directly in audio_dir (no link out) and be 1 byte…max_bytes."""
    checked = []
    for row in rows:
        path = (audio_dir / row["file"]).resolve()
        if path.parent != audio_dir or not path.is_file():
            raise VoiceE2EError(f"{row['file']}: --audio-dir 안에 파일이 없습니다 (링크·하위 폴더 불가)")
        size = path.stat().st_size
        if not 0 < size <= max_bytes:
            raise VoiceE2EError(f"{row['file']}: 파일 크기는 1바이트~{max_bytes}바이트여야 합니다 (STT_MAX_BYTES)")
        checked.append({**row, "path": path, "seconds": audio_seconds(path.read_bytes(), path.suffix.lower())})
    return checked


def audio_seconds(data: bytes, ext: str) -> float | None:
    """Recording length from the container header (wav·mp4·m4a·ogg); None when it cannot be read."""
    try:
        if ext == ".wav":
            with wave.open(io.BytesIO(data)) as stream:
                return round(stream.getnframes() / stream.getframerate(), 2)
        if ext in (".mp4", ".m4a"):
            at = data.find(b"mvhd")
            if at < 0:
                return None
            if data[at + 4] == 1:  # version 1: 64-bit times and duration
                timescale, duration = struct.unpack(">IQ", data[at + 24:at + 36])
            else:
                timescale, duration = struct.unpack(">II", data[at + 16:at + 24])
            return round(duration / timescale, 2) if timescale else None
        if ext == ".ogg":
            last = data.rfind(b"OggS")
            granule = struct.unpack("<q", data[last + 6:last + 14])[0] if last >= 0 else -1
            vorbis = data.find(b"\x01vorbis")
            if b"OpusHead" in data:
                rate = 48_000  # Opus granule positions always count 48 kHz samples
            elif vorbis >= 0:
                rate = struct.unpack("<I", data[vorbis + 12:vorbis + 16])[0]
            else:
                return None
            return round(granule / rate, 2) if granule > 0 and rate else None
    except (wave.Error, struct.error, EOFError, IndexError, ZeroDivisionError):
        return None
    return None


# ── settings · baseline · cost ────────────────────────────────────────────────


def load_text_baseline(golden_path: Path = GOLDEN_DEV) -> dict[str, Any]:
    """DL-11 mini raw outputs re-scored now (post-processing + validation): per-run rows and run settings."""
    runs = []
    for run_id in TEXT_BASELINE_RUNS:
        predictions = TEXT_BASELINE_DIR / f"{run_id}.predictions.jsonl"
        scored = rescore(golden_path, predictions, postprocess=True, attach_validation=True)
        runs.append({"run_id": run_id, "rows": _record_rows(scored["metrics"]),
                     "postprocess_version": scored["run"]["postprocess_version"]})
    summary = json.loads((TEXT_BASELINE_DIR / f"{TEXT_BASELINE_RUNS[0]}.summary.json").read_text(encoding="utf-8"))
    return {"runs": runs, "run": summary["run"]}


def measurement_settings(baseline_run: dict[str, Any]) -> Settings:
    """backend/.env settings pinned to the text baseline's model and call settings, raw output saved."""
    calls = baseline_run["call_settings"]
    return Settings(
        _env_file=BACKEND_ROOT / ".env", llm_model=baseline_run["model"],
        ko_numeral_normalize=baseline_run["ko_numeral_normalize"], llm_postprocess=False,
        llm_temperature=calls["temperature"], llm_max_completion_tokens=calls["max_tokens"],
        llm_timeout_seconds=calls["timeout_seconds"], llm_max_retries=calls["max_retries"],
    )


def require_comparable(settings: Any, baseline_run: dict[str, Any]) -> None:
    """Voice runs must use the text baseline's model, prompt and normalization, with raw output saved."""
    prompt = hashlib.sha256(load_system_prompt(settings).encode("utf-8")).hexdigest()
    differ = [name for name, ours, theirs in (
        ("model", settings.llm_model, baseline_run["model"]),
        ("prompt", prompt, baseline_run["prompt_sha256"]),
        ("ko_numeral_normalize", bool(settings.ko_numeral_normalize), bool(baseline_run["ko_numeral_normalize"])),
    ) if ours != theirs]
    if settings.llm_postprocess:
        differ.append("llm_postprocess (원 출력 저장 후 오프라인 후처리여야 함)")
    if differ:
        raise VoiceE2EError(f"텍스트 기준선과 조건이 다릅니다: {', '.join(differ)} — 텍스트 기준선을 새로 재야 비교 가능")


def stt_price(prices: dict[str, Any], model: str) -> dict[str, Any]:
    """Whisper $/min from configs/eval_prices.yaml; blank → refuse (the price is never guessed)."""
    entry = (prices.get("stt_models") or {}).get(model) or {}
    if entry.get("usd_per_minute") is None:
        raise VoiceE2EError(
            f"{model} 단가가 비어 있습니다 — 공식 가격표 확인 필요 (configs/eval_prices.yaml stt_models.{model})")
    return entry


def _stt_minutes(recordings: list[dict[str, Any]]) -> float:
    """Recorded minutes by length (unreadable length counted as ASSUMED_MAX_SECONDS) — the cost basis."""
    return sum((rec["seconds"] or ASSUMED_MAX_SECONDS) for rec in recordings) / 60


def _stt_minutes_upper(recordings: list[dict[str, Any]]) -> int:
    """Reference only: each recording rounded up to a whole minute (if billing rounded per call)."""
    return sum(math.ceil((rec["seconds"] or ASSUMED_MAX_SECONDS) / 60) for rec in recordings)


def estimate_cost(recordings: list[dict[str, Any]], llm_records: list[dict[str, Any]], *, settings: Any,
                  repeat: int, prices: dict[str, Any]) -> dict[str, Any]:
    """STT by recording length + upper-bound LLM cost (run_baseline.estimate_cost)."""
    stt = round(_stt_minutes(recordings) * stt_price(prices, settings.stt_model)["usd_per_minute"], 6)
    llm = baseline.estimate_cost(records=llm_records, settings_by_tag={"voice": settings},
                                 repeat=repeat, prices=prices)["total_usd"] if llm_records else 0.0
    return {"stt_usd": stt, "llm_usd": llm, "total_usd": round(stt + llm, 6),
            "cap_usd": prices.get("cost_cap_usd", 1.0)}


def _check_cap(estimate: dict[str, Any], stage: str) -> None:
    """Print the estimate and refuse to go on above the cap."""
    print(f"[예상 비용·{stage}] STT ${estimate['stt_usd']:.4f} + LLM ${estimate['llm_usd']:.4f} "
          f"= ${estimate['total_usd']:.4f} (상한 ${estimate['cap_usd']})")
    if estimate["total_usd"] > estimate["cap_usd"]:
        raise VoiceE2EError(f"예상 비용 ${estimate['total_usd']:.4f}가 상한 ${estimate['cap_usd']}를 넘어 실행하지 않습니다")


# ── STT ───────────────────────────────────────────────────────────────────────


def _seconds_label(seconds: float | None) -> str:
    return "길이 미확인" if seconds is None else f"{seconds:.1f}s"


def transcribe_all(recordings: list[dict[str, Any]], *, settings: Any,
                   stt: Callable[..., str]) -> list[dict[str, Any]]:
    """One STT call per recording; logs ID·alias·length·result only. Failures keep the error type/status."""
    results = []
    for rec in recordings:
        audio = rec["path"].read_bytes()
        result = {"golden_id": rec["golden_id"], "speaker": rec["speaker"], "file": rec["file"],
                  "seconds": rec["seconds"], "transcript": None, "error": None}
        try:
            result["transcript"] = stt(audio, rec["file"], AUDIO_MIME[rec["path"].suffix.lower()], settings=settings)
        except STTError as exc:
            cause = exc.__cause__ or exc
            result["error"] = {"type": type(cause).__name__, "status": getattr(cause, "status_code", None)}
        finally:
            del audio
        state = "성공" if result["error"] is None else f"실패 {result['error']['type']} status={result['error']['status']}"
        print(f"[STT] {rec['golden_id']} {rec['speaker']} {_seconds_label(rec['seconds'])} {state}")
        results.append(result)
    return results


def _voice_records(stt_results: list[dict[str, Any]], golden: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Golden copies with utterance_text = transcript, one per (ID, speaker) as `{golden_id}@{speaker}`."""
    records = []
    for result in stt_results:
        if result["transcript"] is None:
            continue
        record = dict(golden[result["golden_id"]])
        record["id"] = f"{result['golden_id']}@{result['speaker']}"
        record["utterance_text"] = result["transcript"]
        record["voice"] = {key: result[key] for key in ("golden_id", "speaker", "file", "seconds")}
        records.append(record)
    return records


# ── metrics ───────────────────────────────────────────────────────────────────


def _record_rows(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-record metric values plus credit flags (a metric without its sentence kind has no ids)."""
    wrong = set(metrics["credit_wrongly_confirmed"].get("ids", []))
    flips = metrics["credit_flip_saved"].get("ids", {})
    flipped = {record_id for ids in flips.values() for record_id in ids}
    return {row["id"]: {**{key: row[key] for key in METRIC_KEYS},
                        "credit_wrong": row["id"] in wrong, "credit_flip": row["id"] in flipped}
            for row in metrics["records"]}


def _verdict(row: dict[str, Any]) -> bool:
    """맞음 per record: transaction exact for normal sentences, follow-up appropriateness otherwise."""
    return bool(row["transaction_exact"] if row["transaction_exact"] is not None else row["confirmation_appropriate"])


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Correct/total/rate per metric over (recording × run) rows, plus credit error counts."""
    out: dict[str, Any] = {}
    for key in METRIC_KEYS:
        values = [row[key] for row in rows if row[key] is not None]
        out[key] = {"correct": sum(values), "total": len(values),
                    "rate": round(sum(values) / len(values), 6) if values else None}
    out["credit_wrongly_confirmed"] = sum(row["credit_wrong"] for row in rows)
    out["credit_flip_saved"] = sum(row["credit_flip"] for row in rows)
    return out


def _gap(text: dict[str, Any], voice: dict[str, Any]) -> dict[str, Any]:
    """Text minus voice in %p per rate (positive = voice is worse); credit errors as voice minus text."""
    gap = {key: None if text[key]["rate"] is None or voice[key]["rate"] is None
           else round((text[key]["rate"] - voice[key]["rate"]) * 100, 2) for key in METRIC_KEYS}
    for key in ("credit_wrongly_confirmed", "credit_flip_saved"):
        gap[key] = voice[key] - text[key]
    return gap


def _cer_text(text: str, *, normalize: bool) -> str:
    """Characters compared for CER: optional numeral normalization, then whitespace and punctuation removed."""
    if normalize:
        text = normalize_korean_numerals(" ".join(text.split())).text
    return re.sub(r"[\W_]", "", text)


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (insert/delete/substitute = 1)."""
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def key_info(text: str) -> dict[str, Counter]:
    """Amounts (number+원), quantities (other numbers) and payment/credit words after numeral normalization."""
    normalized = normalize_korean_numerals(" ".join(text.split())).text
    normalized = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", normalized)
    amounts = Counter(re.findall(r"(\d+(?:\.\d+)?)\s*원", normalized))
    numbers = Counter(re.findall(r"\d+(?:\.\d+)?", normalized))
    credit = Counter({term: normalized.count(term) for term in CREDIT_TERMS if term in normalized})
    return {"amount": amounts, "quantity": numbers - amounts, "credit": credit}


def stt_quality(reference: str, transcript: str) -> dict[str, Any]:
    """CER (whitespace·punctuation excluded, raw and numeral-normalized) and key-info counts for one recording."""
    out: dict[str, Any] = {}
    for name, normalize in (("cer", False), ("cer_normalized", True)):
        ref, hyp = _cer_text(reference, normalize=normalize), _cer_text(transcript, normalize=normalize)
        out[name] = {"edits": edit_distance(ref, hyp), "ref_chars": len(ref)}
    ref_info, hyp_info = key_info(reference), key_info(transcript)
    out["key_info"] = {kind: {"preserved": sum((ref_info[kind] & hyp_info[kind]).values()),
                              "total": sum(ref_info[kind].values())} for kind in ref_info}
    return out


def _cer_summary(qualities: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """Micro CER (total edits / total reference chars), mean and max per recording."""
    if not qualities:
        return {"micro": None, "mean": None, "max": None, "n": 0}
    per = [q[name]["edits"] / q[name]["ref_chars"] for q in qualities if q[name]["ref_chars"]]
    chars = sum(q[name]["ref_chars"] for q in qualities)
    return {"micro": round(sum(q[name]["edits"] for q in qualities) / chars, 6) if chars else None,
            "mean": round(sum(per) / len(per), 6) if per else None,
            "max": round(max(per), 6) if per else None, "n": len(qualities)}


def _key_info_summary(qualities: list[dict[str, Any]]) -> dict[str, Any]:
    """Preserved / total per kind (amount·quantity·credit) over all recordings."""
    out = {}
    for kind in ("amount", "quantity", "credit"):
        preserved = sum(q["key_info"][kind]["preserved"] for q in qualities)
        total = sum(q["key_info"][kind]["total"] for q in qualities)
        out[kind] = {"preserved": preserved, "total": total, "rate": round(preserved / total, 6) if total else None}
    return out


def stt_prompt_of(settings: Any) -> str:
    """STT prompt text read from STT_PROMPT_FILE (stripped, as stt.py sends it); unset = off ("").

    getattr: the setting arrives with the backend STT prompt PR. The server transcribes without the hint
    when the file is unreadable, so a measurement refuses a set path whose file is missing or blank.
    """
    configured = (getattr(settings, "stt_prompt_file", "") or "").strip()
    if not configured:
        return ""
    try:
        prompt = Path(configured).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise VoiceE2EError(f"STT_PROMPT_FILE을 읽을 수 없습니다 ({type(exc).__name__}): {configured}") from exc
    if not prompt:
        raise VoiceE2EError(f"STT_PROMPT_FILE이 비어 있습니다: {configured}")
    return prompt


def _stt_prompt_info(prompt: str) -> dict[str, Any]:
    """Prompt on/off with its hash and length only — the word list itself stays out of the results."""
    return {"on": bool(prompt), "chars": len(prompt),
            "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else None}


def _longest_common_run(a: str, b: str) -> int:
    """Length of the longest substring of `a` that also appears in `b`."""
    best = 0
    for start in range(len(a)):
        end = start + best + 1
        while end <= len(a) and a[start:end] in b:
            best, end = end - start, end + 1
    return best


def prompt_leaked(transcript: str, prompt: str) -> bool:
    """True when the transcript repeats the prompt (≥ PROMPT_LEAK_MIN_CHARS consecutive Hangul, spacing ignored)."""
    transcript_hangul, prompt_hangul = (re.sub(r"[^가-힣]", "", text) for text in (transcript, prompt))
    return _longest_common_run(transcript_hangul, prompt_hangul) >= PROMPT_LEAK_MIN_CHARS


def type_label(record: dict[str, Any]) -> str:
    """정상 · 표현 난이도 / 추가 확인 · 확인 종류 (Golden labels only, no sentence content)."""
    if record["expected_outcome"] == "정상 구조화 가능":
        return f"정상 · {record['difficulty_label']}"
    return f"추가 확인 · {record['confirm_type']}"


def build_summary(*, run_id: str, golden: dict[str, dict[str, Any]], stt_results: list[dict[str, Any]],
                  voice_runs: list[dict[str, Any]], text: dict[str, Any], gap_exclude: list[str],
                  settings: Any, git: dict[str, Any], prices: dict[str, Any], estimate: dict[str, Any],
                  repeat: int) -> dict[str, Any]:
    """Numbers and per-ID right/wrong only — the summary never holds utterances or transcripts."""
    text_runs = text["runs"]
    voice_rows: list[dict[str, Any]] = []
    text_rows: list[dict[str, Any]] = []
    per_recording = []
    qualities: dict[tuple[str, str], dict[str, Any]] = {}
    for result in stt_results:
        gid, speaker = result["golden_id"], result["speaker"]
        tag = {"golden_id": gid, "speaker": speaker, "type": type_label(golden[gid])}
        text_for = [{**tag, **run["rows"][gid]} for run in text_runs]
        text_rows.extend(text_for)
        if result["transcript"] is None:
            # STT failure stays in the denominator: wrong wherever the metric applies (503 → no candidate).
            failed = {**tag, **{key: None if text_for[0][key] is None else False for key in METRIC_KEYS},
                      "credit_wrong": False, "credit_flip": False}
            voice_for = [dict(failed) for _ in range(repeat)]
        else:
            qualities[(gid, speaker)] = stt_quality(golden[gid]["utterance_text"], result["transcript"])
            voice_for = [{**tag, **run["rows"][f"{gid}@{speaker}"]} for run in voice_runs]
        voice_rows.extend(voice_for)
        quality = qualities.get((gid, speaker))
        per_recording.append({
            **tag, "seconds": result["seconds"], "stt_ok": result["transcript"] is not None,
            "stt_error": result["error"],
            "cer": None if quality is None or not quality["cer"]["ref_chars"]
            else round(quality["cer"]["edits"] / quality["cer"]["ref_chars"], 4),
            "text_correct": f"{sum(_verdict(row) for row in text_for)}/{len(text_for)}",
            "voice_correct": f"{sum(_verdict(row) for row in voice_for)}/{len(voice_for)}",
        })

    def scope(keep: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        t = _aggregate([row for row in text_rows if keep(row)])
        v = _aggregate([row for row in voice_rows if keep(row)])
        return {"text": t, "voice": v, "gap_pp": _gap(t, v)}

    excluded = set(gap_exclude)
    speakers = sorted({row["speaker"] for row in voice_rows}, key=lambda s: int(s[1:]))
    types = sorted({row["type"] for row in voice_rows if row["golden_id"] not in excluded})
    known_seconds = [r["seconds"] for r in stt_results if r["seconds"] is not None]
    price = stt_price(prices, settings.stt_model)["usd_per_minute"]
    llm_cost = [run["cost_estimate_usd"] for run in voice_runs]
    succeeded = list(qualities.values())
    stt_prompt = stt_prompt_of(settings)
    leaked = [f"{r['golden_id']}@{r['speaker']}" for r in stt_results
              if stt_prompt and r["transcript"] is not None and prompt_leaked(r["transcript"], stt_prompt)]
    return {
        "run": {
            "id": run_id, "executed_at": datetime.now(baseline.KST).isoformat(timespec="seconds"),
            "git_commit": git["git_commit"], "git_dirty": git["git_dirty"],
            "golden_file": baseline._display_path(GOLDEN_DEV), "split": DEV_SPLIT,
            "stt_model": settings.stt_model, "stt_prompt": _stt_prompt_info(stt_prompt),
            "llm_model": settings.llm_model, "prompt_sha256": text["run"]["prompt_sha256"], "scoring_version": SCORING_VERSION,
            "postprocess_version": voice_runs[0]["postprocess_version"] if voice_runs else None,
            "normalizer_version": NORMALIZER_VERSION if settings.ko_numeral_normalize else None,
            "repeat": repeat, "recordings": len(stt_results), "speakers": speakers,
            "gap_exclude": sorted(excluded),
            "text_baseline": {"runs": list(TEXT_BASELINE_RUNS), "llm_commit": text["run"]["git_commit"],
                              "rescored_at": git["git_commit"],
                              "postprocess_version": text_runs[0]["postprocess_version"]},
        },
        "stt": {
            "succeeded": sum(r["transcript"] is not None for r in stt_results),
            "failed": [{"golden_id": r["golden_id"], "speaker": r["speaker"], **r["error"]}
                       for r in stt_results if r["error"]],
            "seconds": {"known": len(known_seconds), "unknown": len(stt_results) - len(known_seconds),
                        "total_known": round(sum(known_seconds), 2)},
            "cer": _cer_summary(succeeded, "cer"),
            "cer_normalized": _cer_summary(succeeded, "cer_normalized"),
            "key_info": _key_info_summary(succeeded),
            "prompt_leak": {"count": len(leaked), "ids": leaked} if stt_prompt else None,
        },
        "e2e": {
            "gap_excluded": scope(lambda row: row["golden_id"] not in excluded),
            "all_included": scope(lambda row: True),
        },
        "excluded_rows": [row for row in per_recording if row["golden_id"] in excluded],
        "by_speaker": {s: {**scope(lambda row, s=s: row["speaker"] == s and row["golden_id"] not in excluded),
                           "cer": _cer_summary([q for (g, sp), q in qualities.items() if sp == s], "cer")}
                       for s in speakers},
        "by_type": {t: scope(lambda row, t=t: row["type"] == t and row["golden_id"] not in excluded) for t in types},
        "records": per_recording,
        "cost": {
            "estimate_upper": estimate,
            "stt_usd_upper": round(_stt_minutes_upper(stt_results) * price, 6),
            "stt_usd_by_length": round(_stt_minutes(stt_results) * price, 6),
            "llm_usd": round(sum(c for c in llm_cost if c is not None), 6) if llm_cost else 0.0,
            "stt_price": stt_price(prices, settings.stt_model),
        },
    }


# ── report ────────────────────────────────────────────────────────────────────

_METRIC_NAMES = {"transaction_exact": "거래 전체 정확도", "amount_exact": "금액 Exact Match",
                 "confirmation_appropriate": "추가 확인 적합성"}


def _rate(metric: dict[str, Any]) -> str:
    return "N/A" if metric["rate"] is None else f"{metric['rate'] * 100:.2f}% ({metric['correct']}/{metric['total']})"


def _pp(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2f}%p"


def _scope_table(scope: dict[str, Any]) -> list[str]:
    lines = ["| 지표 | 텍스트 | 음성 | 하락 폭 (텍스트−음성) |", "|---|---:|---:|---:|"]
    for key in METRIC_KEYS:
        lines.append(f"| {_METRIC_NAMES[key]} | {_rate(scope['text'][key])} | {_rate(scope['voice'][key])} "
                     f"| {_pp(scope['gap_pp'][key])} |")
    for key, name in (("credit_wrongly_confirmed", "외상 잘못 확정"), ("credit_flip_saved", "외상 반대 저장")):
        lines.append(f"| {name} | {scope['text'][key]}건 | {scope['voice'][key]}건 | {scope['gap_pp'][key]:+d}건 |")
    return lines


def _prompt_label(info: dict[str, Any]) -> str:
    return f"켬 (sha256 `{info['sha256'][:12]}`, {info['chars']}자)" if info["on"] else "끔"


def _cer_line(name: str, cer: dict[str, Any]) -> str:
    if cer["micro"] is None:
        return f"- {name}: 측정 불가 (STT 성공 0건)"
    return (f"- {name}: 전체 {cer['micro'] * 100:.2f}% · 녹음별 평균 {cer['mean'] * 100:.2f}% · "
            f"최대 {cer['max'] * 100:.2f}% ({cer['n']}건)")


def render_report(summary: dict[str, Any]) -> str:
    """Markdown report from the summary — numbers and IDs only."""
    run, stt, e2e, cost = summary["run"], summary["stt"], summary["e2e"], summary["cost"]
    tb = run["text_baseline"]
    excluded = ", ".join(run["gap_exclude"]) or "없음"
    lines = [
        "# 음성 E2E 측정 (녹음 → STT → 구조화 → 채점)", "",
        f"- 실행 ID: `{run['id']}` · 실행 시각 `{run['executed_at']}`",
        f"- 코드: `{run['git_commit']}`" + (" (커밋 안 된 변경 포함)" if run["git_dirty"] else ""),
        f"- 데이터: `{run['golden_file']}` ({run['split']}) · 녹음 {run['recordings']}건 · 화자 {', '.join(run['speakers'])}",
        f"- STT `{run['stt_model']}` 1회 · 구조화 `{run['llm_model']}` {run['repeat']}회 (원 출력 → 후처리 "
        f"{run['postprocess_version']} + 검증, 채점기 {run['scoring_version']}, 정규화 {run['normalizer_version'] or 'off'})",
        f"- STT 도움말(prompt): {_prompt_label(run['stt_prompt'])}",
        f"- 텍스트 기준선: DL-11 mini 3회 원 출력(`{tb['llm_commit'][:7]}`)을 이 커밋에서 오프라인 재채점 "
        f"(후처리 {tb['postprocess_version']}) — 같은 녹음 구성으로 가중",
        f"- 하락 폭에서 뺀 ID: {excluded}", "",
        "## STT", "",
        f"- 성공 {stt['succeeded']}건 / {run['recordings']}건"
        + (" · 실패: " + ", ".join(f"{f['golden_id']} {f['speaker']} ({f['type']} status={f['status']})"
                                   for f in stt["failed"]) if stt["failed"] else ""),
        f"- 녹음 길이: 확인 {stt['seconds']['known']}건 합계 {stt['seconds']['total_known']}초 · 미확인 {stt['seconds']['unknown']}건",
        _cer_line("글자 오류율 CER (공백·문장부호 제외)", stt["cer"]),
        _cer_line("CER (한글 수사 정규화 후)", stt["cer_normalized"]),
        *([] if stt["prompt_leak"] is None else [
            f"- 도움말 낱말이 섞인 인식 결과 (한글 {PROMPT_LEAK_MIN_CHARS}자 이상 겹침): {stt['prompt_leak']['count']}건"
            + (f" ({', '.join(stt['prompt_leak']['ids'])})" if stt["prompt_leak"]["ids"] else "")]), "",
        "| 핵심 정보 | 보존 | 비율 |", "|---|---:|---:|",
    ]
    for kind, name in (("amount", "금액"), ("quantity", "수량"), ("credit", "외상·결제 표현")):
        info = stt["key_info"][kind]
        rate = "N/A" if info["rate"] is None else f"{info['rate'] * 100:.2f}%"
        lines.append(f"| {name} | {info['preserved']}/{info['total']} | {rate} |")
    lines += ["", f"## 구조화 지표 — 하락 폭 계산 ({excluded} 빼고)", "", *_scope_table(e2e["gap_excluded"]),
              "", "## 구조화 지표 — 전체 포함", "", *_scope_table(e2e["all_included"]), ""]
    if summary["excluded_rows"]:
        lines += ["## 하락 폭에서 뺀 ID (별도 표시)", "", "| ID | 화자 | 유형 | 텍스트 | 음성 |", "|---|---|---|---:|---:|"]
        lines += [f"| {r['golden_id']} | {r['speaker']} | {r['type']} | {r['text_correct']} | {r['voice_correct']} |"
                  for r in summary["excluded_rows"]]
        lines.append("")
    lines += ["## 화자별 (구조화 지표는 뺀 ID 제외, CER은 STT 성공 전체)", "", "| 화자 | CER | 거래 (텍스트→음성) | 금액 (텍스트→음성) | 추가 확인 (텍스트→음성) |",
              "|---|---:|---|---|---|"]
    for speaker, s in summary["by_speaker"].items():
        cer = "N/A" if s["cer"]["micro"] is None else f"{s['cer']['micro'] * 100:.2f}%"
        cells = " | ".join(f"{_rate(s['text'][k])} → {_rate(s['voice'][k])}" for k in METRIC_KEYS)
        lines.append(f"| {speaker} | {cer} | {cells} |")
    lines += ["", "## 유형별 (뺀 ID 제외)", "", "| 유형 | 거래 (텍스트→음성) | 금액 (텍스트→음성) | 추가 확인 (텍스트→음성) |",
              "|---|---|---|---|"]
    for name, s in summary["by_type"].items():
        lines.append(f"| {name} | " + " | ".join(f"{_rate(s['text'][k])} → {_rate(s['voice'][k])}" for k in METRIC_KEYS) + " |")
    lines += ["", "## ID별 (맞음 횟수 — 정상은 거래 전체, 추가 확인은 적합성)", "",
              "| ID | 화자 | 유형 | 길이 | CER | 텍스트 | 음성 |", "|---|---|---|---:|---:|---:|---:|"]
    for r in summary["records"]:
        cer = "STT 실패" if not r["stt_ok"] else ("N/A" if r["cer"] is None else f"{r['cer'] * 100:.1f}%")
        lines.append(f"| {r['golden_id']} | {r['speaker']} | {r['type']} | {_seconds_label(r['seconds'])} | {cer} "
                     f"| {r['text_correct']} | {r['voice_correct']} |")
    price = cost["stt_price"]
    lines += [
        "", "## 비용", "",
        f"- 실행 전 예상: ${cost['estimate_upper']['total_usd']:.4f} (상한 ${cost['estimate_upper']['cap_usd']})",
        f"- STT: 녹음 길이 기준 ${cost['stt_usd_by_length']:.4f} (참고: 건당 1분 올림이면 ${cost['stt_usd_upper']:.4f}) "
        f"(${price['usd_per_minute']}/분, {price.get('checked_at')} 확인, 길이 미확인 건은 {ASSUMED_MAX_SECONDS}초로 셈)",
        f"- LLM: ${cost['llm_usd']:.4f} (응답 토큰 × configs/eval_prices.yaml 단가)", "",
        "## 해석 제한", "",
        "- STT 실패는 분모에 남기고, 지표가 적용되는 칸은 모두 오답으로 셌다 (운영이면 503 → 텍스트 입력).",
        "- CER·핵심 정보는 Golden 원문 대비. Whisper가 숫자를 아라비아 숫자로 적으면 원 CER이 커지므로 정규화 후 값을 같이 본다.",
        "- 핵심 정보 보존은 한글 수사 정규화 후 숫자(원 앞 = 금액, 나머지 = 수량)와 결제·외상 단어 문자열 일치로 셌다.",
        "- 텍스트 기준선은 10/8 LLM 원 출력이다. 같은 문장이라도 LLM 응답은 실행마다 달라질 수 있다.",
        "",
    ]
    return "\n".join(lines)


# ── run ───────────────────────────────────────────────────────────────────────


def run(*, audio_dir: Path, manifest: Path, out_dir: Path, repeat: int = 3, delete_after: bool = False,
        gap_exclude: list[str] | None = None, settings: Any = None, prices: dict[str, Any] | None = None,
        stt: Callable[..., str] = transcribe, parser: Callable[..., Any] = parse_utterance_with_llm,
        results_dir: Path = RESULTS_DIR, run_id: str | None = None) -> dict[str, Any]:
    """Whole measurement; returns the summary written to `results_dir`. See the module docstring."""
    if repeat < 1:
        raise VoiceE2EError("--repeat는 1 이상이어야 합니다")
    audio_dir = require_outside_repo(audio_dir, "--audio-dir")
    out_dir = require_outside_repo(out_dir, "--out-dir")
    if not audio_dir.is_dir():
        raise VoiceE2EError("--audio-dir 폴더가 없습니다")
    golden = load_dev_golden()
    rows = load_manifest(manifest)
    unknown = sorted({row["golden_id"] for row in rows} - set(golden))
    gap_exclude = list(gap_exclude or [])
    unknown += sorted(set(gap_exclude) - set(golden))
    if unknown:
        raise VoiceE2EError(f"Dev Golden에 없는 ID: {unknown[:5]}")

    git = baseline._git_state()
    if git["git_dirty"]:  # same rule as run_baseline (paid runs reproducible from a commit), no override
        raise VoiceE2EError(f"커밋 안 된 변경이 있습니다 — 커밋한 뒤 실행하세요 (HEAD {git['git_commit']})")
    text = load_text_baseline()
    settings = settings or measurement_settings(text["run"])
    require_comparable(settings, text["run"])
    prices = prices if prices is not None else baseline.load_prices()
    stt_price(prices, settings.stt_model)
    stt_prompt_of(settings)  # a set but missing/blank prompt file stops here, before any API call
    recordings = check_recordings(rows, audio_dir, settings.stt_max_bytes)
    print(f"[목록] 녹음 {len(recordings)}건 · 화자 {len({r['speaker'] for r in recordings})}명")
    reference = [{**golden[r["golden_id"]], "utterance_text": golden[r["golden_id"]]["utterance_text"] * TRANSCRIPT_LENGTH_MARGIN}
                 for r in recordings]
    estimate = estimate_cost(recordings, reference, settings=settings, repeat=repeat, prices=prices)
    _check_cap(estimate, "실행 전")

    run_id = run_id or datetime.now(baseline.KST).strftime("voice-%Y%m%d-%H%M%S-kst")
    out_dir.mkdir(parents=True, exist_ok=True)
    stt_results = transcribe_all(recordings, settings=settings, stt=stt)
    records = _voice_records(stt_results, golden)
    transcripts_path = out_dir / f"{run_id}.transcripts.jsonl"
    with transcripts_path.open("x", encoding="utf-8") as stream:
        stream.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    # STT is spent; bound the LLM part again with the real transcripts before any LLM call.
    _check_cap(estimate_cost(recordings, records, settings=settings, repeat=repeat, prices=prices), "STT 후")

    voice_runs = []
    for index in range(1, repeat + 1):
        if not records:
            break
        summary = baseline.run_baseline(golden_path=transcripts_path, output_dir=out_dir, parser=parser,
                                        run_id=f"{run_id}-r{index}", settings=settings)
        scored = rescore(transcripts_path, out_dir / f"{run_id}-r{index}.predictions.jsonl",
                         postprocess=True, attach_validation=True)
        voice_runs.append({"rows": _record_rows(scored["metrics"]), "cost_estimate_usd": summary["cost_estimate_usd"],
                           "postprocess_version": scored["run"]["postprocess_version"]})
        print(f"[구조화] r{index} 완료 ({len(records)}건)")

    result = build_summary(run_id=run_id, golden=golden, stt_results=stt_results, voice_runs=voice_runs,
                           text=text, gap_exclude=gap_exclude, settings=settings, git=git, prices=prices,
                           estimate=estimate, repeat=repeat)
    results_dir.mkdir(parents=True, exist_ok=True)
    with (results_dir / f"{run_id}.summary.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    with (results_dir / f"{run_id}.report.md").open("x", encoding="utf-8") as stream:
        stream.write(render_report(result))
    print(f"[보고서] {baseline._display_path(results_dir / f'{run_id}.report.md')}")

    if delete_after and not result["stt"]["failed"]:
        for rec in recordings:
            rec["path"].unlink()
        print(f"[삭제] 녹음 {len(recordings)}건 삭제")
    else:
        reason = " (STT 실패가 있어 지우지 않음)" if delete_after else ""
        print(f"[삭제 남음] 녹음 {len(recordings)}건{reason} — 측정이 끝나면 직접 삭제하세요")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_voice_e2e", description=__doc__.splitlines()[0])
    parser.add_argument("--audio-dir", type=Path, required=True, help="녹음 폴더 (레포 밖 절대경로)")
    parser.add_argument("--manifest", type=Path, required=True, help="manifest.csv (golden_id, speaker, file)")
    parser.add_argument("--out-dir", type=Path, required=True, help="STT 결과·실행 기록 폴더 (레포 밖 절대경로)")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--delete-after", action="store_true", help="끝까지 성공한 뒤 manifest의 녹음만 삭제")
    parser.add_argument("--gap-exclude", nargs="*", default=[], metavar="GOLDEN_ID",
                        help="하락 폭 계산에서 뺄 ID (보고서에는 빼고/포함 두 값을 모두 적는다)")
    args = parser.parse_args(argv)
    # Measurement runs locally like score_voice_answer.py; production settings are not touched.
    os.environ.setdefault("APP_ENV", "development")
    try:
        run(audio_dir=args.audio_dir, manifest=args.manifest, out_dir=args.out_dir, repeat=args.repeat,
            delete_after=args.delete_after, gap_exclude=args.gap_exclude)
    except VoiceE2EError as exc:  # our refusals never carry sentence content
        print(f"[중단] {exc}")
    except Exception as exc:  # anything else may quote a sentence (str(exc)), so only the type is shown
        print(f"[중단] 예상하지 못한 오류 {type(exc).__name__} (문장 노출 방지로 내용은 출력하지 않음)")
    else:
        return 0
    print("[삭제 남음] 녹음은 지우지 않았습니다 — 측정이 끝나면 직접 삭제하세요")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
