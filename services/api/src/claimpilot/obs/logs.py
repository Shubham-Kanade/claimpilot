"""Logging setup: one JSON object per line, with the bound ids on every line.

Both ``structlog`` loggers and plain ``logging`` loggers (uvicorn, our own modules) go through the
same formatter, so a log line from anywhere carries the request, trace, sandbox, batch and
document ids that were bound when it was written. Configure it from the entry points that serve
traffic (the API lifespan, the worker), never at import time: tests import the app, and replacing
the root handlers there would break pytest's log capture.
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

import structlog
import structlog.contextvars
import structlog.dev
import structlog.processors
import structlog.stdlib

LogFormat = Literal["json", "console"]
QUIET_LOGGERS = ("uvicorn.access", "httpx", "httpx2", "httpcore", "mcp")
_HANDLER_FLAG = "_claimpilot_handler"  # marks the one handler we install, so calls are idempotent


def configure_logging(fmt: LogFormat = "json", level: str = "INFO") -> None:
    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    renderer = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,  # lines from plain ``logging`` get the same fields
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    setattr(handler, _HANDLER_FLAG, True)

    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not getattr(h, _HANDLER_FLAG, False)]
    root.addHandler(handler)
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        # uvicorn installs its own handlers; route its records through ours instead
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    # The middleware logs every request itself (with ids and duration), so uvicorn's access line is
    # a duplicate, and the HTTP clients log one line per MCP call: keep warnings, drop the chatter.
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
