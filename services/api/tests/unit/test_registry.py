from __future__ import annotations

import pytest

from claimpilot.config import API_ROOT
from claimpilot.llm.registry import ModelRegistry, load_registry


@pytest.fixture(scope="module")
def registry() -> ModelRegistry:
    return load_registry(API_ROOT / "config" / "models.yaml")


def test_registry_has_three_tiers(registry):
    assert {"haiku", "sonnet", "opus"} <= registry.models.keys()
    assert all(spec.id.startswith("claude-") for spec in registry.models.values())


def test_default_resolution(registry):
    r = registry.resolve("agent_chat", env={})
    assert r.model.key == "sonnet"
    assert r.effort == "low"
    assert not r.overridden


def test_env_override_switches_model(registry):
    r = registry.resolve(
        "extraction", env={"ROUTE_EXTRACTION": "opus", "ROUTE_EXTRACTION_EFFORT": "low"}
    )
    assert r.model.key == "opus"
    assert r.effort == "low"
    assert r.overridden


def test_override_onto_haiku_drops_unsupported_route_effort(registry):
    # agent_chat defaults to effort=low; Haiku rejects `effort`, so it must be dropped, not sent.
    r = registry.resolve("agent_chat", env={"ROUTE_AGENT_CHAT": "haiku"})
    assert r.model.key == "haiku"
    assert r.effort is None


def test_explicit_unsupported_effort_override_is_an_error(registry):
    with pytest.raises(ValueError, match="unsupported"):
        registry.resolve("extraction", env={"ROUTE_EXTRACTION_EFFORT": "low"})  # extraction=haiku


def test_unknown_model_override_is_an_error(registry):
    with pytest.raises(ValueError, match="not a registry model"):
        registry.resolve("extraction", env={"ROUTE_EXTRACTION": "gpt"})


def test_unknown_route(registry):
    with pytest.raises(KeyError):
        registry.resolve("nope", env={})


def test_cost_estimate(registry):
    # 1M input + 1M output on Haiku = $1 + $5
    assert registry.estimate_cost("haiku", input_tokens=1_000_000, output_tokens=1_000_000) == 6.0
    # cache reads are billed at the cache_read rate, not the input rate
    cost = registry.estimate_cost(
        "sonnet", input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000
    )
    assert cost == pytest.approx(0.20)


def test_invalid_route_config_rejected(registry):
    raw = registry.model_dump()
    raw["routes"]["bad"] = {"model": "haiku", "effort": "high", "max_tokens": 100}
    with pytest.raises(ValueError, match="does not support effort"):
        ModelRegistry.model_validate(raw)
