"""Cost / accuracy / latency trade-off selection for the model bake-off (ADR-005)."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigResult:
    name: str  # e.g. "haiku-low", "sonnet-medium", "cascade"
    accuracy: float  # critical-field accuracy, 0..1
    cost_per_receipt: float  # USD
    p95_latency_ms: float
    passes_gates: bool = True

    @property
    def cost_per_1k(self) -> float:
        return self.cost_per_receipt * 1000


def pareto_frontier(results: Sequence[ConfigResult]) -> list[ConfigResult]:
    """Configs not dominated on (lower cost, higher accuracy), sorted by cost ascending.

    A config is dominated if another is no more expensive AND no less accurate,
    and strictly better on at least one of the two.
    """

    def dominates(a: ConfigResult, b: ConfigResult) -> bool:
        no_worse = a.cost_per_receipt <= b.cost_per_receipt and a.accuracy >= b.accuracy
        better = a.cost_per_receipt < b.cost_per_receipt or a.accuracy > b.accuracy
        return no_worse and better

    frontier = [r for r in results if not any(dominates(o, r) for o in results if o is not r)]
    return sorted(frontier, key=lambda r: (r.cost_per_receipt, -r.accuracy))


def choose(results: Sequence[ConfigResult], tie_accuracy: float = 0.01) -> ConfigResult | None:
    """Cheapest config on the frontier of those that pass the eval gates.

    If a slightly more expensive config is within ``tie_accuracy`` of it, prefer the one with the
    lower p95 latency (the bake-off skill's tie-break rule).
    """
    # Gate first: a failing config must not knock a passing one off the frontier.
    eligible = pareto_frontier([r for r in results if r.passes_gates])
    if not eligible:
        return None
    best = eligible[0]
    ties = [r for r in eligible if abs(r.accuracy - best.accuracy) <= tie_accuracy]
    return min(ties, key=lambda r: (r.p95_latency_ms, r.cost_per_receipt))


def p95(values: Sequence[float]) -> float:
    """95th percentile (inclusive method); a single value is its own p95."""
    if not values:
        raise ValueError("no values")
    if len(values) == 1:
        return float(values[0])
    return statistics.quantiles(values, n=20, method="inclusive")[18]
