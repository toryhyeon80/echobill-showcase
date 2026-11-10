"""STT noise check — synthetic silence/noise → backend `transcribe()` → voice-answer `interpret()` per field.

    python eval/run_stt_noise.py --out-dir D:/noise-out [--models whisper-1 gpt-4o-transcribe] \
        [--prompt-file configs/prompts/stt_prompt_ko.txt] [--repeat 3] [--keep-audio] \
        [--recordings-dir D:/answers --recording-models whisper-1 gpt-4o-transcribe --delete-recordings]

Before turning STT_PROMPT_FILE on: Whisper-family models can answer silence or noise with text (often the prompt).
A voice answer read from such text would fill a ledger field nobody said. The voice-answer judge set
(`score_voice_answer.py`) is text only and never reaches STT, so it cannot show this.

Flow: make CLIPS with ffmpeg (fixed commands and seeds, no human voice) in a temp folder outside the repo
→ for each model × prompt off/on × repeat, `transcribe()` once per clip → `interpret()` for credit · qty · total ·
counterparty → `<out-dir>/<run_id>.summary.json`·`.report.md`. Clips are deleted afterwards unless --keep-audio.

Pass per call: an empty transcript (STTError, as the server answers 503 → buttons) or all four fields failed.
Any filled field fails. Other STT errors (network, timeout) are neither pass nor fail and are counted apart.
Transcripts of synthetic sound hold no Golden sentence, so they are written to the report as they are.
Short-answer recordings (optional, `--recordings-dir`, outside the repo): `{alias}_{judge ID}.m4a`
(alias S1·S2…, ID from eval/data/voice_answer_judge_v1.jsonl). Each is transcribed with the prompt off per
recording model × repeat and scored with that ID's expected value (`score_voice_answer.judge_row`: hit · miss ·
wrong_fill · correct_fail). An Android "3gp4" file is remuxed with `ffmpeg -c copy` into a temp .m4a first.
Recording transcripts are never written anywhere (only lengths and interpreted values);
`--delete-recordings` deletes the recordings after a run with no STT error.

Estimated cost is printed first; above `cost_cap_usd` (configs/eval_prices.yaml) or with uncommitted changes
nothing runs. The OpenAI key is read from backend/.env like the server.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
for _path in (REPO_ROOT, BACKEND_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.config import Settings
from app.services.stt import STTError, STTRefused, build_stt_prompt, transcribe
from app.services.voice_answer import interpret

from eval import run_baseline as baseline
from eval.run_voice_e2e import (
    VoiceE2EError,
    prompt_leaked,
    require_outside_repo,
    stt_price,
    stt_prompt_of,
)
from eval.score_voice_answer import DEFAULT_SET as JUDGE_SET
from eval.score_voice_answer import FIELD_MAP, judge_row, load_set

DEFAULT_MODELS = ("whisper-1", "gpt-4o-mini-transcribe", "gpt-4o-transcribe")
FIELDS = ("credit", "qty", "total", "counterparty")
EMPTY_TRANSCRIPT = "STT returned an empty transcript"  # stt.py's message for a blank result
HINTS_CONDITION = Path("<counterparty-hints>")  # condition key: names sent as counterparty_hints, not a prompt file
# name, seconds, ffmpeg lavfi source, extra audio filter (None = none). Seeds fixed so every run sends the same sound.
CLIPS: tuple[tuple[str, float, str, str | None], ...] = (
    ("silence_1s", 1.0, "anullsrc=r=16000:cl=mono", None),
    ("silence_2s", 2.0, "anullsrc=r=16000:cl=mono", None),
    ("white_1500ms", 1.5, "anoisesrc=r=16000:color=white:amplitude=0.3:seed=1", None),
    ("pink_2s", 2.0, "anoisesrc=r=16000:color=pink:amplitude=0.3:seed=1", None),
    ("pink_quiet_2s", 2.0, "anoisesrc=r=16000:color=pink:amplitude=0.003:seed=2", None),
    ("white_quiet_1s", 1.0, "anoisesrc=r=16000:color=white:amplitude=0.003:seed=3", None),
    ("tone_quiet_1s", 1.0, "sine=f=440:r=16000", "volume=0.005"),
)
CLIP_MIME = "audio/ogg"
DEFAULT_RECORDING_MODELS = ("whisper-1", "gpt-4o-transcribe")
RECORDING_NAME = re.compile(r"^(S\d+)_(va-[a-z]+-\d{2})\.m4a$")
RECORDING_MIME = "audio/mp4"
# Cost basis when ffprobe cannot read a recording's length (same assumption as run_voice_e2e).
ASSUMED_RECORDING_SECONDS = 60


# ── clips ─────────────────────────────────────────────────────────────────────


def ffmpeg_command(ffmpeg: str, source: str, seconds: float, audio_filter: str | None, out: Path) -> list[str]:
    """Mono 16kHz ogg/opus 24kbps (the Android recording format), bit-exact so the file is the same every run."""
    command = [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", source, "-t", f"{seconds:g}", "-ac", "1"]
    if audio_filter:
        command += ["-af", audio_filter]
    return command + ["-c:a", "libopus", "-b:a", "24k", "-fflags", "+bitexact", "-flags:a", "+bitexact", str(out)]


def make_clips(work_dir: Path, *, ffmpeg: str | None = None,
               runner: Callable[..., Any] = subprocess.run) -> list[dict[str, Any]]:
    """Write CLIPS into `work_dir` (must be outside the repo); returns name·path·seconds·sha256 per clip."""
    work_dir = require_outside_repo(work_dir, "작업 폴더")
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        raise VoiceE2EError("ffmpeg가 없습니다 — 설치 후 PATH에 넣어 주세요")
    clips = []
    for name, seconds, source, audio_filter in CLIPS:
        path = work_dir / f"{name}.ogg"
        runner(ffmpeg_command(ffmpeg, source, seconds, audio_filter, path), check=True, capture_output=True)
        clips.append({"name": name, "path": path, "seconds": seconds,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return clips


# ── recordings ────────────────────────────────────────────────────────────────


def load_recordings(recordings_dir: Path, judge_rows: list[dict[str, Any]], max_bytes: int) -> list[dict[str, Any]]:
    """`{alias}_{ID}.m4a` files with their judge row; any other file name, unknown ID or size is refused."""
    recordings_dir = require_outside_repo(recordings_dir, "--recordings-dir")
    if not recordings_dir.is_dir():
        raise VoiceE2EError("--recordings-dir 폴더가 없습니다")
    by_id = {row["id"]: row for row in judge_rows}
    recordings = []
    for path in sorted(p for p in recordings_dir.iterdir() if p.is_file()):
        match = RECORDING_NAME.match(path.name)
        if not match:
            raise VoiceE2EError(f"{path.name}: 파일명은 가명_ID.m4a 여야 합니다 (예: S1_va-qty-01.m4a)")
        if match.group(2) not in by_id:
            raise VoiceE2EError(f"{path.name}: 판정 세트에 없는 ID입니다")
        if not 0 < path.stat().st_size <= max_bytes:
            raise VoiceE2EError(f"{path.name}: 파일 크기는 1바이트~{max_bytes}바이트여야 합니다 (STT_MAX_BYTES)")
        recordings.append({"alias": match.group(1), "id": match.group(2), "path": path, "row": by_id[match.group(2)]})
    if not recordings:
        raise VoiceE2EError("--recordings-dir에 녹음이 없습니다")
    return recordings


def is_3gp(data: bytes) -> bool:
    """MP4 container whose major brand is 3GP (Android "3gp4") — remuxed before it is sent as .m4a."""
    return data[4:8] == b"ftyp" and data[8:11] == b"3gp"


def prepare_recordings(recordings: list[dict[str, Any]], work_dir: Path, *, ffmpeg: str | None = None,
                       ffprobe: str | None = None, runner: Callable[..., Any] = subprocess.run) -> None:
    """Fill `audio` (3gp remuxed with `-c copy` into `work_dir`) and `seconds` (ffprobe; unknown → None)."""
    work_dir = require_outside_repo(work_dir, "작업 폴더")
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    ffprobe = ffprobe or shutil.which("ffprobe")
    for rec in recordings:
        data, rec["remuxed"], rec["seconds"] = rec["path"].read_bytes(), False, None
        if is_3gp(data):
            if not ffmpeg:
                raise VoiceE2EError("3gp4 녹음을 M4A로 바꾸려면 ffmpeg가 필요합니다")
            out = work_dir / rec["path"].name
            runner([ffmpeg, "-v", "error", "-y", "-i", str(rec["path"]), "-c", "copy", str(out)],
                   check=True, capture_output=True)
            data, rec["remuxed"] = out.read_bytes(), True
        rec["audio"] = data
        if ffprobe:
            probe = runner([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(rec["path"])], capture_output=True, text=True)
            try:
                rec["seconds"] = float(probe.stdout.strip())
            except ValueError:
                rec["seconds"] = None


def judge_recording(row: dict[str, Any], transcript: str | None, error: STTError | None,
                    *, settings: Any) -> dict[str, Any]:
    """Score one recording's transcript with its judge row; keeps length, value and the failure reason code, never the text.

    `reason` is interpret()'s fixed code (ambiguous · no_answer · no_number · …) when it failed, or
    `stt_empty` when STT returned nothing.
    """
    if error is not None and EMPTY_TRANSCRIPT not in str(error):
        return {"outcome": "stt_error", "error": str(error), "chars": 0, "status": None, "value": None, "unit": None,
                "reason": None}
    kwargs = {k: row[k] for k in ("choices", "item_names") if row.get(k) is not None}
    result = interpret(FIELD_MAP[row["field"]], transcript or "", settings=settings, **kwargs)
    reason = "stt_empty" if error is not None else result.reason
    return {"outcome": judge_row(row, result.status, result.value, result.unit), "error": None,
            "chars": len(transcript or ""), "status": result.status, "value": result.value, "unit": result.unit,
            "reason": reason}


def summarize_recordings(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per recording model: outcome counts and failure reasons, and per recording hits · wrong fills · reasons."""
    rows = []
    for model in dict.fromkeys(c["model"] for c in calls):
        group = [c for c in calls if c["model"] == model]
        counts = {o: sum(c["outcome"] == o for c in group)
                  for o in ("hit", "miss", "wrong_fill", "correct_fail", "stt_error")}
        per_recording: dict[str, dict[str, Any]] = {}
        for c in group:
            entry = per_recording.setdefault(f"{c['alias']}_{c['id']}",
                                             {"runs": 0, "hit": 0, "wrong_fill": 0, "reasons": {}})
            entry["runs"] += 1
            entry["hit"] += c["outcome"] == "hit"
            entry["wrong_fill"] += c["outcome"] == "wrong_fill"
            if c["outcome"] == "miss" and c.get("reason"):
                entry["reasons"][c["reason"]] = entry["reasons"].get(c["reason"], 0) + 1
        reasons = dict(Counter(c["reason"] for c in group if c["outcome"] == "miss" and c.get("reason")))
        rows.append({"model": model, "calls": len(group), **counts, "miss_reasons": reasons,
                     "per_recording": per_recording})
    return rows


# ── conditions · cost ─────────────────────────────────────────────────────────


def condition_settings(model: str, prompt_file: Path | None) -> Settings:
    """backend/.env settings with this STT model and prompt file ("" = off).

    getattr: STT_PROMPT_FILE arrives with the backend STT prompt PR (#137). Before it, Settings drops the
    unknown field, so a prompt-on condition is refused instead of silently measuring without the prompt.
    """
    settings = Settings(_env_file=BACKEND_ROOT / ".env", stt_model=model,
                        stt_prompt_file=str(prompt_file) if prompt_file else "")
    if prompt_file and not getattr(settings, "stt_prompt_file", ""):
        raise VoiceE2EError("이 backend에는 STT_PROMPT_FILE 설정이 없습니다 (#137 머지 전) — 도움말 있음 조건을 돌릴 수 없습니다")
    return settings


def estimate_cost(clips: list[dict[str, Any]], models: list[str], *, prompt_conditions: int, repeat: int,
                  prices: dict[str, Any], recordings: list[dict[str, Any]] = (),
                  recording_models: list[str] = ()) -> dict[str, Any]:
    """Audio minutes × $/min per model × repeat; per-call whole minutes as a reference.

    Clips run once per model × prompt condition, recordings once per recording model (prompt off).
    A recording whose length is unknown counts as ASSUMED_RECORDING_SECONDS.
    """
    clip_pass = (sum(c["seconds"] for c in clips) * prompt_conditions, len(clips) * prompt_conditions)
    rec_pass = (sum(r["seconds"] or ASSUMED_RECORDING_SECONDS for r in recordings), len(recordings))
    jobs = [(model, *clip_pass) for model in models] + [(model, *rec_pass) for model in recording_models]
    per_model: dict[str, float] = {}
    calls, upper = 0, 0.0
    for model, seconds, files in jobs:
        price = stt_price(prices, model)["usd_per_minute"]
        per_model[model] = round(per_model.get(model, 0.0) + seconds / 60 * repeat * price, 6)
        calls += files * repeat
        upper += files * repeat * price
    return {"per_model_usd": per_model, "total_usd": round(sum(per_model.values()), 6),
            "if_each_call_billed_1min_usd": round(upper, 6), "calls": calls,
            "cap_usd": prices.get("cost_cap_usd", 1.0)}


def check_cap(estimate: dict[str, Any]) -> None:
    """Print the estimate and refuse to run above the cap."""
    print(f"[예상 비용] ${estimate['total_usd']:.4f} (호출 {estimate['calls']}회, 호출마다 1분 과금이면 "
          f"${estimate['if_each_call_billed_1min_usd']:.4f}) · 상한 ${estimate['cap_usd']}")
    if estimate["total_usd"] > estimate["cap_usd"]:
        raise VoiceE2EError(f"예상 비용 ${estimate['total_usd']:.4f}가 상한 ${estimate['cap_usd']}를 넘어 실행하지 않습니다")


# ── judging ───────────────────────────────────────────────────────────────────


def _words(text: str) -> list[str]:
    return re.findall(r"[가-힣A-Za-z0-9]+", text)


def judge_call(transcript: str | None, error: STTError | None, prompt: str, *, settings: Any) -> dict[str, Any]:
    """One STT result → outcome (pass_empty · pass_unfilled · fail_filled · stt_error) with filled fields."""
    if error is not None:
        # a guard refusal (hint on: no speech / hint echo) is answered like an empty result — 503 → buttons
        outcome = "pass_empty" if isinstance(error, STTRefused) or EMPTY_TRANSCRIPT in str(error) else "stt_error"
        return {"outcome": outcome, "error": None if outcome == "pass_empty" else str(error),
                "transcript": None, "chars": 0, "prompt_word_hits": 0, "prompt_leak": False, "filled": {}}
    filled = {}
    for field in FIELDS:
        result = interpret(field, transcript, settings=settings)
        if result.status == "ok":
            filled[field] = result.value if result.unit is None else f"{result.value} {result.unit}"
    prompt_words = set(_words(prompt))
    return {"outcome": "fail_filled" if filled else "pass_unfilled", "error": None, "transcript": transcript,
            "chars": len(transcript), "prompt_word_hits": sum(word in prompt_words for word in _words(transcript)),
            "prompt_leak": bool(prompt) and prompt_leaked(transcript, prompt), "filled": filled}


def summarize(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per model × prompt condition: counts by outcome, which fields got filled, and distinct transcripts."""
    rows = []
    for key in dict.fromkeys((c["model"], c["prompt_on"]) for c in calls):
        group = [c for c in calls if (c["model"], c["prompt_on"]) == key]
        fields = sorted({field for c in group for field in c["filled"]})
        transcripts = sorted({c["transcript"] for c in group if c["transcript"]})
        counts = {o: sum(c["outcome"] == o for c in group)
                  for o in ("pass_empty", "pass_unfilled", "fail_filled", "stt_error")}
        rows.append({"model": key[0], "prompt_on": key[1], "calls": len(group), **counts,
                     "filled_fields": fields, "prompt_leak": sum(c["prompt_leak"] for c in group),
                     "max_prompt_word_hits": max((c["prompt_word_hits"] for c in group), default=0),
                     "transcripts": transcripts,
                     "verdict": "실패" if counts["fail_filled"] else ("판정 보류" if counts["stt_error"] else "통과")})
    return rows


def render_report(result: dict[str, Any]) -> str:
    """Markdown table per condition + every non-empty transcript (synthetic sound only)."""
    run = result["run"]
    lines = [
        f"# STT 무음·잡음 확인 — {run['run_id']}", "",
        (f"- 커밋 `{run['git_commit']}` · 소리 {len(result['clips'])}개 × {run['repeat']}회 · "
         f"예상 비용 ${result['cost_estimate']['total_usd']:.4f}"),
        (f"- 도움말: {run['stt_prompt']['chars']}자 · sha256 `{run['stt_prompt']['sha256']}`"
         + (f" · 거래처 힌트 {run['counterparty_hints']}곳을 서버 경로(counterparty_hints — 무음 거르기·되풀이 확인 포함)로 보냄"
            if run.get("counterparty_hints") else "")) if run["stt_prompt"]["on"]
        else "- 도움말: 없음 조건만",
        "- 통과: 빈 전사·안전장치 거절(STTError → 503) 또는 네 칸(credit·qty·total·counterparty) 모두 해석 실패. 하나라도 채워지면 실패.",
        "",
        "| 모델 | 도움말 | 호출 | 빈 전사 | 글자 있으나 안 채움 | **채워짐(실패)** | STT 오류 | 채워진 칸 | 도움말 되풀이 | 판정 |",
        "|---|---|--:|--:|--:|--:|--:|---|--:|---|",
    ]
    for row in result["conditions"]:
        lines.append(
            f"| {row['model']} | {'있음' if row['prompt_on'] else '없음'} | {row['calls']} | {row['pass_empty']} | "
            f"{row['pass_unfilled']} | **{row['fail_filled']}** | {row['stt_error']} | "
            f"{', '.join(row['filled_fields']) or '—'} | {row['prompt_leak']} | {row['verdict']} |")
    lines += ["", "## 전사가 나온 호출", "",
              "| 모델 | 도움말 | 소리 | 회 | 글자 수 | 도움말 낱말 겹침 | 채워진 칸 | 전사 |", "|---|---|---|--:|--:|--:|---|---|"]
    for c in result["calls"]:
        if c["transcript"] is None:
            continue
        filled = ", ".join(f"{k}={v}" for k, v in c["filled"].items()) or "—"
        text = c["transcript"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {c['model']} | {'있음' if c['prompt_on'] else '없음'} | {c['clip']} | {c['repeat']} | "
                     f"{c['chars']} | {c['prompt_word_hits']} | {filled} | {text} |")
    if result.get("recordings"):
        lines += _recording_report(result["recordings"])
    return "\n".join(lines) + "\n"


def _recording_report(recordings: dict[str, Any]) -> list[str]:
    """Short-answer section: per model counts, then per recording hits — values only, no transcript."""
    lines = ["", f"## 짧은 답 녹음 ({len(recordings['files'])}개, 도움말 없음)", "",
             "- 판정 세트 기대 해석값으로 채점 (hit = 값까지 맞음, wrong_fill = 틀린 값 채움). 전사 원문은 남기지 않음.", "",
             "| 모델 | 호출 | 맞음 | 해석 실패 | **틀린 값 채움** | 실패해야 할 답 실패 | STT 오류 | 해석 실패 이유 |",
             "|---|--:|--:|--:|--:|--:|--:|---|"]
    for row in recordings["conditions"]:
        lines.append(f"| {row['model']} | {row['calls']} | {row['hit']} | {row['miss']} | **{row['wrong_fill']}** | "
                     f"{row['correct_fail']} | {row['stt_error']} | {_reason_label(row.get('miss_reasons'))} |")
    models = [row["model"] for row in recordings["conditions"]]
    lines += ["", "| 녹음 | 기대값 | " + " | ".join(f"{m} 맞음/회" for m in models) + " |",
              "|---|---|" + "--:|" * len(models)]
    for file in recordings["files"]:
        key = f"{file['alias']}_{file['id']}"
        cells = []
        for row in recordings["conditions"]:
            entry = row["per_recording"].get(key, {"runs": 0, "hit": 0, "wrong_fill": 0})
            wrong = f" (틀린 값 {entry['wrong_fill']})" if entry["wrong_fill"] else ""
            reasons = f" [{_reason_label(entry.get('reasons'))}]" if entry.get("reasons") else ""
            cells.append(f"{entry['hit']}/{entry['runs']}{wrong}{reasons}")
        lines.append(f"| {key} | {file['expected']} | " + " | ".join(cells) + " |")
    return lines


def _reason_label(reasons: dict[str, int] | None) -> str:
    """{"ambiguous": 2, "no_number": 1} → "ambiguous 2, no_number 1" (— when none)."""
    return ", ".join(f"{code} {count}" for code, count in sorted((reasons or {}).items())) or "—"


def _transcribe(stt: Callable[..., str], audio: bytes, name: str, mime: str,
                settings: Any, hints: list[str] | None = None) -> tuple[str | None, STTError | None]:
    """One STT call → (transcript, None) or (None, STTError). `hints` go the server's counterparty_hints way."""
    try:
        if hints:
            return stt(audio, name, mime, counterparty_hints=hints, settings=settings), None
        return stt(audio, name, mime, settings=settings), None
    except STTError as exc:
        return None, exc


# ── run ───────────────────────────────────────────────────────────────────────


def run(*, out_dir: Path, models: list[str], prompt_file: Path | None = None, repeat: int = 3,
        keep_audio: bool = False, work_dir: Path | None = None, prices: dict[str, Any] | None = None,
        stt: Callable[..., str] = transcribe, settings_for: Callable[..., Any] = condition_settings,
        clip_maker: Callable[[Path], list[dict[str, Any]]] = make_clips, run_id: str | None = None,
        recordings_dir: Path | None = None, recording_models: list[str] = DEFAULT_RECORDING_MODELS,
        delete_recordings: bool = False, judge_set: Path = JUDGE_SET,
        recording_preparer: Callable[..., None] = prepare_recordings,
        counterparty_hints: list[str] | None = None) -> dict[str, Any]:
    """Whole check; returns the summary also written to `out_dir`. See the module docstring.

    `counterparty_hints` (PM 10/21): the "on" condition sends these names the way /parse-audio does with
    STT_COUNTERPARTY_HINTS on (transcribe's counterparty_hints — prompt line, speech gate, echo check),
    instead of a prompt file.
    """
    if repeat < 1:
        raise VoiceE2EError("--repeat는 1 이상이어야 합니다")
    if counterparty_hints and prompt_file:
        raise VoiceE2EError("--prompt-file과 --counterparty-hints는 따로 돌려 주세요")
    git = baseline._git_state()
    if git["git_dirty"]:  # paid runs reproducible from a commit (run_baseline · run_voice_e2e rule)
        raise VoiceE2EError(f"커밋 안 된 변경이 있습니다 — 커밋한 뒤 실행하세요 (HEAD {git['git_commit']})")
    prompt_path = prompt_file.resolve() if prompt_file else (HINTS_CONDITION if counterparty_hints else None)
    recording_models = list(recording_models) if recordings_dir else []
    conditions = [(model, path) for model in models for path in ([None, prompt_path] if prompt_path else [None])]
    settings_by_condition = {(model, path): settings_for(model, None if path == HINTS_CONDITION else path)
                             for model, path in conditions + [(model, None) for model in recording_models]}
    key = getattr(settings_by_condition[conditions[0]], "openai_api_key", None)
    if stt is transcribe and not (key and key.get_secret_value()):
        raise VoiceE2EError("OPENAI_API_KEY가 없습니다 (backend/.env)")
    if counterparty_hints:  # what the server sends as `prompt` (no STT_PROMPT_FILE in this condition)
        prompt = build_stt_prompt("", counterparty_hints)
    else:
        prompt = stt_prompt_of(settings_by_condition[conditions[-1]]) if prompt_path else ""  # missing/blank → stop
    prices = prices if prices is not None else baseline.load_prices()
    for model in [*models, *recording_models]:
        stt_price(prices, model)  # a model without a price stops before any sound or call
    recordings = (load_recordings(recordings_dir, load_set(judge_set),
                                  getattr(settings_by_condition[conditions[0]], "stt_max_bytes", 5_000_000))
                  if recordings_dir else [])

    created = work_dir is None
    work_dir = Path(tempfile.mkdtemp(prefix="echobill-stt-noise-")) if created else work_dir
    try:
        clips = clip_maker(work_dir)
        if recordings:
            recording_preparer(recordings, work_dir)
        estimate = estimate_cost(clips, models, prompt_conditions=2 if prompt_path else 1, repeat=repeat, prices=prices,
                                 recordings=recordings, recording_models=recording_models)
        check_cap(estimate)
        run_id = run_id or datetime.now(baseline.KST).strftime("stt-noise-%Y%m%d-%H%M%S-kst")
        calls = []
        for model, path in conditions:
            settings = settings_by_condition[(model, path)]
            for index in range(1, repeat + 1):
                for clip in clips:
                    transcript, error = _transcribe(stt, clip["path"].read_bytes(), clip["path"].name, CLIP_MIME,
                                                    settings, counterparty_hints if path == HINTS_CONDITION else None)
                    judged = judge_call(transcript, error, prompt if path else "", settings=settings)
                    calls.append({"model": model, "prompt_on": path is not None, "clip": clip["name"],
                                  "repeat": index, **judged})
                    print(f"[STT] {model} 도움말{'있음' if path else '없음'} r{index} {clip['name']} → {judged['outcome']}")
        recording_calls = []
        for model in recording_models:
            settings = settings_by_condition[(model, None)]
            for index in range(1, repeat + 1):
                for rec in recordings:
                    transcript, error = _transcribe(stt, rec["audio"], rec["path"].name, RECORDING_MIME, settings)
                    judged = judge_recording(rec["row"], transcript, error, settings=settings)
                    recording_calls.append({"model": model, "alias": rec["alias"], "id": rec["id"],
                                            "repeat": index, **judged})
                    print(f"[녹음] {model} r{index} {rec['alias']}_{rec['id']} → {judged['outcome']}")
    finally:
        if created and not keep_audio:
            shutil.rmtree(work_dir, ignore_errors=True)

    result = {
        "run": {"run_id": run_id, "git_commit": git["git_commit"], "repeat": repeat, "models": models,
                "counterparty_hints": len(counterparty_hints or []),
                "stt_prompt": {"on": bool(prompt), "chars": len(prompt),
                               "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else None}},
        "clips": [{k: clip[k] for k in ("name", "seconds", "sha256")} for clip in clips],
        "cost_estimate": estimate, "conditions": summarize(calls), "calls": calls,
    }
    if recordings:
        result["recordings"] = {
            "models": recording_models, "prompt_on": False,
            "files": [{"alias": r["alias"], "id": r["id"], "field": r["row"]["field"], "seconds": r["seconds"],
                       "remuxed_3gp": r["remuxed"], "expected": _expected_label(r["row"])} for r in recordings],
            "conditions": summarize_recordings(recording_calls), "calls": recording_calls,
        }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{run_id}.summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                                    encoding="utf-8")
    (out_dir / f"{run_id}.report.md").write_text(render_report(result), encoding="utf-8")
    print(f"[보고서] {out_dir / f'{run_id}.report.md'}")
    if keep_audio or not created:
        print(f"[소리 남음] {work_dir}")
    if recordings:
        if delete_recordings and not any(c["outcome"] == "stt_error" for c in recording_calls):
            for rec in recordings:
                rec["path"].unlink()
            print(f"[삭제] 녹음 {len(recordings)}개 삭제")
        else:
            reason = " (STT 오류가 있어 지우지 않음)" if delete_recordings else ""
            print(f"[삭제 남음] 녹음 {len(recordings)}개{reason} — 측정이 끝나면 직접 삭제하세요")
    return result


def _expected_label(row: dict[str, Any]) -> str:
    """Expected interpretation as shown in the report (value + unit, or 실패 for answers that must fail)."""
    if row["expected_status"] != "ok":
        return "실패"
    return f"{row['expected_value']}{row.get('expected_unit') or ''}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_stt_noise", description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, required=True, help="summary.json·report.md 폴더")
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--prompt-file", type=Path, help="주면 도움말 없음·있음 두 조건, 없으면 없음만")
    parser.add_argument("--counterparty-hints", type=Path,
                        help="거래처 이름 파일(한 줄에 하나) — 도움말 있음 조건을 서버의 거래처 힌트 경로로 보냄")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--keep-audio", action="store_true", help="합성 소리를 지우지 않음 (임시 폴더 경로 출력)")
    parser.add_argument("--recordings-dir", type=Path, help="짧은 답 녹음 폴더 (레포 밖, 가명_ID.m4a)")
    parser.add_argument("--recording-models", nargs="+", default=list(DEFAULT_RECORDING_MODELS))
    parser.add_argument("--delete-recordings", action="store_true", help="STT 오류 없이 끝나면 녹음 삭제")
    args = parser.parse_args(argv)
    os.environ.setdefault("APP_ENV", "development")  # local measurement; production settings untouched
    try:
        hints = ([line.strip() for line in args.counterparty_hints.read_text(encoding="utf-8").splitlines() if line.strip()]
                 if args.counterparty_hints else None)
        run(out_dir=args.out_dir, models=args.models, prompt_file=args.prompt_file, repeat=args.repeat,
            counterparty_hints=hints,
            keep_audio=args.keep_audio, recordings_dir=args.recordings_dir,
            recording_models=args.recording_models, delete_recordings=args.delete_recordings)
    except VoiceE2EError as exc:
        print(f"[중단] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
