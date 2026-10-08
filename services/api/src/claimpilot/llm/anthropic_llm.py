"""Live backend: the official ``anthropic`` SDK (AsyncAnthropic).

Structured outputs: the shim already puts the Pydantic schema in ``output_config.format``
(``transform_schema``, exactly what ``messages.parse`` would send). We call ``messages.create``
and validate afterwards instead of ``messages.parse`` because ``parse`` validates inside the
response hook: a ``max_tokens`` or ``refusal`` response raises a pydantic error before
``stop_reason`` and ``usage`` can be read, and rules/llm.md requires checking ``stop_reason``
first (and the ledger needs the usage of failed calls too).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import anthropic
from anthropic.types import Message
from anthropic.types.beta import BetaMessage

from claimpilot.config import Settings
from claimpilot.llm.base import BaseLLM, Ledger
from claimpilot.llm.errors import LLMAuthError, map_sdk_error
from claimpilot.llm.params import LLMRequest
from claimpilot.llm.registry import ModelRegistry
from claimpilot.llm.types import Completion, TokenUsage


class AnthropicLLM(BaseLLM):
    def __init__(
        self,
        registry: ModelRegistry,
        client: anthropic.AsyncAnthropic,
        *,
        ledger: Ledger | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(registry, mode="live", ledger=ledger, env=env)
        self._client = client

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        registry: ModelRegistry,
        *,
        ledger: Ledger | None = None,
        env: Mapping[str, str] | None = None,
    ) -> AnthropicLLM:
        if settings.llm_mode != "live":
            raise LLMAuthError(f"live client refused: LLM_MODE={settings.llm_mode} (spend guard)")
        if settings.anthropic_api_key is None:
            raise LLMAuthError("LLM_MODE=live needs ANTHROPIC_API_KEY")
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
        return cls(registry, client, ledger=ledger, env=env)

    async def complete(self, request: LLMRequest) -> Completion:
        started = time.perf_counter()
        try:
            message = await self._create(request)
        except anthropic.APIError as exc:
            raise map_sdk_error(exc) from exc
        return to_completion(message, latency_ms=int((time.perf_counter() - started) * 1000))

    async def count(self, request: LLMRequest) -> int:
        try:
            result = await self._client.messages.count_tokens(**request.count_tokens_body())
        except anthropic.APIError as exc:
            raise map_sdk_error(exc) from exc
        return result.input_tokens

    async def _create(self, request: LLMRequest) -> Message | BetaMessage:
        body: dict[str, Any] = request.body
        if request.betas:  # e.g. refusal fallbacks live on the beta surface
            return await self._client.beta.messages.create(**body, betas=list(request.betas))
        return await self._client.messages.create(**body)


def to_completion(message: Message | BetaMessage, *, latency_ms: int) -> Completion:
    text = "".join(block.text for block in message.content if block.type == "text")
    usage = message.usage
    return Completion(
        text=text or None,
        stop_reason=message.stop_reason,
        stop_category=message.stop_details.category if message.stop_details else None,
        model_id=message.model,
        usage=TokenUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
        ),
        latency_ms=latency_ms,
    )
