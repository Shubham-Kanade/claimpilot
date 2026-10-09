"""A request id for every HTTP request, bound into the log context and echoed to the caller.

Plain ASGI on purpose (not ``BaseHTTPMiddleware``): it wraps only ``send``, so streaming responses
(the live progress stream) pass through untouched and never get buffered. The id is also the trace
id: a batch remembers the id of the upload that made it, so the request log line, the pipeline's
log lines and the cost ledger rows of that upload all share one id.
"""

from __future__ import annotations

import re
import time
import uuid

import structlog
import structlog.contextvars
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
VALID_REQUEST_ID = re.compile(
    r"[A-Za-z0-9._-]{1,64}"
)  # nothing a caller sends can forge a log line
QUIET_PATHS = frozenset({"/healthz", "/readyz"})  # the container healthcheck polls these

log = structlog.get_logger("claimpilot.http")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        sent = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = sent if sent and VALID_REQUEST_ID.fullmatch(sent) else uuid.uuid4().hex
        status_code = 500  # what a crash before the response starts will look like to the caller

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        started = time.perf_counter()
        with structlog.contextvars.bound_contextvars(request_id=request_id, trace_id=request_id):
            try:
                await self.app(scope, receive, send_with_id)
            finally:
                emit = log.debug if scope["path"] in QUIET_PATHS else log.info
                emit(
                    "request",
                    method=scope["method"],
                    path=scope["path"],
                    status=status_code,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )
