"""`/v1/ops/llm`: how the AI calls are going (volume, cost, latency, failures, caching).

Totals and the per-model table are system-wide (they describe the system, not a visitor). The
failure list and the per-trace drill-down show only the caller's own sandbox, so one demo visitor
never reads another's upload ids or receipt-related errors.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from claimpilot.api.deps import ContainerDep, PersonaDep
from claimpilot.db import LlmCall
from claimpilot.llm.ops import LlmOps, call_of, failure_of, summarise
from claimpilot.pipeline.repo import in_sandbox

router = APIRouter(prefix="/v1/ops", tags=["ops"])

MAX_ROWS = 5000  # the newest calls in the window; beyond that the figures say "sampled"
MAX_FAILURES = 10
MAX_TRACE_CALLS = 100


@router.get(
    "/llm",
    response_model=LlmOps,
    summary="LLM calls: volume, cost, latency, failures, caching (recorded vs live)",
)
async def llm_ops(
    container: ContainerDep,
    persona: PersonaDep,
    hours: Annotated[int, Query(ge=1, le=168, description="Look-back window in hours")] = 24,
    trace_id: Annotated[
        str | None, Query(max_length=64, description="Show the calls of one trace")
    ] = None,
) -> LlmOps:
    since = datetime.now(UTC) - timedelta(hours=hours)
    newest = (
        select(LlmCall)
        .where(LlmCall.created_at >= since)
        .order_by(LlmCall.created_at.desc())
        .limit(MAX_ROWS)
    )
    async with container.sessions() as session:
        rows = list((await session.scalars(newest)).all())
    totals, routes = summarise(rows)
    mine = [r for r in rows if r.sandbox == persona.sandbox]  # None matches None, like IS NULL
    failures = [failure_of(r) for r in mine if r.error is not None][:MAX_FAILURES]
    trace_calls = []
    if trace_id:
        query = (
            select(LlmCall)
            .where(
                LlmCall.trace_id == trace_id,
                in_sandbox(LlmCall.sandbox, persona.sandbox),
            )
            .order_by(LlmCall.created_at)
            .limit(MAX_TRACE_CALLS)
        )
        async with container.sessions() as session:
            trace_calls = [call_of(r) for r in (await session.scalars(query)).all()]
    return LlmOps(
        hours=hours,
        since=min((r.created_at for r in rows), default=None),
        sampled=len(rows) >= MAX_ROWS,
        totals=totals,
        routes=routes,
        failures=failures,
        trace_calls=trace_calls,
    )
