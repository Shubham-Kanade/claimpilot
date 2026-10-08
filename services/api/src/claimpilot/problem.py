"""RFC 9457-style problem responses: one error shape for the whole API."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from claimpilot.mcp.client import McpError, McpToolError, McpUnavailableError


class Problem(BaseModel):
    """The body of every error response (``application/problem+json``)."""

    type: str  # stable machine code, e.g. "claim_not_ready"
    title: str  # human-readable summary
    status: int
    detail: dict[str, Any] | None = None


class ProblemError(HTTPException):
    def __init__(
        self, status_code: int, code: str, title: str, detail: dict[str, Any] | None = None
    ):
        super().__init__(status_code=status_code, detail=title)
        self.code, self.title, self.extra = code, title, detail


def problem(
    status_code: int, code: str, title: str, detail: dict[str, Any] | None = None
) -> ProblemError:
    return ProblemError(status_code, code, title, detail)


def install(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(_: Request, exc: ProblemError) -> JSONResponse:
        body = Problem(type=exc.code, title=exc.title, status=exc.status_code, detail=exc.extra)
        return JSONResponse(
            body.model_dump(exclude_none=True),
            status_code=exc.status_code,
            media_type="application/problem+json",
        )

    @app.exception_handler(McpError)
    async def _mcp(_: Request, exc: McpError) -> JSONResponse:
        """A mocked enterprise system failed: say which one and whether to try again."""
        system = {"finance": "finance system", "corp": "corporate systems"}.get(
            exc.server or "", "a connected system"
        )
        if isinstance(exc, McpUnavailableError):
            status, code, title = 503, "system_unavailable", f"The {system} is not reachable"
        elif isinstance(exc, McpToolError):
            status, code = 502, "system_rejected"
            title = f"The {system} rejected the request: {str(exc)[:200]}"
        else:
            status, code, title = 502, "system_error", f"The {system} answered unexpectedly"
        body = Problem(type=code, title=title, status=status, detail={"retryable": exc.retryable})
        return JSONResponse(
            body.model_dump(exclude_none=True),
            status_code=status,
            media_type="application/problem+json",
        )
