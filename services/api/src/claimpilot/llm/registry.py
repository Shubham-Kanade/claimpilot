"""Model registry: per-model capabilities and prices, plus per-route assignment.

Nothing else in the codebase may hold a model ID. Callers ask for a *route* (e.g. ``extraction``),
and the registry resolves it to a model, applying ``ROUTE_<NAME>`` and
``ROUTE_<NAME>_EFFORT`` env overrides.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

Effort = Literal["low", "medium", "high", "xhigh", "max"]


BATCH_DISCOUNT = 0.5  # Message Batches API: 50% off input and output


class PriceTier(BaseModel):
    """USD per 1M tokens."""

    model_config = ConfigDict(frozen=True)

    input: float
    output: float
    cache_read: float
    cache_write_5m: float


class LongContextTier(PriceTier):
    """Rates that apply once the whole prompt exceeds ``threshold_tokens`` (e.g. Haiku 5.5)."""

    threshold_tokens: int


class Price(PriceTier):
    long_context: LongContextTier | None = None

    def tier_for(self, prompt_tokens: int) -> PriceTier:
        if self.long_context and prompt_tokens > self.long_context.threshold_tokens:
            return self.long_context
        return self


class ModelSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    id: str
    context_tokens: int
    max_output_tokens: int
    price: Price
    thinking: Literal["budget_tokens", "adaptive"]
    # How to turn thinking off: omit the field, send {type: disabled}, send {type: between_tools},
    # or None when the model cannot run without thinking.
    thinking_off: Literal["omit", "disabled", "between_tools"] | None
    effort_levels: tuple[Effort, ...]
    default_effort: Effort | None
    forced_tool_choice: bool
    structured_outputs: bool
    refusal_fallbacks: bool
    min_cache_prefix: int
    vision: bool
    pdf: bool

    @property
    def supports_effort(self) -> bool:
        return bool(self.effort_levels)


class RouteSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    effort: Effort | None = None
    max_tokens: int


class ResolvedRoute(BaseModel):
    """A route bound to a concrete model, with effective effort after overrides."""

    model_config = ConfigDict(frozen=True)

    route: str
    model: ModelSpec
    effort: Effort | None
    max_tokens: int
    overridden: bool


class ModelRegistry(BaseModel):
    model_config = ConfigDict(frozen=True)

    pricing_checked: date
    models: dict[str, ModelSpec]
    routes: dict[str, RouteSpec]

    @model_validator(mode="after")
    def _check_routes(self) -> ModelRegistry:
        for name, route in self.routes.items():
            if route.model not in self.models:
                raise ValueError(f"route '{name}' references unknown model '{route.model}'")
            spec = self.models[route.model]
            if route.effort is not None and route.effort not in spec.effort_levels:
                raise ValueError(
                    f"route '{name}': model '{route.model}' "
                    f"does not support effort '{route.effort}'"
                )
            if route.max_tokens > spec.max_output_tokens:
                raise ValueError(f"route '{name}': max_tokens exceeds {spec.key} output cap")
        return self

    def resolve(self, route: str, env: Mapping[str, str] | None = None) -> ResolvedRoute:
        """Resolve a route, honouring ``ROUTE_<NAME>`` and ``ROUTE_<NAME>_EFFORT`` overrides."""
        if route not in self.routes:
            raise KeyError(f"unknown route '{route}'")
        env = os.environ if env is None else env
        base = self.routes[route]
        prefix = f"ROUTE_{route.upper()}"

        model_key = env.get(prefix, base.model).strip().lower()
        if model_key not in self.models:
            raise ValueError(
                f"{prefix}={model_key!r} is not a registry model {sorted(self.models)}"
            )
        spec = self.models[model_key]

        effort_override = env.get(f"{prefix}_EFFORT")
        effort: str | None = effort_override.strip().lower() if effort_override else base.effort
        if effort is not None and effort not in spec.effort_levels:
            if effort_override:
                raise ValueError(f"{prefix}_EFFORT={effort!r} unsupported by model '{model_key}'")
            effort = None  # e.g. route default 'low' overridden onto Haiku: drop silently

        return ResolvedRoute(
            route=route,
            model=spec,
            effort=effort,  # type: ignore[arg-type]  # validated against Effort levels above
            max_tokens=min(base.max_tokens, spec.max_output_tokens),
            overridden=model_key != base.model or bool(effort_override),
        )

    def estimate_cost(
        self,
        model_key: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        batch: bool = False,
    ) -> float:
        """USD cost of one call. ``input_tokens`` excludes cached reads and writes.

        The price tier is chosen by the whole prompt size (uncached + cache read + cache write),
        and the Batch API discount is applied on top when ``batch`` is set.
        """
        prompt_tokens = input_tokens + cache_read_tokens + cache_write_tokens
        p = self.models[model_key].price.tier_for(prompt_tokens)
        cost = (
            input_tokens * p.input
            + output_tokens * p.output
            + cache_read_tokens * p.cache_read
            + cache_write_tokens * p.cache_write_5m
        ) / 1_000_000
        return cost * BATCH_DISCOUNT if batch else cost


def load_registry(path: Path) -> ModelRegistry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["models"] = {key: {"key": key, **spec} for key, spec in raw["models"].items()}
    return ModelRegistry.model_validate(raw)


@lru_cache
def get_registry() -> ModelRegistry:
    from claimpilot.config import get_settings

    return load_registry(get_settings().models_config)
