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
        return RecordReplayLLM(registry, settings.replay_dir, ledger=ledger)
    if not settings.llm_record:
        return AnthropicLLM.from_settings(settings, registry, ledger=ledger)
    live = AnthropicLLM.from_settings(settings, registry)  # the recording wrapper does the ledger
    return RecordReplayLLM(registry, settings.replay_dir, inner=live, ledger=ledger)
