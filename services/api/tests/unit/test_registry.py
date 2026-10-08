from __future__ import annotations

import pytest

from claimpilot.config import API_ROOT
from claimpilot.llm.registry import ModelRegistry, load_registry


@pytest.fixture(scope="module")
def registry() -> ModelRegistry:
    return load_registry(API_ROOT / "config" / "models.yaml")


def test_registry_has_all_tiers(registry):
    assert {"haiku", "haiku45", "sonnet", "opus"} <= registry.models.keys()
    assert registry.models["haiku"].id == "claude-haiku-5-5"
    assert all(spec.id.startswith("claude-") for spec in registry.models.values())


def test_every_route_resolves_by_default(registry):
    for name in registry.routes:
        resolved = registry.resolve(name, env={})
        assert resolved.model.key in registry.models
        assert resolved.max_tokens <= resolved.model.max_output_tokens


def test_default_resolution(registry):
    r = registry.resolve("agent_chat", env={})
    assert r.model.key == "sonnet"
    assert r.effort == "low"
    assert not r.overridden


def test_env_override_switches_model(registry):
    r = registry.resolve(
        "extraction", env={"ROUTE_EXTRACTION": "opus", "ROUTE_EXTRACTION_EFFORT": "medium"}
    )
    assert r.model.key == "opus"
    assert r.effort == "medium"
    assert r.overridden


def test_override_is_case_insensitive(registry):
    assert (
        registry.resolve("extraction", env={"ROUTE_EXTRACTION": " Sonnet "}).model.key == "sonnet"
    )


def test_override_onto_haiku45_drops_unsupported_route_effort(registry):
    # extraction defaults to effort=low; Haiku 4.5 rejects `effort`: drop it, never send it.
    r = registry.resolve("extraction", env={"ROUTE_EXTRACTION": "haiku45"})
    assert r.model.key == "haiku45"
    assert r.effort is None


def test_explicit_unsupported_effort_override_is_an_error(registry):
    env = {"ROUTE_EXTRACTION": "haiku45", "ROUTE_EXTRACTION_EFFORT": "low"}
    with pytest.raises(ValueError, match="unsupported"):
        registry.resolve("extraction", env=env)


def test_unknown_model_override_is_an_error(registry):
    with pytest.raises(ValueError, match="not a registry model"):
        registry.resolve("extraction", env={"ROUTE_EXTRACTION": "gpt"})


def test_unknown_route(registry):
    with pytest.raises(KeyError):
        registry.resolve("nope", env={})


def test_cost_estimate_standard_tier(registry):
    # Haiku 5.5 under 100K prompt tokens: $0.10 in / $0.50 out per MTok
    cost = registry.estimate_cost("haiku", input_tokens=50_000, output_tokens=10_000)
    assert cost == pytest.approx(50_000 * 0.10 / 1e6 + 10_000 * 0.50 / 1e6)


def test_cost_estimate_long_context_tier_uses_whole_prompt(registry):
    # 60K uncached + 60K cache read = 120K prompt -> Haiku 5.5 long-context rates apply to all parts
    cost = registry.estimate_cost(
        "haiku", input_tokens=60_000, output_tokens=1_000, cache_read_tokens=60_000
    )
    assert cost == pytest.approx((60_000 * 0.50 + 1_000 * 2.50 + 60_000 * 0.05) / 1e6)


def test_cost_estimate_cache_and_batch(registry):
    # Sonnet 5.5 cache reads are 0.05x input = $0.10/MTok
    read = registry.estimate_cost(
        "sonnet", input_tokens=0, output_tokens=0, cache_read_tokens=10**6
    )
    assert read == pytest.approx(0.10)
    full = registry.estimate_cost("opus", input_tokens=1_000, output_tokens=1_000)
    assert registry.estimate_cost(
        "opus", input_tokens=1_000, output_tokens=1_000, batch=True
    ) == pytest.approx(full / 2)


def test_models_without_long_context_tier_use_flat_price(registry):
    big = registry.estimate_cost("sonnet", input_tokens=500_000, output_tokens=0)
    assert big == pytest.approx(500_000 * 2.00 / 1e6)


def test_invalid_route_config_rejected(registry):
    raw = registry.model_dump()
    raw["routes"]["bad"] = {"model": "haiku45", "effort": "high", "max_tokens": 100}
    with pytest.raises(ValueError, match="does not support effort"):
        ModelRegistry.model_validate(raw)


def test_route_with_unknown_model_rejected(registry):
    raw = registry.model_dump()
    raw["routes"]["bad"] = {"model": "nope", "max_tokens": 100}
    with pytest.raises(ValueError, match="unknown model"):
        ModelRegistry.model_validate(raw)


def test_route_exceeding_output_cap_rejected(registry):
    raw = registry.model_dump()
    raw["routes"]["bad"] = {"model": "haiku45", "max_tokens": 10**6}
    with pytest.raises(ValueError, match="output cap"):
        ModelRegistry.model_validate(raw)
