"""Public LLM entry point: the ``LLMClient`` protocol and the mode-selecting factory.

Callers depend on ``LLMClient`` and get an instance from ``get_llm(settings)``:

* ``fake``   -> ``FakeLLM`` (deterministic, in-process)
* ``replay`` -> ``RecordReplayLLM`` reading recordings from ``settings.replay_dir`` ($0)
* ``live``   -> ``AnthropicLLM``; with ``LLM_RECORD=1`` wrapped in a recording ``RecordReplayLLM``

Request parameters are built only by the capability shim (``claimpilot.llm.params``).
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from claimpilot.config import Settings
from claimpilot.llm.anthropic_llm import AnthropicLLM
from claimpilot.llm.base import BaseLLM, Content, Ledger
from claimpilot.llm.fake import FakeLLM
from claimpilot.llm.params import SystemPrompt
from claimpilot.llm.registry import ModelRegistry, ResolvedRoute, load_registry
from claimpilot.llm.replay import RecordReplayLLM
from claimpilot.llm.types import LLMMode, LLMResult, ThinkingMode


class LLMClient(Protocol):
    mode: LLMMode

    def resolve(self, route: str) -> ResolvedRoute: ...

    async def parse[T: BaseModel](
        self,
        route: str,
        *,
        system: SystemPrompt,
        content: Content,
        output_model: type[T],
        thinking: ThinkingMode = "auto",
    ) -> LLMResult[T]: ...

    async def count_tokens(
        self,
        route: str,
        *,
        system: SystemPrompt,
        content: Content,
        output_model: type[BaseModel] | None = None,
        thinking: ThinkingMode = "auto",
    ) -> int: ...


def get_llm(
    settings: Settings,
    *,
    registry: ModelRegistry | None = None,
    ledger: Ledger | None = None,
) -> BaseLLM:
    registry = registry or load_registry(settings.models_config)
    if settings.llm_mode == "fake":
        return FakeLLM(registry, ledger=ledger)
    if settings.llm_mode == "replay":
        return RecordReplayLLM(
            registry,
            settings.replay_dir,
            ledger=ledger,
            latency_scale=settings.replay_latency_scale,
        )
    spend = ledger if hasattr(ledger, "live_spend_since") else None  # the cap reads the ledger
    if not settings.llm_record:
        return AnthropicLLM.from_settings(settings, registry, ledger=ledger, spend=spend)  # type: ignore[arg-type]
    live = AnthropicLLM.from_settings(settings, registry, spend=spend)  # type: ignore[arg-type]
    return RecordReplayLLM(  # the wrapper records, and replays what it already has
        registry,
        settings.replay_dir,
        inner=live,
        ledger=ledger,
        latency_scale=settings.replay_latency_scale,
    )
