"""RTZR (리턴제로) batch STT for the vendor comparison only — not a production adapter (PM 10/19).

The batch API is asynchronous (submit → id → poll), so it is measured for accuracy only; its wait
is queue time, not a response time we could serve. Formats it reads: mp4·m4a·mp3·amr·flac·wav — webm
and anything else goes through the same `to_wav` the CLOVA path uses. Every vendor failure (429 usage
or concurrency, 500, poll deadline, conversion) is raised as STTError, the same 503 class as stt.py.

Env: RTZR_CLIENT_ID, RTZR_CLIENT_SECRET (backend/.env, never committed).
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx
from app.services.audio_convert import AudioConvertError, to_wav
from app.services.stt import STTError

API_BASE = "https://openapi.vito.ai/v1"
RTZR_MIME_TYPES = {"audio/mp4", "audio/x-m4a", "audio/mpeg", "audio/amr", "audio/flac", "audio/wav", "audio/x-wav"}
POLL_INTERVAL_SECONDS = 5.0  # API guide: shorter polling can be answered with 429
TOKEN_REFRESH_MARGIN_SECONDS = 30 * 60  # tokens live 6 h; refresh well before expire_at
REQUEST_TIMEOUT_SECONDS = 30.0
KEYWORD_MIN_CHARS = 2


class RtzrStatusError(STTError):
    """A non-200 answer; the HTTP status stays readable for the report (429 usage · 500 · …)."""

    def __init__(self, status_code: int):
        super().__init__(f"RTZR answered {status_code}")
        self.status_code = status_code


def rtzr_keywords(counterparty_hints: Sequence[str]) -> list[str]:
    """Owner's names as `config.keywords` (string array): whitespace collapsed, 2+ chars, duplicates dropped."""
    kept: list[str] = []
    for raw in counterparty_hints:
        name = " ".join(raw.split())
        if len(name) >= KEYWORD_MIN_CHARS and name not in kept:
            kept.append(name)
    return kept


class RtzrClient:
    """Token-holding client; `submit` and `poll` are split so a whole pass can be in flight at once."""

    def __init__(self, client_id: str, client_secret: str, *, http: httpx.Client | None = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        if not client_id or not client_secret:
            raise STTError("RTZR_CLIENT_ID / RTZR_CLIENT_SECRET is not configured")
        self._id, self._secret = client_id, client_secret
        self._http = http or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        self._clock, self._sleep = clock, sleep
        self._token: str | None = None
        self._expire_at = 0.0

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """One HTTP call; transport errors, non-200 and non-JSON bodies all become STTError (status kept)."""
        try:
            response = self._http.request(method, API_BASE + path, **kwargs)
        except httpx.HTTPError as exc:
            raise STTError(f"RTZR request failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise RtzrStatusError(response.status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise STTError("RTZR response is not JSON") from exc

    def _auth_header(self) -> dict[str, str]:
        if self._token is None or self._clock() > self._expire_at - TOKEN_REFRESH_MARGIN_SECONDS:
            body = self._call("POST", "/authenticate", data={"client_id": self._id, "client_secret": self._secret})
            self._token, self._expire_at = body.get("access_token"), float(body.get("expire_at") or 0)
            if not self._token:
                raise STTError("RTZR authentication returned no token")
        return {"Authorization": f"Bearer {self._token}"}

    def submit(self, audio: bytes, filename: str, mime: str, *, counterparty_hints: Sequence[str] = (),
               timeout: float = REQUEST_TIMEOUT_SECONDS) -> str:
        """Upload one recording (converted to wav when RTZR cannot read it) and return the transcribe id."""
        if mime not in RTZR_MIME_TYPES:
            try:
                audio, filename = to_wav(audio, timeout=timeout), "audio.wav"
            except AudioConvertError as exc:
                raise STTError(f"conversion failed: {exc}") from exc
        config: dict[str, Any] = {"model_name": "sommers", "language": "ko", "use_itn": True,
                                  "use_disfluency_filter": True, "use_paragraph_splitter": False}
        keywords = rtzr_keywords(counterparty_hints)
        if keywords:
            config["keywords"] = keywords
        body = self._call("POST", "/transcribe", headers=self._auth_header(),
                          files={"file": (filename, audio)}, data={"config": json.dumps(config, ensure_ascii=False)})
        if not body.get("id"):
            raise STTError("RTZR returned no transcribe id")
        return str(body["id"])

    def poll(self, transcribe_id: str, *, deadline_seconds: float) -> str:
        """Wait for one job (every POLL_INTERVAL_SECONDS) and return the joined text; failure/deadline → STTError."""
        give_up = self._clock() + deadline_seconds
        while True:
            body = self._call("GET", f"/transcribe/{transcribe_id}", headers=self._auth_header())
            status = body.get("status")
            if status == "completed":
                utterances = (body.get("results") or {}).get("utterances") or []
                text = " ".join(str(u.get("msg", "")).strip() for u in utterances).strip()
                if not text:
                    raise STTError("RTZR returned an empty transcript")
                return text
            if status != "transcribing":
                code = (body.get("error") or {}).get("code")
                raise STTError(f"RTZR job {status}: {code}")
            if self._clock() >= give_up:
                raise STTError(f"RTZR job still transcribing after {deadline_seconds:g} s")
            self._sleep(POLL_INTERVAL_SECONDS)
