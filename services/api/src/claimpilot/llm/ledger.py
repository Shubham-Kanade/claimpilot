"""Cost ledger: persists every LLM call to ``llm_calls`` and reports spend.

Replay and fake calls are recorded too (with their would-be cost) so evals can report cost in
any mode; filter with ``mode="live"`` for money actually spent.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import func, select

from claimpilot.db import LlmCall, SessionFactory
from claimpilot.llm.types import CallInfo, LLMMode
from claimpilot.telemetry import current_ids

ERROR_MAX_CHARS = 500  # matches llm_calls.error String(500)


class SpendTotals(BaseModel):
    calls: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, row: LlmCall) -> None:
        self.calls += 1
        self.errors += row.error is not None
        self.input_tokens += row.input_tokens
        self.output_tokens += row.output_tokens
        self.cache_read_tokens += row.cache_read_tokens
        self.cache_write_tokens += row.cache_write_tokens
        self.cost_usd += row.cost_usd


class SpendSummary(BaseModel):
    total: SpendTotals
    by_route: dict[str, SpendTotals]
    by_model: dict[str, SpendTotals]


class CostLedger:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def record(self, call: CallInfo) -> None:
        ids = current_ids()  # whose call this was (the demo sandbox, the batch), if anyone's
        row = LlmCall(
            route=call.route,
            model_key=call.model_key,
            model_id=call.model_id,
            effort=call.effort,
            mode=call.mode,
            input_tokens=call.usage.input_tokens,
            output_tokens=call.usage.output_tokens,
            cache_read_tokens=call.usage.cache_read_tokens,
            cache_write_tokens=call.usage.cache_write_tokens,
            cost_usd=call.cost_usd,
            latency_ms=call.latency_ms,
            stop_reason=call.stop_reason,
            request_hash=call.request_hash,
            batch=call.batch,
            error=call.error[:ERROR_MAX_CHARS] if call.error else None,
            sandbox=ids["sandbox"],
            batch_id=ids["batch_id"],
            trace_id=ids["trace_id"],
            document_id=ids["document_id"],
            claim_id=ids["claim_id"],
        )
        async with self._sessions() as session:
            session.add(row)
            await session.commit()

    async def live_spend_since(self, since: datetime) -> float:
        """Dollars spent on live calls since ``since`` (replay and fake calls cost nothing)."""
        query = select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(
            LlmCall.mode == "live", LlmCall.created_at >= since
        )
        async with self._sessions() as session:
            return float((await session.execute(query)).scalar_one())

    async def summary(
        self, since: datetime | None = None, *, mode: LLMMode | None = None
    ) -> SpendSummary:
        query = select(LlmCall)
        if since is not None:
            query = query.where(LlmCall.created_at >= since)
        if mode is not None:
            query = query.where(LlmCall.mode == mode)
        async with self._sessions() as session:
            rows = (await session.scalars(query)).all()

        total = SpendTotals()
        by_route: defaultdict[str, SpendTotals] = defaultdict(SpendTotals)
        by_model: defaultdict[str, SpendTotals] = defaultdict(SpendTotals)
        for row in rows:
            for bucket in (total, by_route[row.route], by_model[row.model_key]):
                bucket.add(row)
        return SpendSummary(total=total, by_route=dict(by_route), by_model=dict(by_model))
