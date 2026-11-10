"""읽어 주기 (PM 10/26) — a short Korean sentence → mp3 in the fixed EchoBill voice.

For 되묻기 questions and failure guides the FE also shows as text. Nothing is stored and nothing changes,
so no X-Idempotency-Key; each call, failed or not, counts toward its own per-user cap (SPEAK_LIMIT_*). The text is never
logged (counterparty names) — only its length on failure.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.dependencies import CurrentUserId
from app.schemas.errors import ErrorResponse
from app.services import rate_limit
from app.services.idempotency import error_json
from app.services.tts import TTSError, synthesize

logger = logging.getLogger(__name__)
router = APIRouter()


class SpeakRequest(BaseModel):
    """The sentence to read, as shown on screen."""

    text: str


@router.post(
    "/speak",
    response_class=Response,
    responses={
        200: {"content": {"audio/mpeg": {}}, "description": "mp3"},
        400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 413: {"model": ErrorResponse},
        429: {"model": ErrorResponse}, 503: {"model": ErrorResponse},
    },
)
def speak(body: SpeakRequest, user_id: CurrentUserId, db: Annotated[Session, Depends(get_db)]) -> Response:
    """
    200 audio/mpeg · 400 TEXT_EMPTY / TEXT_TOO_LONG (over TTS_MAX_CHARS) · 401 login ·
    429 RATE_LIMITED + Retry-After · 503 EXTERNAL_API_TIMEOUT when TTS fails (FE keeps showing the text).
    """
    settings = get_settings()
    text = body.text.strip()
    if not text:
        return error_json(400, "TEXT_EMPTY", "읽어 줄 문장이 없습니다.")
    if len(text) > settings.tts_max_chars:
        return error_json(400, "TEXT_TOO_LONG", f"문장은 {settings.tts_max_chars}자까지 읽어 줄 수 있습니다.")

    limited = rate_limit.count_use(db, user_id, "speak", settings)
    if limited is not None:
        return limited
    try:
        audio = synthesize(text, settings=settings)
    except TTSError:
        logger.warning("voice_speak failed chars=%d", len(text))
        return error_json(503, "EXTERNAL_API_TIMEOUT", "음성을 만들지 못했습니다.")
    return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})
