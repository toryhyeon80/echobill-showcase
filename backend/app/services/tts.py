"""Text-to-speech adapter — the one place that calls the TTS vendor (읽어 주기, PM 10/26).

Phone built-in voices sound robotic and stay silent in Galaxy KakaoTalk, so the server makes the mp3.
Model · voice · tone instructions come from env / configs (CLAUDE.md §4-13). The text is never logged —
it holds counterparty names.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

from openai import OpenAI, OpenAIError

from app.config import Settings, get_settings
from app.services.tts_numbers import korean_numbers

logger = logging.getLogger(__name__)

# Same tone as the recorded failure-guide files (PM 10/26 설정값.md)
DEFAULT_INSTRUCTIONS_FILE = Path(__file__).resolve().parents[3] / "configs" / "prompts" / "tts_instructions_ko.txt"


class TTSError(RuntimeError):
    """No audio — the API answers 503 and the FE just shows the text."""


def load_instructions(settings: Settings) -> str:
    """Tone instructions from configs; an unreadable file is a TTSError (path name only in the message)."""
    path = Path(settings.tts_instructions_file) if settings.tts_instructions_file else DEFAULT_INSTRUCTIONS_FILE
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("TTS failed: instructions file not readable (%s): %s", type(exc).__name__, path.name)
        raise TTSError(f"TTS instructions file is not readable: {path.name}") from exc


def synthesize(text: str, *, settings: Settings | None = None, client: Any = None) -> bytes:
    """
    Korean text → mp3 bytes. One call under TTS_TIMEOUT_SECONDS for the whole request (SDK timeouts
    are per network phase, so a slow trickle of audio could run past them); no retries.
    Missing key, vendor error, timeout or empty audio → TTSError. `client` replaces OpenAI in tests.
    """
    settings = settings or get_settings()
    if not settings.openai_api_key or not settings.openai_api_key.get_secret_value():
        raise TTSError("OPENAI_API_KEY is not configured")
    request = {"model": settings.tts_model, "voice": settings.tts_voice, "input": korean_numbers(text),
               "response_format": "mp3", "instructions": load_instructions(settings)}
    api_client = client or OpenAI(
        api_key=settings.openai_api_key.get_secret_value(), timeout=settings.tts_timeout_seconds, max_retries=0,
    )
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        audio = executor.submit(lambda: api_client.audio.speech.create(**request).read()).result(
            timeout=settings.tts_timeout_seconds
        )
    except FutureTimeout as exc:
        logger.warning("TTS failed: deadline %ss exceeded", f"{settings.tts_timeout_seconds:g}")
        raise TTSError(f"TTS exceeded {settings.tts_timeout_seconds:g} seconds") from exc
    except OpenAIError as exc:
        # type/status only — never the text
        logger.warning("TTS failed: %s status=%s", type(exc).__name__, getattr(exc, "status_code", None))
        raise TTSError(f"TTS request failed: {type(exc).__name__}") from exc
    finally:
        # Do not wait for an abandoned call; its own timeout ends that thread.
        executor.shutdown(wait=False, cancel_futures=True)
    if not audio:
        logger.warning("TTS failed: empty audio")
        raise TTSError("TTS returned no audio")
    return audio
