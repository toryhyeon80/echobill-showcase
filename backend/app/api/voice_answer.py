"""DL-31 voice answer — field name + short recording → STT → per-field rule → value candidate.

Stores nothing (only the 24h idempotency record), transcript never logged — one `voice_answer` log line
per new answer (field · ok/failed · reason) feeds the per-field success rate in ops metrics. The FE fills the
value into the existing repair submit (parser_type=manual, validation only); the user still
confirms. Per-field on/off via VOICE_ANSWER_FIELDS; voice usage cap shared with /parse-audio.
"""

import hashlib
import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Form, Header, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.ledger import AUDIO_MIME_TYPES
from app.config import get_settings
from app.db import get_db
from app.dependencies import CurrentUserId
from app.schemas.errors import ErrorResponse
from app.services.idempotency import run_idempotent
from app.services.stt import STTError, transcribe
from app.services.voice_answer import interpret

logger = logging.getLogger(__name__)
router = APIRouter()

Field_ = Literal["credit", "qty", "total", "counterparty"]


class VoiceAnswerResponse(BaseModel):
    """status ok → value (+ unit for qty). failed → reason; FE asks again (max 2) then buttons."""

    field: Field_
    status: Literal["ok", "failed"]
    value: str | int | bool | None = None
    unit: str | None = None
    reason: str | None = None
    utterance_text: str  # what was heard — shown back to the user, never logged


def _error(code: str, message: str) -> dict[str, Any]:
    return ErrorResponse(error_code=code, message=message).model_dump(mode="json")


def _log_result(field: str, status: str, reason: str | None) -> None:
    """One countable line per new answer (an idempotent replay does not re-run, so is not re-counted).
    Field, status and reason only — never the transcript or the value. WARNING like `postprocess_400`:
    app loggers have no handler of their own, so INFO would not reach the Railway log."""
    logger.warning("voice_answer field=%s status=%s reason=%s", field, status, reason or "-")


def _answer(
    field: str, audio: bytes, filename: str, mime: str, choices: list[int] | None, item_names: list[str] | None
) -> tuple[int, dict[str, Any]]:
    settings = get_settings()
    if mime not in AUDIO_MIME_TYPES or not audio or len(audio) > settings.stt_max_bytes:
        _log_result(field, "failed", "audio_invalid")
        return 400, _error("AUDIO_INVALID", "녹음 파일을 처리할 수 없습니다. 버튼으로 골라 주세요.")
    try:
        text = transcribe(audio, filename, mime)
    except STTError:
        _log_result(field, "failed", "stt_failed")
        return 503, _error("EXTERNAL_API_TIMEOUT", "음성을 알아듣지 못했습니다. 버튼으로 골라 주세요.")
    result = interpret(field, text, choices=choices, item_names=item_names, settings=settings)
    _log_result(field, result.status, result.reason)
    return 200, VoiceAnswerResponse(
        field=field, status=result.status, value=result.value, unit=result.unit,
        reason=result.reason, utterance_text=text,
    ).model_dump(mode="json")


@router.post(
    "/voice-answer",
    response_model=VoiceAnswerResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
               413: {"model": ErrorResponse}, 429: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def voice_answer(
    field: Annotated[Field_, Form()],
    file: UploadFile,
    user_id: CurrentUserId,
    db: Annotated[Session, Depends(get_db)],
    choices: Annotated[str | None, Form(description="산술 모순: 화면에 보인 두 금액 '30000,35000'")] = None,
    item_names: Annotated[
        str | None, Form(max_length=300, description="거래처 칸: 이 거래의 품목명 '배추,무' — 같은 답은 실패")
    ] = None,
    x_idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """
    Answer one 되묻기 question by voice. 400 VOICE_ANSWER_DISABLED for a field that is off ·
    400 AUDIO_INVALID · 401 login · 413 PAYLOAD_TOO_LARGE · 422 bad form (incl. choices) ·
    429 voice cap · 503 STT failure (→ buttons). Nothing is stored.
    """
    if field not in get_settings().voice_answer_enabled:
        return JSONResponse(status_code=400, content=_error(
            "VOICE_ANSWER_DISABLED", "이 항목은 버튼으로 골라 주세요."))
    try:
        audio = file.file.read(get_settings().stt_max_bytes + 1)
    finally:
        file.file.close()
    mime = (file.content_type or "").split(";")[0].strip().lower()
    try:
        choice_list = [int(c) for c in choices.split(",")] if choices else None
    except ValueError:  # same 422 shape as every other bad form field (FastAPI validation)
        raise RequestValidationError([{"type": "value_error", "loc": ("body", "choices"),
                                       "msg": "choices must be comma-separated integers", "input": choices}])
    item_list = [i.strip() for i in item_names.split(",") if i.strip()] if item_names else None
    payload = {"audio_sha256": hashlib.sha256(audio).hexdigest(), "mime": mime, "field": field,
               "choices": choice_list, "item_names": item_list}
    return run_idempotent(
        db, user_id, x_idempotency_key, "voice-answer", payload,
        lambda: _answer(field, audio, file.filename or "answer", mime, choice_list, item_list),
    )
