"""Capability shim: snapshot the full model x option matrix, then pin each rule explicitly."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from syrupy.assertion import SnapshotAssertion

from claimpilot.llm.params import REFUSAL_FALLBACK_BETA, LLMRequest, build_request
from claimpilot.llm.registry import ModelRegistry, ResolvedRoute
from claimpilot.llm.types import ThinkingMode

MODEL_KEYS = ["haiku", "haiku45", "sonnet", "opus"]
LOOKUP_TOOL = {
    "name": "lookup_policy",
    "description": "Look up an expense policy clause",
    "input_schema": {
        "type": "object",
        "properties": {"category": {"type": "string"}},
        "required": ["category"],
        "additionalProperties": False,
    },
}
FORCED = {"type": "tool", "name": "lookup_policy"}
USER = [{"role": "user", "content": "Total: Rs 120.50"}]


class ReceiptTotal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: float


def route_for(
    registry: ModelRegistry, model_key: str, *, effort: str | None = None, route: str = "extraction"
) -> ResolvedRoute:
    env = {f"ROUTE_{route.upper()}": model_key}
    if effort:
        env[f"ROUTE_{route.upper()}_EFFORT"] = effort
    return registry.resolve(route, env=env)


def build(
    registry: ModelRegistry,
    model_key: str,
    *,
    thinking: ThinkingMode = "auto",
    effort: str | None = None,
    **kwargs: Any,
) -> LLMRequest:
    kwargs.setdefault("system", "You extract receipt totals.")
    kwargs.setdefault("messages", USER)
    return build_request(route_for(registry, model_key, effort=effort), thinking=thinking, **kwargs)


def summary(request: LLMRequest) -> dict[str, Any]:
    """Everything except the (constant) messages, for compact snapshots."""
    body = {k: v for k, v in request.body.items() if k != "messages"}
    return {"body": body, "betas": list(request.betas), "effort": request.effort}


# --- exhaustive snapshots --------------------------------------------------------------------
VARIANTS: dict[str, dict[str, Any]] = {
    "structured": {"output_model": ReceiptTotal},
    "tools_auto": {"tools": [LOOKUP_TOOL]},
    "tools_forced": {"tools": [LOOKUP_TOOL], "tool_choice": FORCED},
}


@pytest.mark.parametrize("variant", list(VARIANTS))
@pytest.mark.parametrize("thinking", ["auto", "off"])
@pytest.mark.parametrize("model_key", MODEL_KEYS)
def test_shim_matrix_snapshot(
    models_registry: ModelRegistry,
    snapshot: SnapshotAssertion,
    model_key: str,
    thinking: ThinkingMode,
    variant: str,
) -> None:
    request = build(models_registry, model_key, thinking=thinking, **VARIANTS[variant])
    assert summary(request) == snapshot


def test_every_route_on_every_model_snapshot(
    models_registry: ModelRegistry, snapshot: SnapshotAssertion
) -> None:
    table = {}
    for route in models_registry.routes:
        for key in MODEL_KEYS:
            request = build_request(
                route_for(models_registry, key, route=route),
                system="s",
                messages=USER,
                output_model=ReceiptTotal,
            )
            body = request.body
            table[f"{route}/{key}"] = {
                "model": body["model"],
                "max_tokens": body["max_tokens"],
                "effort": request.effort,
                "thinking": body.get("thinking"),
                "betas": list(request.betas),
            }
    assert table == snapshot


# --- the rules, one by one -------------------------------------------------------------------
@pytest.mark.parametrize("model_key", MODEL_KEYS)
def test_model_id_and_max_tokens_come_from_registry(models_registry, model_key):
    request = build(models_registry, model_key)
    assert request.body["model"] == models_registry.models[model_key].id
    assert request.body["max_tokens"] == models_registry.routes["extraction"].max_tokens
    assert request.model_key == model_key
    assert request.route == "extraction"


@pytest.mark.parametrize("thinking", ["auto", "off"])
@pytest.mark.parametrize("model_key", MODEL_KEYS)
def test_effort_sent_explicitly_iff_supported(models_registry, model_key, thinking):
    request = build(models_registry, model_key, thinking=thinking)
    spec = models_registry.models[model_key]
    effort = request.body.get("output_config", {}).get("effort")
    assert (effort is not None) == spec.supports_effort
    assert request.effort == effort


def test_model_default_effort_used_when_route_has_none(models_registry):
    spec = models_registry.models["sonnet"]
    route = ResolvedRoute(route="r", model=spec, effort=None, max_tokens=100, overridden=False)
    request = build_request(route, system="s", messages=USER)
    assert request.body["output_config"] == {"effort": spec.default_effort}


def test_opus_cannot_disable_thinking_so_off_means_adaptive_at_low_effort(models_registry):
    request = build(models_registry, "opus", thinking="off", effort="high")
    assert request.body["thinking"] == {"type": "adaptive"}
    assert request.body["output_config"]["effort"] == "low"


@pytest.mark.parametrize(
    ("effort", "thinking"),
    [("low", {"type": "between_tools"}), ("high", {"type": "between_tools"})]
    + [(e, {"type": "adaptive"}) for e in ("xhigh", "max")],
)
def test_sonnet_off_uses_between_tools_only_up_to_high(models_registry, effort, thinking):
    request = build(models_registry, "sonnet", thinking="off", effort=effort)
    assert request.body["thinking"] == thinking
    assert request.body["output_config"]["effort"] == effort


def test_haiku_off_is_disabled_but_stays_adaptive_above_high(models_registry):
    assert build(models_registry, "haiku", thinking="off").body["thinking"] == {"type": "disabled"}
    above = build(models_registry, "haiku", thinking="off", effort="max")
    assert above.body["thinking"] == {"type": "adaptive"}


@pytest.mark.parametrize(("max_tokens", "budget"), [(4000, 2000), (1500, 1024), (1024, None)])
def test_haiku45_auto_uses_legacy_budget(models_registry, max_tokens, budget):
    spec = models_registry.models["haiku45"]
    route = ResolvedRoute(
        route="r", model=spec, effort=None, max_tokens=max_tokens, overridden=False
    )
    request = build_request(route, system="s", messages=USER)
    expected = {"type": "enabled", "budget_tokens": budget} if budget else None
    assert request.body.get("thinking") == expected
    assert "output_config" not in request.body  # no effort, no schema


def test_adaptive_model_whose_off_switch_is_omission(models_registry):
    spec = models_registry.models["haiku"].model_copy(update={"thinking_off": "omit"})
    route = ResolvedRoute(route="r", model=spec, effort="low", max_tokens=100, overridden=False)
    request = build_request(route, system="s", messages=USER, thinking="off")
    assert "thinking" not in request.body


def test_haiku45_off_omits_thinking(models_registry):
    assert "thinking" not in build(models_registry, "haiku45", thinking="off").body


@pytest.mark.parametrize("model_key", ["sonnet", "opus"])
def test_forced_tool_choice_downgraded_to_auto_with_strict_tools(models_registry, model_key):
    choice = {**FORCED, "disable_parallel_tool_use": True}
    request = build(models_registry, model_key, tools=[LOOKUP_TOOL], tool_choice=choice)
    assert request.body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert all(tool["strict"] is True for tool in request.body["tools"])
    assert "strict" not in LOOKUP_TOOL  # caller's tool untouched


@pytest.mark.parametrize("model_key", ["haiku", "haiku45"])
def test_forced_tool_choice_kept_where_allowed_and_turns_thinking_off(models_registry, model_key):
    request = build(models_registry, model_key, tools=[LOOKUP_TOOL], tool_choice=FORCED)
    assert request.body["tool_choice"] == FORCED
    assert "strict" not in request.body["tools"][0]
    assert request.body.get("thinking") in (None, {"type": "disabled"})


def test_auto_tool_choice_passes_through(models_registry):
    request = build(models_registry, "sonnet", tools=[LOOKUP_TOOL], tool_choice={"type": "auto"})
    assert request.body["tool_choice"] == {"type": "auto"}
    assert "strict" not in request.body["tools"][0]


@pytest.mark.parametrize("model_key", MODEL_KEYS)
def test_refusal_fallbacks_only_where_supported(models_registry, model_key):
    request = build(models_registry, model_key)
    if models_registry.models[model_key].refusal_fallbacks:
        assert request.body["fallbacks"] == "default"
        assert request.betas == (REFUSAL_FALLBACK_BETA,)
    else:
        assert "fallbacks" not in request.body
        assert request.betas == ()


def test_structured_output_schema_is_strict_json_schema(models_registry):
    request = build(models_registry, "haiku", output_model=ReceiptTotal)
    fmt = request.body["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["required"] == ["total"]
    assert request.output_model is ReceiptTotal


def test_cache_breakpoint_on_last_system_block_only(models_registry):
    stable = [{"type": "text", "text": "rules"}, {"type": "text", "text": "policy"}]
    request = build(models_registry, "haiku", system=stable)
    system = request.body["system"]
    assert "cache_control" not in system[0]
    assert system[1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in stable[1]  # caller's blocks untouched


def test_string_system_becomes_one_cached_block(models_registry):
    assert build(models_registry, "haiku", system="rules").body["system"] == [
        {"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}
    ]


def test_empty_system_omitted_and_cache_can_be_turned_off(models_registry):
    assert "system" not in build(models_registry, "haiku", system="").body
    request = build(models_registry, "haiku", system="rules", cache_system=False)
    assert request.body["system"] == [{"type": "text", "text": "rules"}]


def test_request_hash_is_stable_and_content_sensitive(models_registry):
    a = build(models_registry, "haiku", output_model=ReceiptTotal)
    b = build(models_registry, "haiku", output_model=ReceiptTotal)
    c = build(models_registry, "haiku", messages=[{"role": "user", "content": "Total: 9"}])
    assert a.request_hash == b.request_hash
    assert a.request_hash != c.request_hash
    assert len(a.request_hash) == 64
    assert '"model":"claude-haiku-5-5"' in a.canonical_json()


def test_count_tokens_body_drops_generation_only_params(models_registry):
    request = build(
        models_registry, "sonnet", output_model=ReceiptTotal, tools=[LOOKUP_TOOL], effort="high"
    )
    body = request.count_tokens_body()
    assert set(body) == {"model", "system", "messages", "thinking", "tools", "output_config"}
    assert body["output_config"] == {"format": request.body["output_config"]["format"]}
