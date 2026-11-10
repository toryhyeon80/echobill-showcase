"""Speech-to-text adapter — the one place that calls an STT vendor (DL-16)."""

import logging
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI, OpenAIError

from app.config import Settings, get_settings
from app.services.audio_convert import AudioConvertError, to_wav
from app.services.audio_gate import looks_like_speech

logger = logging.getLogger(__name__)


class STTError(RuntimeError):
    """STT could not return text — the API answers 503 and the FE switches to text input."""


class STTRefused(STTError):
    """Refused on purpose with the hint on: no speech in the recording, or the transcript is the hint list."""


# Paths already warned about — a missing file is logged once per process, not on every request
_warned_unreadable_prompt: set[str] = set()

# CLOVA Speech short-sentence recognition (PM 10/19) — formats it reads as-is; others go through to_wav
CLOVA_MIME_TYPES = {"audio/mpeg", "audio/wav", "audio/x-wav", "audio/ogg", "audio/flac", "audio/aac"}
CLOVA_STT_PATH = "/recog/v1/stt"
CLOVA_BOOSTINGS_MAX_CHARS = 512  # whole tab-joined string (API limit)
CLOVA_BOOSTING_MIN_CHARS = 3  # shorter keywords are not boosted by the API


def load_stt_prompt(settings: Any) -> str:
    """Read the STT vocabulary hint from configs; "" when STT_PROMPT_FILE is unset, blank or unreadable.

    An unreadable file only drops the hint (one warning, path only) so transcription still runs
    with the same request as when the setting is off.
    """
    configured = (getattr(settings, "stt_prompt_file", "") or "").strip()
    if not configured:
        return ""
    try:
        return Path(configured).read_text(encoding="utf-8").strip()
    except OSError as exc:
        if configured not in _warned_unreadable_prompt:
            _warned_unreadable_prompt.add(configured)
            logger.warning("STT prompt file not readable (%s), transcribing without it: %s",
                           type(exc).__name__, configured)
        return ""


def build_stt_prompt(base: str, counterparty_hints: Sequence[str] = ()) -> str:
    """Vocabulary hint + the owner's counterparty names as one "거래처: A, B" line ("" when both empty)."""
    lines = [base] if base else []
    if counterparty_hints:
        lines.append("거래처: " + ", ".join(counterparty_hints))
    return "\n".join(lines)


_HANGUL_NAME = re.compile(r"[가-힣 ]+")


def clova_boostings(counterparty_hints: Sequence[str]) -> str:
    """Owner's names as CLOVA `boostings`: tab-joined, Hangul only, 3+ chars each, ≤512 chars in total ("" when none).

    CLOVA documents boostings as Korean only — a name like "GS25" or "ABC상사" is skipped, so CLOVA
    never rejects the whole request (and the owner's every voice input with it) over one name (병철 #168 리뷰).
    """
    kept: list[str] = []
    used = 0
    for raw in counterparty_hints:
        name = " ".join(raw.split())  # no tab may survive inside a name
        if len(name) < CLOVA_BOOSTING_MIN_CHARS or not _HANGUL_NAME.fullmatch(name):
            continue
        cost = len(name) + (1 if kept else 0)
        if used + cost > CLOVA_BOOSTINGS_MAX_CHARS:
            continue
        kept.append(name)
        used += cost
    return "\t".join(kept)


# PM 10/21 noise check (gpt-4o-transcribe, hint line on): silence or noise came back as the whole hint
# list 17 times in 21. A real utterance names one counterparty (two at most — multi-counterparty is a 400),
# so a transcript holding this many listed names is the prompt, not speech.
HINT_ECHO_MIN_NAMES = 3


def echoes_hints(text: str, counterparty_hints: Sequence[str]) -> bool:
    """
    True when the transcript repeats the hint list: a "거래처:" label or HINT_ECHO_MIN_NAMES+ listed names.
    Names are matched longest first and removed as found, so "영수네 가게" never also counts "영수네".
    """
    if not counterparty_hints:
        return False
    rest = "".join(text.split())
    if rest.startswith("거래처:"):
        return True
    found = 0
    for name in sorted({"".join(n.split()) for n in counterparty_hints}, key=len, reverse=True):
        if len(name) >= 2 and name in rest:
            found += 1
            rest = rest.replace(name, " ")
    return found >= HINT_ECHO_MIN_NAMES


def _with_deadline(call: Callable[[], Any], settings: Settings, errors: tuple[type[Exception], ...]) -> Any:
    """
    Run one vendor call under the whole-call deadline; vendor errors and timeouts → STTError.

    SDK/HTTP timeouts are per network phase (connect · upload · read), so a slow upload plus a slow
    reply can run far past them. Waiting on a worker thread with a whole-call deadline makes the
    server answer 503 before the FE's own request timeout fires (CLAUDE.md §4: 503 ≠ network error).
    """
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        return executor.submit(call).result(timeout=settings.stt_timeout_seconds)
    except TimeoutError as exc:
        logger.warning("STT failed: deadline %ss exceeded", f"{settings.stt_timeout_seconds:g}")
        raise STTError(f"STT exceeded {settings.stt_timeout_seconds:g} seconds") from exc
    except errors as exc:
        # type/status only — no audio or transcript in logs
        logger.warning("STT failed: %s status=%s", type(exc).__name__, getattr(exc, "status_code", None))
        raise STTError(f"STT request failed: {type(exc).__name__}") from exc
    finally:
        # Do not wait for an abandoned call; its own timeout ends that thread.
        executor.shutdown(wait=False, cancel_futures=True)


def _transcribe_openai(
    audio: bytes, filename: str, mime: str, counterparty_hints: Sequence[str], settings: Settings, client: Any
) -> str:
    """OpenAI transcription (stt_model) with the vocabulary file + names as `prompt`."""
    if not settings.openai_api_key or not settings.openai_api_key.get_secret_value():
        raise STTError("OPENAI_API_KEY is not configured")

    request: dict[str, Any] = {"model": settings.stt_model, "file": (filename, audio, mime), "language": "ko"}
    prompt = build_stt_prompt(load_stt_prompt(settings), counterparty_hints)
    if prompt:  # empty → no prompt argument at all, the same request as before STT_PROMPT_FILE
        request["prompt"] = prompt

    api_client = client or OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.stt_timeout_seconds,
        max_retries=0,
    )
    result = _with_deadline(lambda: api_client.audio.transcriptions.create(**request), settings, (OpenAIError,))
    return (getattr(result, "text", "") or "").strip()


def _clova_url(invoke_url: str) -> str:
    """Invoke URL as copied from the builder, with or without the /recog/v1/stt path."""
    url = invoke_url.strip().rstrip("/")
    return url if url.endswith(CLOVA_STT_PATH) else url + CLOVA_STT_PATH


def _transcribe_clova(
    audio: bytes, mime: str, counterparty_hints: Sequence[str], settings: Settings, client: Any
) -> str:
    """
    CLOVA Speech short-sentence recognition (≤60 s). webm · mp4 are converted to wav first (in memory /
    a private temp dir removed at once). The vocabulary file is OpenAI prose and is not sent; only the
    owner's names go as `boostings`.
    """
    secret = settings.clova_speech_secret_key.get_secret_value()
    if not secret or not settings.clova_speech_invoke_url.strip():
        raise STTError("CLOVA Speech is not configured")

    params = {"lang": "Kor"}
    boostings = clova_boostings(counterparty_hints)
    if boostings:
        params["boostings"] = boostings

    def call() -> httpx.Response:
        body = audio if mime in CLOVA_MIME_TYPES else to_wav(audio, timeout=settings.stt_timeout_seconds)
        http = client or httpx.Client(timeout=settings.stt_timeout_seconds)
        try:
            return http.post(
                _clova_url(settings.clova_speech_invoke_url),
                params=params,
                headers={"Content-Type": "application/octet-stream", "X-CLOVASPEECH-API-KEY": secret},
                content=body,
            )
        finally:
            if client is None:
                http.close()

    response = _with_deadline(call, settings, (httpx.HTTPError, AudioConvertError))
    if response.status_code != 200:
        logger.warning("STT failed: clova status=%s", response.status_code)
        raise STTError(f"CLOVA Speech answered {response.status_code}")
    try:
        text = response.json().get("text")
    except (ValueError, AttributeError) as exc:
        logger.warning("STT failed: clova response not JSON")
        raise STTError("CLOVA Speech response is not JSON") from exc
    return (text or "").strip() if isinstance(text, str) else ""


def _require_speech(audio: bytes, settings: Settings) -> None:
    """
    With hints on, silence or steady noise never reaches the vendor (PM 10/21 (b)): gpt-4o-transcribe answers
    it with the owner's names. A recording the converter cannot read goes on as before (the echo check after
    STT still applies) — the gate must not turn a readable-by-vendor recording into a 503.
    """
    try:
        wav = to_wav(audio, timeout=settings.stt_timeout_seconds)
    except AudioConvertError as exc:
        logger.warning("speech check skipped: %s", type(exc).__name__)
        return
    if not looks_like_speech(wav):
        logger.warning("STT skipped: recording is silence or steady noise")
        raise STTRefused("recording has no speech")


def transcribe(
    audio: bytes,
    filename: str,
    mime: str,
    *,
    counterparty_hints: Sequence[str] = (),
    settings: Settings | None = None,
    client: Any = None,
) -> str:
    """
    Send in-memory audio to the configured STT vendor and return the Korean transcript.

    STT_PROVIDER picks the vendor (openai by default). `counterparty_hints` (the owner's own names,
    see stt_hints) go as the OpenAI prompt line or CLOVA boostings; empty → the same request as before.
    `client` replaces the vendor client in tests. Any vendor failure or empty text → STTError (503), and so
    does a transcript that only repeats the hint list (`echoes_hints`) — silence must not become names.
    With hints, a recording of silence or steady noise is refused before any vendor call (`audio_gate`).
    """
    settings = settings or get_settings()
    if counterparty_hints:
        _require_speech(audio, settings)
    if settings.stt_provider == "clova":
        text = _transcribe_clova(audio, mime, counterparty_hints, settings, client)
    else:
        text = _transcribe_openai(audio, filename, mime, counterparty_hints, settings, client)
    if not text:
        raise STTError("STT returned an empty transcript")
    if echoes_hints(text, counterparty_hints):
        # count only — the transcript is the owner's names (PII), never logged
        logger.warning("STT failed: transcript repeats the counterparty hint (%d names sent)", len(counterparty_hints))
        raise STTRefused("STT returned the counterparty hint instead of speech")
    return text
