"""Capability shim: the only code that turns a resolved route into Messages API parameters.

Pure and deterministic, so the full model x option matrix is snapshot-tested. Per-model rules
(sources: claude-api skill docs + models.yaml, which mirrors the Models API):

* ``model`` always comes from the registry; ``max_tokens`` from the route.
* ``output_config.effort`` is sent explicitly whenever the model supports effort (route effort,
  else the model default) and never otherwise (Haiku 4.5 returns 400 on ``effort``).
* Thinking ``"auto"``: ``{type: adaptive}`` on adaptive models; on Haiku 4.5 the legacy
  ``{type: enabled, budget_tokens}`` with half of ``max_tokens`` (>= 1024 and < ``max_tokens``),
  omitted when the route is too small for the minimum budget.
* Thinking ``"off"`` uses the model's ``thinking_off`` capability:
    - ``omit`` (Haiku 4.5): no ``thinking`` field;
    - ``disabled`` (Haiku 5.5) / ``between_tools`` (Sonnet 5.5): only at effort <= high, because
      the API rejects thinking-off at ``xhigh``/``max``; above that thinking stays adaptive;
    - ``None`` (Opus 5.5): thinking cannot be disabled (any disable is a 400), so it stays
      adaptive and effort is forced to ``low``, the documented way to minimise thinking.
* Forced ``tool_choice`` (``any``/``tool``) is a 400 on Sonnet/Opus 5.5: it is downgraded to
  ``auto`` and every tool is marked ``strict`` (callers must check a ``tool_use`` came back).
  Where forcing is allowed it implies thinking off, since extended thinking only supports
  ``auto``/``none`` tool choice.
* Structured outputs go in ``output_config.format`` using the SDK's own ``transform_schema``
  (exactly what ``messages.parse`` sends), so the request body is plain JSON we can hash/replay.
* A ``cache_control`` breakpoint goes on the last system block: tools -> system is the stable
  prefix; anything volatile belongs in ``messages``.
* Models with ``refusal_fallbacks`` get ``fallbacks: "default"`` plus its beta header
  (Messages API only; it is rejected on the Batches API, so batch callers must drop it).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from anthropic import transform_schema
from pydantic import BaseModel

from claimpilot.llm.registry import ModelSpec, ResolvedRoute
from claimpilot.llm.types import ThinkingMode

REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"  # pairs with fallbacks="default" only
EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")
MAX_EFFORT_WITHOUT_THINKING = "high"
LEGACY_MIN_THINKING_BUDGET = 1024
EPHEMERAL = {"type": "ephemeral"}

# Keys the count_tokens endpoint accepts (it has no max_tokens / fallbacks / betas).
_COUNT_TOKENS_KEYS = ("model", "system", "messages", "thinking", "tools", "tool_choice")

Block = Mapping[str, Any]
SystemPrompt = str | Sequence[Block]


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """A fully built Messages API request plus the routing metadata the ledger needs."""

    route: str
    model_key: str
    effort: str | None  # effective effort actually sent (None when the model has no effort)
    body: dict[str, Any]  # JSON-only kwargs for ``messages.create``
    betas: tuple[str, ...] = ()
    # Python type to validate the output against; already encoded in ``body`` as JSON schema.
    output_model: type[BaseModel] | None = field(default=None, compare=False)

    def canonical_json(self) -> str:
        """Stable serialisation (sorted keys) of everything sent to the API. Holds no secrets."""
        payload = {"body": self.body, "betas": list(self.betas)}
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def request_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def count_tokens_body(self) -> dict[str, Any]:
        body = {k: self.body[k] for k in _COUNT_TOKENS_KEYS if k in self.body}
        output_format = self.body.get("output_config", {}).get("format")
        if output_format is not None:
            body["output_config"] = {"format": output_format}
        return body


def build_request(
    route: ResolvedRoute,
    *,
    system: SystemPrompt,
    messages: Sequence[Block],
    output_model: type[BaseModel] | None = None,
    tools: Sequence[Block] | None = None,
    tool_choice: Block | None = None,
    thinking: ThinkingMode = "auto",
    cache_system: bool = True,
) -> LLMRequest:
    model = route.model
    effort = _base_effort(route)
    tool_list, tool_choice = _tools(model, tools, tool_choice)
    if _is_forced(tool_choice):
        thinking = "off"
    thinking_param, effort = _thinking(model, thinking, effort, route.max_tokens)

    body: dict[str, Any] = {"model": model.id, "max_tokens": route.max_tokens}
    if system_blocks := _system(system, cache=cache_system):
        body["system"] = system_blocks
    body["messages"] = [dict(m) for m in messages]
    if thinking_param is not None:
        body["thinking"] = thinking_param
    if output_config := _output_config(effort, output_model):
        body["output_config"] = output_config
    if tool_list:
        body["tools"] = tool_list
    if tool_choice is not None:
        body["tool_choice"] = tool_choice

    betas: tuple[str, ...] = ()
    if model.refusal_fallbacks:
        body["fallbacks"] = "default"
        betas = (REFUSAL_FALLBACK_BETA,)
    return LLMRequest(
        route=route.route,
        model_key=model.key,
        effort=effort,
        body=body,
        betas=betas,
        output_model=output_model,
    )


def _base_effort(route: ResolvedRoute) -> str | None:
    if not route.model.supports_effort:
        return None
    return route.effort or route.model.default_effort


def _thinking(
    model: ModelSpec, mode: ThinkingMode, effort: str | None, max_tokens: int
) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(thinking param or None, effective effort)``."""
    if model.thinking == "budget_tokens":
        if mode == "off":
            return None, effort
        budget = max(LEGACY_MIN_THINKING_BUDGET, max_tokens // 2)
        return (
            {"type": "enabled", "budget_tokens": budget} if budget < max_tokens else None
        ), effort

    adaptive = {"type": "adaptive"}
    if mode == "auto":
        return adaptive, effort
    if model.thinking_off is None:  # e.g. Opus 5.5: cannot disable; minimise via effort
        return adaptive, "low" if model.supports_effort else effort
    if effort is not None and _rank(effort) > _rank(MAX_EFFORT_WITHOUT_THINKING):
        return adaptive, effort  # thinking-off is a 400 at xhigh/max
    if model.thinking_off == "omit":
        return None, effort
    return {"type": model.thinking_off}, effort


def _rank(effort: str) -> int:
    return EFFORT_ORDER.index(effort)


def _is_forced(tool_choice: Block | None) -> bool:
    return tool_choice is not None and tool_choice.get("type") in ("any", "tool")


def _tools(
    model: ModelSpec, tools: Sequence[Block] | None, tool_choice: Block | None
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    tool_list = [dict(t) for t in tools or ()]
    choice = dict(tool_choice) if tool_choice is not None else None
    if choice is not None and _is_forced(choice) and not model.forced_tool_choice:
        downgraded: dict[str, Any] = {"type": "auto"}
        if "disable_parallel_tool_use" in choice:
            downgraded["disable_parallel_tool_use"] = choice["disable_parallel_tool_use"]
        return [{**t, "strict": True} for t in tool_list], downgraded
    return tool_list, choice


def _system(system: SystemPrompt, *, cache: bool) -> list[dict[str, Any]]:
    blocks = (
        [{"type": "text", "text": system}] if isinstance(system, str) else [dict(b) for b in system]
    )
    blocks = [b for b in blocks if b.get("type") != "text" or b.get("text")]
    if cache and blocks:
        blocks[-1] = {**blocks[-1], "cache_control": EPHEMERAL}
    return blocks


def _output_config(
    effort: str | None, output_model: type[BaseModel] | None
) -> dict[str, Any] | None:
    config: dict[str, Any] = {}
    if effort is not None:
        config["effort"] = effort
    if output_model is not None:
        schema = transform_schema(output_model.model_json_schema())
        config["format"] = {"type": "json_schema", "schema": schema}
    return config or None
