"""The AI-operations view of the cost ledger: how the model calls are going.

Pure functions over ``LlmCall`` rows (no I/O), so the numbers are unit-testable. The ledger holds
one row per call with its route, model, tokens, cost, latency, outcome and the ids that tie it to
the upload that caused it. Replayed calls (answered from a recording) carry the latency and the
*would-be* cost of the recorded call, so they are reported separately from live ones, never added
to the money actually spent.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from claimpilot.db import LlmCall

FailureKind = Literal["error", "not_recorded", "budget"]

NOT_RECORDED = "The demo has no recording for this receipt (only the sample receipts can be read)."
BUDGET = "The daily budget for live model calls was used up."
_PATH = re.compile(r"(?:[A-Za-z]:)?[\\/][\w .\\/-]+")  # a file path in an error text
MESSAGE_CHARS = 160


class OpsTotals(BaseModel):
    calls: int
    live_calls: int
    recorded_calls: int  # answered from a recording: free, and not counted in the money below
    errors: int  # real failures only (a missing recording or a used-up budget is not an error)
    not_recorded: int
    budget_refusals: int
    error_rate: float  # errors / calls
    live_cost_usd: float
    recorded_cost_usd: float  # what the replayed calls would have cost
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_read_share: float  # cached share of all prompt tokens (prompt caching)


class OpsRoute(BaseModel):
    route: str
    model_key: str
    calls: int
    live_calls: int
    errors: int
    p50_ms: int
    p95_ms: int
    live_cost_usd: float
    recorded_cost_usd: float


class OpsFailure(BaseModel):
    at: datetime
    route: str
    model_key: str
    kind: FailureKind
    message: str
    trace_id: str | None
    batch_id: str | None
    document_id: str | None


class OpsCall(BaseModel):
    at: datetime
    route: str
    model_key: str
    mode: str
    latency_ms: int
    cost_usd: float
    error: str | None
    document_id: str | None
    claim_id: str | None


class LlmOps(BaseModel):
    hours: int
    since: datetime | None  # the oldest call in the window (the ledger resets with the demo)
    sampled: bool  # the window held more calls than the cap: figures cover the newest ones
    totals: OpsTotals
    routes: list[OpsRoute]
    failures: list[OpsFailure]  # newest first, only the caller's own sandbox
    trace_calls: list[OpsCall]  # the calls of one trace (when asked), only the caller's sandbox


def percentile(values: Sequence[int], q: float) -> int:
    """Nearest-rank percentile: the smallest value with at least ``q`` of the data at or below."""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def classify_error(error: str) -> tuple[FailureKind, str]:
    """What kind of failure a ledger error text is, and a message that is safe to show."""
    name = error.split(":", 1)[0].strip()
    if name == "ReplayMissError":
        return "not_recorded", NOT_RECORDED
    if name == "LLMBudgetError":
        return "budget", BUDGET
    detail = _PATH.sub("<path>", error)  # server paths are of no use to a visitor
    return "error", detail[:MESSAGE_CHARS]


def summarise(rows: Iterable[LlmCall]) -> tuple[OpsTotals, list[OpsRoute]]:
    totals = defaultdict(float)
    groups: dict[tuple[str, str], list[LlmCall]] = defaultdict(list)
    for row in rows:
        groups[(row.route, row.model_key)].append(row)
        live = row.mode == "live"
        totals["calls"] += 1
        totals["live_calls" if live else "recorded_calls"] += 1
        totals["live_cost" if live else "recorded_cost"] += row.cost_usd
        totals["input"] += row.input_tokens
        totals["output"] += row.output_tokens
        totals["cache_read"] += row.cache_read_tokens
        if row.error is not None:
            kind, _ = classify_error(row.error)
            totals[{"error": "errors", "not_recorded": "miss", "budget": "budget"}[kind]] += 1
    calls = int(totals["calls"])
    prompt_tokens = totals["input"] + totals["cache_read"]
    summary = OpsTotals(
        calls=calls,
        live_calls=int(totals["live_calls"]),
        recorded_calls=int(totals["recorded_calls"]),
        errors=int(totals["errors"]),
        not_recorded=int(totals["miss"]),
        budget_refusals=int(totals["budget"]),
        error_rate=totals["errors"] / calls if calls else 0.0,
        live_cost_usd=totals["live_cost"],
        recorded_cost_usd=totals["recorded_cost"],
        input_tokens=int(totals["input"]),
        output_tokens=int(totals["output"]),
        cache_read_tokens=int(totals["cache_read"]),
        cache_read_share=totals["cache_read"] / prompt_tokens if prompt_tokens else 0.0,
    )
    routes = [
        OpsRoute(
            route=route,
            model_key=model_key,
            calls=len(items),
            live_calls=sum(r.mode == "live" for r in items),
            errors=sum(
                r.error is not None and classify_error(r.error)[0] == "error" for r in items
            ),
            p50_ms=percentile([r.latency_ms for r in items], 0.50),
            p95_ms=percentile([r.latency_ms for r in items], 0.95),
            live_cost_usd=sum(r.cost_usd for r in items if r.mode == "live"),
            recorded_cost_usd=sum(r.cost_usd for r in items if r.mode != "live"),
        )
        for (route, model_key), items in sorted(groups.items())
    ]
    return summary, routes


def failure_of(row: LlmCall) -> OpsFailure:
    kind, message = classify_error(row.error or "")
    return OpsFailure(
        at=row.created_at,
        route=row.route,
        model_key=row.model_key,
        kind=kind,
        message=message,
        trace_id=row.trace_id,
        batch_id=row.batch_id,
        document_id=row.document_id,
    )


def call_of(row: LlmCall) -> OpsCall:
    return OpsCall(
        at=row.created_at,
        route=row.route,
        model_key=row.model_key,
        mode=row.mode,
        latency_ms=row.latency_ms,
        cost_usd=row.cost_usd,
        error=classify_error(row.error)[1] if row.error else None,
        document_id=row.document_id,
        claim_id=row.claim_id,
    )
