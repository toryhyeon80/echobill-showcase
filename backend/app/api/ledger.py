"""Ledger routes — parse stores candidates; feedback is the only path to confirmed."""

import hashlib
import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.dependencies import CurrentUserId
from app.middleware import server_timing_stages
from app.models import LedgerEntry
from app.schemas.errors import ErrorResponse
from app.schemas.ledger import (
    CandidateFields,
    FeedbackRequest,
    LedgerEntryResponse,
    ParseCandidateResponse,
    ParseRequest,
)
from app.services.feedback import FeedbackRejected, apply_feedback
from app.services.idempotency import error_json, run_idempotent
from app.services.ledger_store import create_candidate, get_owned_entry, repair_record, repair_trace
from app.services.parse_mock import ParserResult
from app.services.parse_pipeline import structure_utterance
from app.services.stt import STTError, transcribe
from app.services.stt_hints import recent_counterparty_names
from app.services.validation import ValidationFailure, check_input_length, validate_candidate

logger = logging.getLogger(__name__)

router = APIRouter()

# 1주차 실기기 녹음: Android·iPhone 모두 audio/webm(opus). 구형 iOS는 audio/mp4일 수 있다
AUDIO_MIME_TYPES = {"audio/webm", "audio/mp4", "audio/m4a", "audio/x-m4a", "audio/mpeg", "audio/wav", "audio/x-wav", "audio/ogg"}


def _failure_body(failure: ValidationFailure) -> dict[str, Any]:
    """DL-03 400 body. Nothing is stored on this path."""
    return ErrorResponse(
        error_code=failure.error_code,
        message=failure.message,
        problem_fields=failure.problem_fields,
        partial_candidate=failure.partial_candidate,
        redirect=failure.redirect,
    ).model_dump(mode="json")


def _entry_body(entry: LedgerEntry) -> dict[str, Any]:
    """Public view of one stored entry (never the integer PK, DL-23)."""
    return LedgerEntryResponse(
        entry_id=entry.entry_id,
        status=entry.status,
        counterparty=entry.counterparty,
        items=entry.items or [],
        total_amount=entry.total_amount,
        credit=entry.credit,
        utterance_text=entry.utterance_text,
        created_at=entry.created_at,
    ).model_dump(mode="json")


# PM #23: which problem fields each post-process rule can be blamed for
_RULE_FIELDS = {
    "credit_cleared_no_word": ("credit",),
    "credit_cleared_negated": ("credit",),
    "credit_cleared_contradiction": ("credit",),  # pp-1.4 (#82)
    "credit_cleared_partial_payment": ("credit",),
    "credit_cleared_not_received": ("credit",),
    "credit_from_text": ("credit",),
    "unit_price_to_amount": (".unit_price", ".amount"),
    "unit_price_cleared": (".unit_price", ".amount"),
    "pack_spec_qty_cleared": (".qty", ".amount", "total_amount"),  # PM 10/13
    "total_restored_stated": ("total_amount",),  # PM 10/12
    "item_amount_invented": ("total_amount", ".amount"),  # PM 10/13
    "total_restored_spoken": ("total_amount", ".amount"),  # PM 10/16
    "counterparty_is_item_cleared": ("counterparty",),  # PM 10/29
    # 회의 10/23: the two models disagreed, so the field was emptied and asked again (cascade.py)
    "cascade_counterparty": ("counterparty",),
    "cascade_item_name": (".name",),
    "cascade_item_qty": (".qty",),
    "cascade_total_amount": ("total_amount", ".amount"),
    "cascade_credit": ("credit",),
}


def _rules_behind(applied: list[str], problem_fields: list[str]) -> list[str]:
    """Only the rules that touched a field the 400 is about (e.g. unit_price_cleared is not behind a
    MULTIPLE_COUNTERPARTIES 400)."""
    return sorted(
        rule for rule in set(applied)
        if any(f == mark or f.endswith(mark) for f in problem_fields for mark in _RULE_FIELDS.get(rule, ()))
    )


def _parse(body: ParseRequest, user_id: str, db: Session) -> tuple:
    """Utterance (or manual candidate) → validate → store candidate.

    Returns (status, body), or (400, body, trace) when post-process rules caused the 400 — the
    trace is kept on the idempotency record for a later repair_of link (PM #23)."""
    failure = check_input_length(body.utterance_text)
    if failure:
        return 400, _failure_body(failure)

    linked_repair = False
    repair = None
    if body.candidate is not None:
        # PM 10/15: one failed request → one repaired candidate. A repair_of already answered returns that
        # candidate while it is undecided, 409 once it is confirmed; rejected or gone → a new one is allowed
        repair = repair_record(db, user_id, body.repair_of, for_update=True)
        previous = get_owned_entry(db, user_id, repair.repair_entry_id) if repair and repair.repair_entry_id else None
        if previous is not None and previous.status == "candidate":
            return 200, _candidate_body(previous)
        if previous is not None and previous.status == "confirmed":
            return 409, ErrorResponse(error_code="REPAIR_ALREADY_CONFIRMED",
                                      message="이미 장부에 저장된 내용이에요. 장부에서 확인해 주세요.").model_dump(mode="json")
        # DL-03 3-3: user-corrected values skip the parser, validation only
        result = ParserResult(
            fields=body.candidate.model_dump(mode="json"),
            utterance_text=body.utterance_text,
            parser_type="manual",
        )
        trace = repair_trace(repair)
        if trace:
            # the rules that made the original parse ask again → kept with what the user chose
            result.postprocess_version, result.postprocess_applied = trace["version"], list(trace["rules"])
            linked_repair = True
    else:
        result = structure_utterance(body.utterance_text)
    failure = validate_candidate(
        result.fields,
        counterparty_count=result.counterparty_count,
        item_count=result.item_count,
        transaction_type=result.transaction_type,
    )
    if failure:
        rules = _rules_behind(result.postprocess_applied, failure.problem_fields)
        if rules and result.parser_type != "manual":
            # PM #23: a rule that empties a required value (e.g. credit) ends in a 400, which stores
            # nothing — leave one countable line (rule names and code only, no utterance or values)
            # and keep the trace on the idempotency record for a later repair_of link
            logger.warning(
                "postprocess_400 version=%s rules=%s error_code=%s",
                result.postprocess_version, ",".join(rules), failure.error_code,
            )
            return 400, _failure_body(failure), {"version": result.postprocess_version, "rules": rules}
        return 400, _failure_body(failure)

    fields = CandidateFields.model_validate(result.fields)
    # one 400 → at most one linked repair; the link is consumed in the same commit as the entry
    entry = create_candidate(db, user_id, fields, result,
                             consume_repair_key=body.repair_of if linked_repair else None, repair_link=repair)
    return 200, _candidate_body(entry)


def _candidate_body(entry: LedgerEntry) -> dict[str, Any]:
    """200 body of /parse for a stored candidate (a new one, or the one an earlier repair made)."""
    fields = CandidateFields.model_validate(
        {"counterparty": entry.counterparty, "items": entry.items or [], "total_amount": entry.total_amount,
         "credit": entry.credit})
    return ParseCandidateResponse(
        **fields.model_dump(),
        entry_id=entry.entry_id,
        utterance_text=entry.utterance_text,
        parser_type=entry.parser_type,
        is_fallback_used=entry.is_fallback_used,
        processing_time_ms=entry.processing_time_ms or 0,
    ).model_dump(mode="json")


@router.post(
    "/parse",
    response_model=ParseCandidateResponse,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def parse_utterance(
    body: ParseRequest,
    user_id: CurrentUserId,
    db: Annotated[Session, Depends(get_db)],
    x_idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """
    Structure utterance → validate → store as candidate (LLM → Rule fallback, or manual candidate).

    Invariant: status is always `candidate`. Never writes confirmed.
    400 (DL-03) stores nothing; 200 stores one row owned by the JWT user.
    Same X-Idempotency-Key replays the first response (DL-22).
    """
    return run_idempotent(
        db, user_id, x_idempotency_key, "parse",
        body.model_dump(mode="json", exclude={"repair_of"} if body.repair_of is None else None),
        lambda: _parse(body, user_id, db),
    )


def _ms_since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _log_voice_timing(timings: list[tuple[str, int]], status: int, audio_bytes: int, mime: str) -> None:
    """
    One countable line per voice request (PM 10/20 "말 끝 → 확인 화면" 단계별 시간): stage ms, status,
    size and format only — no audio, transcript or user. WARNING like postprocess_400, because the
    app has no log handler and only WARNING+ reaches the Railway log.
    """
    stages = " ".join(f"{name}_ms={ms}" for name, ms in timings)
    logger.warning("voice_timing status=%s %s audio_bytes=%d mime=%s", status, stages, audio_bytes, mime)


def _voice_failure_body() -> dict[str, Any]:
    """503 body of /parse-audio when the recording gave no usable text (DL-33)."""
    return ErrorResponse(
        error_code="EXTERNAL_API_TIMEOUT", message="음성을 글자로 바꾸지 못했습니다. 텍스트로 입력해 주세요."
    ).model_dump(mode="json")


def _nothing_extracted(body: dict[str, Any]) -> bool:
    """True for a missing-field 400 with no counterparty, item value, amount or credit choice at all.

    Other 400s (not a sale, too many items …) have no partial candidate but did understand the sentence. `credit=False`
    is a value; an amount of 0 is not."""
    partial = body.get("partial_candidate")
    if body.get("error_code") != "MISSING_REQUIRED_FIELD" or partial is None:
        return False
    return (
        not (partial["counterparty"] or "").strip()
        and not any(value for item in partial["items"] for value in item.values())
        and not partial["total_amount"]
        and partial["credit"] is None
    )


def _parse_audio(audio: bytes, filename: str, mime: str, user_id: str, db: Session,
                 timings: list[tuple[str, int]] | None = None) -> tuple:
    """Recorded audio → STT text (+ the owner's counterparty names when enabled) → the /parse pipeline.

    `timings` collects (stage, ms) for STT and parsing — logged here and sent as Server-Timing stages."""
    timings = [] if timings is None else timings
    settings = get_settings()
    if mime not in AUDIO_MIME_TYPES or not audio or len(audio) > settings.stt_max_bytes:
        return 400, ErrorResponse(
            error_code="AUDIO_INVALID", message="녹음 파일을 처리할 수 없습니다. 다시 녹음하거나 텍스트로 입력해 주세요."
        ).model_dump(mode="json")
    # Off (default) → the exact call made before hints existed
    hint_kwargs = (
        {"counterparty_hints": recent_counterparty_names(db, user_id, settings.stt_counterparty_hint_limit)}
        if settings.stt_counterparty_hints
        else {}
    )
    started = time.perf_counter()  # STT only — the name lookup above is a small DB read
    try:
        text = transcribe(audio, filename, mime, **hint_kwargs)
    except STTError:
        timings.append(("stt", _ms_since(started)))
        _log_voice_timing(timings, 503, len(audio), mime)
        return 503, _voice_failure_body()
    timings.append(("stt", _ms_since(started)))
    started = time.perf_counter()
    status, body, *trace = _parse(ParseRequest(utterance_text=text), user_id, db)
    timings.append(("parse", _ms_since(started)))
    if status == 400 and _nothing_extracted(body):
        # PM 11/2: background talk gets transcribed; an all-empty form tells the owner nothing → "잘 못 들었어요" like STT
        # failure. Own line so these are not counted as vendor failures (no transcript, CLAUDE.md §3②)
        logger.warning("voice_nothing_extracted audio_bytes=%d", len(audio))
        status, body, trace = 503, _voice_failure_body(), []
    _log_voice_timing(timings, status, len(audio), mime)
    if status == 400:
        # DL-29: give the transcript back so the user sees what was heard and can fix it as text.
        # Never logged — it only lives in this response (and its 24h idempotency record).
        body["utterance_text"] = text
    return (status, body, *trace)


@router.post(
    "/parse-audio",
    response_model=ParseCandidateResponse,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def parse_audio(
    request: Request,
    file: UploadFile,
    user_id: CurrentUserId,
    db: Annotated[Session, Depends(get_db)],
    x_idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """
    Voice input: multipart `file` (webm/mp4…) → Whisper → LLM/Rule → validate → candidate.

    The original audio is read into memory and the upload is closed (temp file removed) before
    STT runs; it is never stored (CLAUDE.md §4-10). STT failure, or a transcript with nothing extracted → 503 EXTERNAL_API_TIMEOUT
    (FE switches to text input), released for retry. Otherwise identical to /parse.
    STT and parse times go out as Server-Timing stages next to `app` (a replayed key has none).
    """
    try:
        audio = file.file.read(get_settings().stt_max_bytes + 1)
    finally:
        file.file.close()
    mime = (file.content_type or "").split(";")[0].strip().lower()  # "audio/webm; codecs=opus"
    payload = {"audio_sha256": hashlib.sha256(audio).hexdigest(), "mime": mime}
    return run_idempotent(
        db, user_id, x_idempotency_key, "parse-audio", payload,
        lambda: _parse_audio(audio, file.filename or "audio", mime, user_id, db, server_timing_stages(request)),
    )


def _feedback(
    entry_id: str, body: FeedbackRequest, user_id: str, db: Session
) -> tuple[int, dict[str, Any]]:
    """Apply confirm/correct/reject and map the outcome to (status, body)."""
    outcome = apply_feedback(db, user_id, entry_id, body)
    if isinstance(outcome, ValidationFailure):
        return 400, _failure_body(outcome)
    if isinstance(outcome, FeedbackRejected):
        return outcome.status_code, ErrorResponse(
            error_code=outcome.error_code, message=outcome.message
        ).model_dump(mode="json")
    return 200, _entry_body(outcome)


@router.post(
    "/{entry_id}/feedback",
    response_model=LedgerEntryResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def submit_feedback(
    entry_id: str,
    body: FeedbackRequest,
    user_id: CurrentUserId,
    db: Annotated[Session, Depends(get_db)],
    x_idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """
    confirm / correct / reject one candidate — the only way to reach `confirmed`.

    404 = missing, another user's or soft-deleted (DL-21) · 409 CANDIDATE_EXPIRED after 7 days
    (reject still allowed) · 409 ENTRY_NOT_CANDIDATE once decided · 400 = corrected values invalid.
    """
    return run_idempotent(
        db, user_id, x_idempotency_key, f"feedback:{entry_id}", body.model_dump(mode="json"),
        lambda: _feedback(entry_id, body, user_id, db),
    )


@router.get(
    "/{entry_id}",
    response_model=LedgerEntryResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_entry(
    entry_id: str,
    user_id: CurrentUserId,
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    """One entry of the caller. Missing / another user's / soft-deleted → identical 404 (DL-21)."""
    entry = get_owned_entry(db, user_id, entry_id)
    if entry is None:
        return error_json(404, "NOT_FOUND", "찾을 수 없습니다.")
    return JSONResponse(status_code=200, content=_entry_body(entry))
