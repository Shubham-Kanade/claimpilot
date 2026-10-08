"""Value types shared by the shim, the LLM clients and the cost ledger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

ThinkingMode = Literal["auto", "off"]
LLMMode = Literal["live", "replay", "fake"]


class TokenUsage(BaseModel):
    """Token counts as billed. ``input_tokens`` excludes cache reads and cache writes."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class Completion(BaseModel):
    """The raw outcome of one Messages API call, before validation.

    This is what backends return and what record/replay stores on disk, so it holds only
    JSON-friendly data (never SDK objects, never request headers or keys).
    """

    model_config = ConfigDict(frozen=True)

    text: str | None
    stop_reason: str | None
    stop_category: str | None = None  # stop_details.category on refusals
    model_id: str  # the model that served the response (differs after a refusal fallback)
    usage: TokenUsage = TokenUsage()
    latency_ms: int = 0
    replayed: bool = False  # set when served from a recording rather than the API


@dataclass(frozen=True, slots=True, kw_only=True)
class CallInfo:
    """Everything the cost ledger stores about one call (successful or not)."""

    route: str
    model_key: str
    model_id: str
    effort: str | None
    mode: LLMMode
    usage: TokenUsage
    cost_usd: float
    latency_ms: int
    stop_reason: str | None
    request_hash: str
    batch: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LLMResult[T](CallInfo):
    """A validated structured output plus the accounting for the call that produced it."""

    parsed: T
