"""LLM layer: model registry, capability shim, LLM clients (live / replay / fake), cost ledger."""

from claimpilot.llm.base import BaseLLM
from claimpilot.llm.client import LLMClient, get_llm
from claimpilot.llm.errors import (
    LLMAPIError,
    LLMError,
    LLMOutputError,
    LLMRefusalError,
    LLMTruncatedError,
    ReplayMissError,
)
from claimpilot.llm.fake import FakeLLM
from claimpilot.llm.ledger import CostLedger, SpendSummary
from claimpilot.llm.params import LLMRequest, build_request
from claimpilot.llm.registry import (
    ModelRegistry,
    ModelSpec,
    ResolvedRoute,
    RouteSpec,
    load_registry,
)
from claimpilot.llm.types import CallInfo, LLMResult, TokenUsage

__all__ = [
    "BaseLLM",
    "CallInfo",
    "CostLedger",
    "FakeLLM",
    "LLMAPIError",
    "LLMClient",
    "LLMError",
    "LLMOutputError",
    "LLMRefusalError",
    "LLMRequest",
    "LLMResult",
    "LLMTruncatedError",
    "ModelRegistry",
    "ModelSpec",
    "ReplayMissError",
    "ResolvedRoute",
    "RouteSpec",
    "SpendSummary",
    "TokenUsage",
    "build_request",
    "get_llm",
    "load_registry",
]
