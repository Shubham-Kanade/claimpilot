"""Shared call flow for every LLM client: resolve route -> shim -> execute -> validate -> ledger.

Backends (live Anthropic, fake, record/replay) only implement ``complete`` and ``count``; the
stop-reason checks, cost math and ledger writes live here once.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

import structlog
from pydantic import BaseModel, ValidationError

from claimpilot.llm.errors import (
    LLMError,
    LLMOutputError,
    LLMRefusalError,
    LLMTruncatedError,
)
from claimpilot.llm.params import Block, LLMRequest, SystemPrompt, build_request
from claimpilot.llm.registry import ModelRegistry, ResolvedRoute
from claimpilot.llm.types import (
    CallInfo,
    Completion,
    LLMMode,
    LLMResult,
    ThinkingMode,
    TokenUsage,
)

log = structlog.get_logger(__name__)

TRUNCATION_STOP_REASONS = frozenset({"max_tokens", "model_context_window_exceeded"})

Content = str | Sequence[Block]


class Ledger(Protocol):
    async def record(self, call: CallInfo) -> None: ...


class BaseLLM(ABC):
    def __init__(
        self,
        registry: ModelRegistry,
        *,
        mode: LLMMode,
        ledger: Ledger | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.registry = registry
        self.mode: LLMMode = mode
        self.ledger = ledger
        self._env = env  # route overrides; None means the process environment

    # --- backend hooks -------------------------------------------------------------------
    @abstractmethod
    async def complete(self, request: LLMRequest) -> Completion:
        """Execute one request. Raise ``LLMError`` subclasses only."""

    @abstractmethod
    async def count(self, request: LLMRequest) -> int:
        """Input tokens the request would use (free endpoint; no ledger entry)."""

    # --- public API ----------------------------------------------------------------------
    def resolve(self, route: str) -> ResolvedRoute:
        """The model/effort this client would use for ``route`` (honours injected env overrides)."""
        return self.registry.resolve(route, env=self._env)

    def build(
        self,
        route: str,
        *,
        system: SystemPrompt,
        content: Content,
        output_model: type[BaseModel] | None = None,
        thinking: ThinkingMode = "auto",
    ) -> LLMRequest:
        resolved = self.registry.resolve(route, env=self._env)
        user_content = content if isinstance(content, str) else [dict(b) for b in content]
        return build_request(
            resolved,
            system=system,
            messages=[{"role": "user", "content": user_content}],
            output_model=output_model,
            thinking=thinking,
        )

    async def parse[T: BaseModel](
        self,
        route: str,
        *,
        system: SystemPrompt,
        content: Content,
        output_model: type[T],
        thinking: ThinkingMode = "auto",
    ) -> LLMResult[T]:
        request = self.build(
            route, system=system, content=content, output_model=output_model, thinking=thinking
        )
        started = time.perf_counter()
        try:
            completion = await self.complete(request)
        except LLMError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            raise await self._fail(self._call_fields(request, None, elapsed), exc) from exc

        fields = self._call_fields(request, completion, completion.latency_ms)
        try:
            parsed = validate_output(completion, output_model)
        except LLMError as exc:
            raise await self._fail(fields, exc) from exc

        result = LLMResult(parsed=parsed, **fields)
        await self._record(result)
        return result

    async def count_tokens(
        self,
        route: str,
        *,
        system: SystemPrompt,
        content: Content,
        output_model: type[BaseModel] | None = None,
        thinking: ThinkingMode = "auto",
    ) -> int:
        request = self.build(
            route, system=system, content=content, output_model=output_model, thinking=thinking
        )
        return await self.count(request)

    # --- accounting ----------------------------------------------------------------------
    def _call_fields(
        self, request: LLMRequest, completion: Completion | None, latency_ms: int
    ) -> dict[str, Any]:
        usage = completion.usage if completion else TokenUsage()
        model_key = self._billing_key(request, completion)
        return {
            "route": request.route,
            "model_key": model_key,
            "model_id": completion.model_id if completion else request.body["model"],
            "effort": request.effort,
            "mode": "replay" if completion and completion.replayed else self.mode,
            "usage": usage,
            "cost_usd": self.registry.estimate_cost(
                model_key,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            ),
            "latency_ms": latency_ms,
            "stop_reason": completion.stop_reason if completion else None,
            "request_hash": request.request_hash,
        }

    def _billing_key(self, request: LLMRequest, completion: Completion | None) -> str:
        """Price by the model that actually served (a refusal fallback may switch models)."""
        if completion and completion.model_id != request.body["model"]:
            for key, spec in self.registry.models.items():
                if spec.id == completion.model_id:
                    return key
        return request.model_key

    async def _fail(self, fields: dict[str, Any], exc: LLMError) -> LLMError:
        exc.call = CallInfo(**fields, error=f"{type(exc).__name__}: {exc}"[:500])
        await self._record(exc.call)
        return exc

    async def _record(self, call: CallInfo) -> None:
        if self.ledger is None:
            return
        try:
            await self.ledger.record(call)
        except Exception:  # accounting must never take down a user request
            log.exception("llm_ledger_write_failed", route=call.route, model=call.model_key)


def validate_output[T: BaseModel](completion: Completion, output_model: type[T]) -> T:
    """Check ``stop_reason`` before trusting the output, then validate it."""
    if completion.stop_reason == "refusal":
        raise LLMRefusalError(completion.stop_category)
    if completion.stop_reason in TRUNCATION_STOP_REASONS:
        raise LLMTruncatedError(f"output truncated (stop_reason={completion.stop_reason})")
    if not completion.text:
        raise LLMOutputError(f"no text output (stop_reason={completion.stop_reason})")
    try:
        return output_model.model_validate_json(completion.text)
    except ValidationError as exc:
        # Field locations only: the ValidationError text embeds receipt content (input_value=...).
        where = sorted({".".join(map(str, e["loc"])) for e in exc.errors(include_input=False)})
        name = output_model.__name__
        raise LLMOutputError(
            f"output does not match {name}: {exc.error_count()} error(s) at {where}"
        ) from exc
