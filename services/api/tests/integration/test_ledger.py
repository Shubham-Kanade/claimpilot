"""CostLedger on a real (sqlite + aiosqlite) database created by the Alembic migration."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from claimpilot.config import API_ROOT, Settings
from claimpilot.db import LlmCall, SessionFactory, create_engine, create_session_factory
from claimpilot.llm.errors import LLMRefusalError
from claimpilot.llm.fake import FakeLLM
from claimpilot.llm.ledger import CostLedger
from claimpilot.llm.registry import ModelRegistry
from claimpilot.llm.types import CallInfo, Completion, TokenUsage


class ReceiptTotal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: float


def migrate(url: str) -> None:
    config = Config(API_ROOT / "alembic.ini")
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[SessionFactory]:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'ledger.db').as_posix()}"
    await asyncio.to_thread(migrate, url)  # env.py runs its own event loop
    engine = create_engine(Settings(database_url=url))
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def ledger(sessions: SessionFactory) -> CostLedger:
    return CostLedger(sessions)


def call(**overrides) -> CallInfo:
    fields = {
        "route": "extraction",
        "model_key": "haiku",
        "model_id": "claude-haiku-5-5",
        "effort": "low",
        "mode": "live",
        "usage": TokenUsage(input_tokens=1000, output_tokens=100, cache_read_tokens=10),
        "cost_usd": 0.00015,
        "latency_ms": 900,
        "stop_reason": "end_turn",
        "request_hash": "a" * 64,
    }
    return CallInfo(**{**fields, **overrides})


async def test_record_persists_every_field(ledger, sessions):
    await ledger.record(call(batch=True, error="LLMRefusalError: no"))
    async with sessions() as session:
        row = (await session.scalars(select(LlmCall))).one()
    assert row.id is not None
    assert row.created_at is not None
    assert (row.route, row.model_key, row.model_id, row.effort, row.mode) == (
        "extraction",
        "haiku",
        "claude-haiku-5-5",
        "low",
        "live",
    )
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens, row.cache_write_tokens) == (
        1000,
        100,
        10,
        0,
    )
    assert row.cost_usd == pytest.approx(0.00015)
    assert (row.latency_ms, row.stop_reason, row.request_hash) == (900, "end_turn", "a" * 64)
    assert row.batch is True
    assert row.error == "LLMRefusalError: no"


async def test_summary_totals_by_route_and_model(ledger):
    await ledger.record(call(cost_usd=0.001))
    await ledger.record(call(cost_usd=0.002, error="boom"))
    await ledger.record(call(route="agent_chat", model_key="sonnet", cost_usd=0.01))
    summary = await ledger.summary()
    assert summary.total.calls == 3
    assert summary.total.errors == 1
    assert summary.total.cost_usd == pytest.approx(0.013)
    assert summary.total.input_tokens == 3000
    assert summary.by_route["extraction"].calls == 2
    assert summary.by_route["extraction"].cost_usd == pytest.approx(0.003)
    assert summary.by_model["sonnet"].cost_usd == pytest.approx(0.01)
    assert set(summary.by_route) == {"extraction", "agent_chat"}


async def test_summary_filters_by_mode_and_since(ledger, sessions):
    await ledger.record(call(mode="replay", cost_usd=0.5))
    await ledger.record(call(mode="live", cost_usd=0.25))
    live = await ledger.summary(mode="live")
    assert live.total.calls == 1
    assert live.total.cost_usd == pytest.approx(0.25)

    future = datetime.now(UTC) + timedelta(minutes=1)
    assert (await ledger.summary(since=future)).total.calls == 0
    past = datetime.now(UTC) - timedelta(minutes=1)
    assert (await ledger.summary(since=past)).total.calls == 2


async def test_empty_ledger_summary(ledger):
    summary = await ledger.summary()
    assert summary.total.calls == 0
    assert summary.by_route == {}


async def test_client_records_successes_and_failures(ledger, models_registry: ModelRegistry):
    fake = FakeLLM(models_registry, ledger=ledger, env={})
    fake.register(ReceiptTotal, {"total": 1.5}, route="extraction")
    fake.register(
        ReceiptTotal,
        Completion(
            text=None,
            stop_reason="refusal",
            model_id="claude-sonnet-5-5",
            usage=TokenUsage(input_tokens=500),
        ),
        route="agent_chat",
    )
    ok = await fake.parse("extraction", system="s", content="x", output_model=ReceiptTotal)
    with pytest.raises(LLMRefusalError):
        await fake.parse("agent_chat", system="s", content="x", output_model=ReceiptTotal)

    summary = await ledger.summary()
    assert summary.total.calls == 2
    assert summary.total.errors == 1
    assert summary.by_route["extraction"].cost_usd == pytest.approx(ok.cost_usd)
    assert summary.by_model["sonnet"].cost_usd == pytest.approx(
        models_registry.estimate_cost("sonnet", input_tokens=500, output_tokens=0)
    )
