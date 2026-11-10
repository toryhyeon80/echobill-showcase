"""Request body size limit — reject before FastAPI spools the upload to disk (PM #13).

Content-Length over the limit → 413 at once. Without Content-Length (chunked), bytes are
counted as they arrive and the request is cut off at the limit. Sits inside CORS so the
browser sees the 413 instead of a network error.
"""

import hmac
import json
import logging
import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


def _error_body(error_code: str, message: str) -> bytes:
    """DL-03 error body as bytes, for responses sent below FastAPI."""
    body = {"error_code": error_code, "message": message, "problem_fields": [], "partial_candidate": None}
    return json.dumps(body, ensure_ascii=False).encode()


_BODY = _error_body("PAYLOAD_TOO_LARGE", "요청이 너무 큽니다. 녹음을 짧게 하거나 텍스트로 입력해 주세요.")
_SERVER_ERROR_BODY = _error_body("INTERNAL_SERVER_ERROR", "서버에 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.")


class BodySizeLimitMiddleware:
    """Per-path limits (exact path match), `default_limit` for everything else."""

    def __init__(self, app: ASGIApp, default_limit: int, path_limits: dict[str, int]) -> None:
        self.app = app
        self.default_limit = default_limit
        self.path_limits = path_limits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.path_limits.get(scope["path"], self.default_limit)
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    await _send_413(send)
                    return
            except ValueError:
                pass  # malformed header: fall through to byte counting

        # Over the limit we hand the app a client disconnect, so the route never runs on a partial
        # body (no write can happen), and we answer 413 ourselves. FastAPI would turn an exception
        # raised here into 400, so nothing is raised into the app.
        received = 0
        too_large = False
        replaced = False

        async def counting_receive() -> Message:
            nonlocal received, too_large
            if too_large:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    too_large = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal replaced
            if not too_large:
                await send(message)
            elif message["type"] == "http.response.start" and not replaced:
                replaced = True
                await _send_413(send)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            if not too_large:
                raise
        if too_large and not replaced:
            await _send_413(send)


async def _send_413(send: Send) -> None:
    await _send_json(send, 413, _BODY)


async def _send_json(send: Send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


class ServerErrorResponseMiddleware:
    """
    Answer an unhandled exception with a DL-03 500 from inside CORS, then re-raise it.

    Starlette's own 500 comes from the layer outside every middleware, so it has no CORS headers: the
    browser hides that response and the screen says "check your connection" (CLAUDE.md §4: 500 is a
    system failure, never a network error). Re-raising keeps the server's traceback log as before.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            if not started:  # a started response cannot change status; the connection just ends
                await _send_json(send, 500, _SERVER_ERROR_BODY)
            raise


class ErrorLogMiddleware:
    """
    One `api_error` log line per 4xx/5xx response, for scripts/ops_metrics.py (PM #18).

    The route is logged as a template — path parameter values swapped back for their names —
    so no entry ID or other identifier reaches the log. Only the DL-03 `error_code` is read
    from the body. Sits outside the size limit so 413s are counted; a raised exception is
    logged as 500 before it propagates.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status = 0
        chunks: list[bytes] = []

        async def capturing_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body" and status >= 400:
                chunks.append(message.get("body", b""))
                if not message.get("more_body"):
                    _log_error(scope, status, b"".join(chunks))
            await send(message)

        try:
            await self.app(scope, receive, capturing_send)
        except Exception:
            if status == 0:
                _log_error(scope, 500, b"")
            raise


def _route_template(scope: Scope) -> str:
    """/api/v1/tax/<uuid> → /api/v1/tax/{entry_id}; unmatched routes → "-"."""
    if scope.get("route") is None:
        return "-"
    path = scope.get("path", "")
    for name, value in (scope.get("path_params") or {}).items():
        path = path.replace(str(value), "{" + name + "}")
    return path


def _log_error(scope: Scope, status: int, body: bytes) -> None:
    code = "-"
    try:
        parsed = json.loads(body) if body else None
        if isinstance(parsed, dict) and isinstance(parsed.get("error_code"), str):
            code = parsed["error_code"]
    except ValueError:
        pass
    logger.warning(
        "api_error method=%s route=%s status=%d error_code=%s",
        scope.get("method", "-"), _route_template(scope), status, code,
    )


class OriginSecretMiddleware:
    """
    Reject requests that skipped Cloudflare (PM #11): Cloudflare adds a secret header to every
    request it forwards; anything hitting the Railway address directly lacks it.

    Off while `secret` is empty, so deploying this changes nothing until both sides are set —
    Cloudflare first, then ORIGIN_SECRET on Railway (docs/deploy.md). /health stays open for
    Railway's own healthcheck. Outermost layer: a rejected request touches nothing else.
    """

    def __init__(self, app: ASGIApp, secret: str, header: str, exempt_paths: tuple[str, ...] = ("/health",)) -> None:
        self.app = app
        self.secret = secret.encode()
        self.header = header.lower().encode()
        self.exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.secret or scope["type"] != "http" or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        sent = dict(scope.get("headers") or []).get(self.header, b"")
        if hmac.compare_digest(sent, self.secret):
            await self.app(scope, receive, send)
            return
        body = b'{"error_code":"ORIGIN_FORBIDDEN","message":"Forbidden","problem_fields":[],"partial_candidate":null}'
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})


def server_timing_stages(request: Any) -> list[tuple[str, int]]:
    """The request's stage list that ServerTimingMiddleware adds to the header (a fresh list outside it)."""
    stages = getattr(request.state, "server_timing", None)
    return stages if isinstance(stages, list) else []


class ServerTimingMiddleware:
    """
    `Server-Timing: app;dur=<ms>` on every response — time from the request reaching this app to the
    response start (PM 10/15 속도 확인). Splits app time from the Cloudflare·Railway network path in
    curl or the browser's Network tab. Duration only: no path, user or body.

    A route may add its own stages (e.g. `stt;dur=…, parse;dur=…` on /parse-audio, PM 10/20) to the
    list from `server_timing_stages(request)`; they follow `app` in the same header.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        stages: list[tuple[str, int]] = []
        scope.setdefault("state", {})["server_timing"] = stages

        async def timed_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                dur = (time.perf_counter() - started) * 1000
                value = ", ".join([f"app;dur={dur:.1f}", *(f"{name};dur={ms}" for name, ms in stages)])
                headers = [*message.get("headers", []), (b"server-timing", value.encode())]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, timed_send)
