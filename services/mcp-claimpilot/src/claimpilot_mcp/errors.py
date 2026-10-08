"""Failures a model (or the person behind it) can act on.

Each message is written to be read: what went wrong and what to try. None of them carries a
request header, the persona, a stack trace or a response body; ``server.py`` turns them into
``ToolError``, the only exception the SDK passes on to the model verbatim.
"""

from __future__ import annotations


class ClaimPilotError(Exception):
    """Base class: ``str(error)`` is safe to show to a person or a model."""


class ApiProblem(ClaimPilotError):
    """The API answered with an error (an RFC 9457 problem, or a plain HTTP failure)."""

    def __init__(self, message: str, *, status: int, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class ApiUnavailable(ClaimPilotError):
    """The API could not be reached, or did not answer in time."""


class ApiProtocolError(ClaimPilotError):
    """The API answered, but not in the shape this connector expects (the two are out of sync)."""


class UploadError(ClaimPilotError):
    """Some of the files cannot be uploaded; ``problems`` holds one line per file."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("Nothing was uploaded. " + " ".join(problems))
        self.problems = problems
