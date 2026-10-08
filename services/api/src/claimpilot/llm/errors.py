"""Typed LLM errors. SDK exceptions never leak past the client; callers branch on these.

Mapping follows the claude-api skill's error-codes reference: 429 / 5xx / 529 / network are
retryable (the SDK already retried them ``max_retries`` times); other 4xx are not.
"""

from __future__ import annotations

import anthropic

from claimpilot.llm.types import CallInfo


class LLMError(Exception):
    """Base for every error raised by an ``LLMClient``."""

    retryable: bool = False

    def __init__(self, message: str, *, call: CallInfo | None = None) -> None:
        super().__init__(message)
        self.call = call  # filled in once the call has been accounted for


class LLMRefusalError(LLMError):
    """``stop_reason == "refusal"``: the model (and any server-side fallback) declined."""

    def __init__(self, category: str | None) -> None:
        super().__init__(f"model refused the request (category={category})")
        self.category = category


class LLMTruncatedError(LLMError):
    """Output cut off (``max_tokens`` or context window) before the structured output closed."""


class LLMOutputError(LLMError):
    """The response finished but did not validate against the requested output model."""


class ReplayMissError(LLMError):
    """``LLM_MODE=replay`` and no recording exists for this exact request."""


class FakeMissError(LLMError):
    """``LLM_MODE=fake`` and no fake response is registered for this route / output model."""


class LLMAPIError(LLMError):
    """The API rejected or failed the request."""

    def __init__(
        self, message: str, *, status_code: int | None = None, request_id: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


class LLMRequestError(LLMAPIError):
    """400 / 404 / 413 / 422: the request itself is wrong (a shim bug or oversized input)."""


class LLMAuthError(LLMAPIError):
    """401 / 402 / 403: key, billing or permission problem. Needs a human."""


class LLMRateLimitError(LLMAPIError):
    retryable = True


class LLMUnavailableError(LLMAPIError):
    """5xx, 529 overloaded, timeouts and connection failures."""

    retryable = True


_STATUS_ERRORS: dict[int, type[LLMAPIError]] = {
    400: LLMRequestError,
    401: LLMAuthError,
    402: LLMAuthError,
    403: LLMAuthError,
    404: LLMRequestError,
    413: LLMRequestError,
    422: LLMRequestError,
    429: LLMRateLimitError,
}


def map_sdk_error(exc: anthropic.APIError) -> LLMAPIError:
    """Translate an Anthropic SDK exception into the matching ``LLMAPIError``."""
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        cls = _STATUS_ERRORS.get(status, LLMUnavailableError if status >= 500 else LLMAPIError)
        return cls(
            f"{status} {exc.type or 'error'}: {exc.message}",
            status_code=status,
            request_id=exc.request_id,
        )
    if isinstance(exc, anthropic.APIConnectionError):  # includes APITimeoutError
        return LLMUnavailableError(f"connection error: {exc.message}")
    return LLMAPIError(str(exc))
