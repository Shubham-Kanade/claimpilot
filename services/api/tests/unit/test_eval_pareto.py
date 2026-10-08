from __future__ import annotations

import pytest

from claimpilot.evals.pareto import ConfigResult, choose, p95, pareto_frontier


def R(name, acc, cost, lat=1000.0, gates=True):
    return ConfigResult(name, acc, cost, lat, gates)


def test_frontier_drops_dominated_configs():
    results = [
        R("haiku", 0.95, 0.0004),
        R("haiku45", 0.93, 0.004),  # dearer and worse than haiku -> dominated
        R("sonnet", 0.98, 0.01),
        R("opus", 0.98, 0.02),  # same accuracy as sonnet, dearer -> dominated
    ]
    assert [r.name for r in pareto_frontier(results)] == ["haiku", "sonnet"]


def test_choose_cheapest_passing_config():
    results = [
        R("haiku", 0.90, 0.0004, gates=False),
        R("sonnet", 0.97, 0.01),
        R("opus", 0.99, 0.02),
    ]
    chosen = choose(results)
    assert chosen is not None and chosen.name == "sonnet"


def test_choose_prefers_lower_latency_on_accuracy_tie():
    results = [R("haiku", 0.960, 0.0004, lat=3000), R("cascade", 0.965, 0.0009, lat=1200)]
    chosen = choose(results)
    assert chosen is not None and chosen.name == "cascade"


def test_choose_none_when_nothing_passes():
    assert choose([R("haiku", 0.5, 0.001, gates=False)]) is None


def test_cost_per_1k():
    assert R("x", 1.0, 0.0004).cost_per_1k == pytest.approx(0.4)


def test_p95():
    assert p95([100.0]) == 100.0
    assert p95([float(i) for i in range(1, 101)]) == pytest.approx(95.05)
    with pytest.raises(ValueError):
        p95([])


def test_failing_config_cannot_knock_a_passing_one_off_the_frontier():
    # haiku fails the gates but dominates haiku45; we must still pick haiku45, not sonnet (2.5x).
    results = [
        R("haiku", 0.96, 0.0004, gates=False),
        R("haiku45", 0.95, 0.004),
        R("sonnet", 0.98, 0.01),
    ]
    chosen = choose(results)
    assert chosen is not None and chosen.name == "haiku45"
