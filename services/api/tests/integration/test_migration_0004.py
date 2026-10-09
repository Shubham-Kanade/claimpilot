"""Migration 0004 (trace, document and claim ids on ``llm_calls``; trace id on ``batches``).

A database that already holds rows from before tracing must keep them all, with the new columns
empty, and be readable and writable by the current models; and the step must be reversible.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text

from claimpilot.config import API_ROOT, Settings
from claimpilot.db import create_engine as create_async_engine
from claimpilot.db import create_session_factory
from claimpilot.llm.ledger import CostLedger
from claimpilot.llm.types import CallInfo, TokenUsage
from claimpilot.pipeline.repo import NewFile, Repository
from claimpilot.telemetry import bound_ids

LLM_COLUMNS = {"trace_id", "document_id", "claim_id"}
NEW_INDEXES = {"ix_llm_calls_trace_id", "ix_llm_calls_document_id", "ix_llm_calls_claim_id"}
OLD_INDEXES = {"ix_llm_calls_sandbox", "ix_llm_calls_batch_id"}  # from 0003: must survive


def config(url: str) -> Config:
    cfg = Config(API_ROOT / "alembic.ini")
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False
    return cfg


async def run(action, url: str, revision: str) -> None:
    await asyncio.to_thread(action, config(url), revision)  # env.py runs its own event loop


@pytest.fixture
def url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / 'legacy.db').as_posix()}"


@pytest.fixture
def sync_engine(url: str) -> Iterator[Engine]:
    engine = create_engine(url.replace("+aiosqlite", ""))
    yield engine
    engine.dispose()


def insert_legacy_rows(engine: Engine) -> None:
    """What a database from before tracing holds: written with the 0003 columns only."""
    when = "2026-10-01 10:00:00.000000"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO batches (id, employee_id, status, total, processed, failed,"
                " created_at, sandbox) VALUES ('batch-legacy', 'P001', 'done', 1, 1, 0, :t,"
                " 'visitor-aaaaaaaaaaaa')"
            ),
            {"t": when},
        )
        conn.execute(
            text(
                "INSERT INTO documents (id, batch_id, employee_id, filename, sha256, storage_key,"
                " position, status, created_at, sandbox) VALUES ('doc-legacy', 'batch-legacy',"
                " 'P001', 'a.png', :sha, 'k/a.png', 0, 'processed', :t, 'visitor-aaaaaaaaaaaa')"
            ),
            {"sha": "a" * 64, "t": when},
        )
        conn.execute(
            text(
                "INSERT INTO llm_calls (id, created_at, route, model_key, model_id, mode,"
                " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd,"
                " latency_ms, request_hash, batch, sandbox, batch_id) VALUES"
                " ('11111111111111111111111111111111', :t, 'extraction', 'haiku', 'm', 'replay',"
                " 10, 5, 0, 0, 0.0004, 100, 'h', 0, 'visitor-aaaaaaaaaaaa', 'batch-legacy')"
            ),
            {"t": when},
        )


@pytest.fixture
async def legacy(url: str, sync_engine: Engine) -> str:
    """A database at revision 0003 holding one batch, document and LLM call."""
    await run(command.upgrade, url, "0003")
    insert_legacy_rows(sync_engine)
    return url


def columns(engine: Engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def indexes(engine: Engine, table: str = "llm_calls") -> set[str]:
    return {i["name"] for i in inspect(engine).get_indexes(table) if i["name"]}


async def test_revision_0003_has_none_of_the_new_columns_yet(legacy: str, sync_engine: Engine):
    assert not LLM_COLUMNS & columns(sync_engine, "llm_calls")
    assert "trace_id" not in columns(sync_engine, "batches")
    assert not NEW_INDEXES & indexes(sync_engine)


async def test_upgrading_adds_the_columns_and_indexes_and_matches_the_models(
    legacy: str, sync_engine: Engine
):
    await run(command.upgrade, legacy, "head")

    assert columns(sync_engine, "llm_calls") >= LLM_COLUMNS
    assert "trace_id" in columns(sync_engine, "batches")
    assert indexes(sync_engine) >= NEW_INDEXES
    assert indexes(sync_engine) >= OLD_INDEXES  # kept
    assert "ix_batches_trace_id" not in indexes(
        sync_engine, "batches"
    )  # batches.trace_id: no index
    await asyncio.to_thread(command.check, config(legacy))  # raises if the models drift


async def test_the_new_columns_are_nullable_with_the_documented_widths(
    legacy: str, sync_engine: Engine
):
    await run(command.upgrade, legacy, "head")

    inspector = inspect(sync_engine)
    wanted = {"trace_id": 64, "document_id": 36, "claim_id": 64}
    found = {c["name"]: c for c in inspector.get_columns("llm_calls")}
    for name, length in wanted.items():
        assert (
            found[name]["nullable"] is True
            and getattr(found[name]["type"], "length", None) == length
        )
    [trace] = [c for c in inspector.get_columns("batches") if c["name"] == "trace_id"]
    assert trace["nullable"] is True and getattr(trace["type"], "length", None) == 64


async def test_legacy_rows_survive_with_the_new_columns_empty(legacy: str, sync_engine: Engine):
    await run(command.upgrade, legacy, "head")

    with sync_engine.connect() as conn:
        call = conn.execute(
            text(
                "SELECT route, model_key, cost_usd, sandbox, batch_id, trace_id, document_id,"
                " claim_id FROM llm_calls"
            )
        ).one()
        batch = conn.execute(text("SELECT id, status, sandbox, trace_id FROM batches")).one()
        documents = conn.execute(text("SELECT id FROM documents")).all()
    assert tuple(call) == (
        "extraction",
        "haiku",
        0.0004,
        "visitor-aaaaaaaaaaaa",
        "batch-legacy",
        None,
        None,
        None,
    )
    assert tuple(batch) == ("batch-legacy", "done", "visitor-aaaaaaaaaaaa", None)
    assert [tuple(d) for d in documents] == [("doc-legacy",)]


async def test_the_current_models_read_and_write_a_migrated_database(legacy: str):
    await run(command.upgrade, legacy, "head")
    engine = create_async_engine(Settings(database_url=legacy))
    try:
        sessions = create_session_factory(engine)
        repo = Repository(sessions)
        assert await repo.batch_trace_id("batch-legacy") is None  # the legacy batch: no trace

        batch_id, _ = await repo.create_batch(
            "P001", [NewFile("b.png", "b" * 64, "k/b.png")], trace_id="req-abc-123"
        )
        with bound_ids(trace_id="req-abc-123", document_id="doc-1", claim_id="clm-1"):
            await CostLedger(sessions).record(
                CallInfo(
                    route="extraction",
                    model_key="haiku",
                    model_id="m",
                    effort=None,
                    mode="fake",
                    usage=TokenUsage(),
                    cost_usd=0.0,
                    latency_ms=1,
                    stop_reason="end_turn",
                    request_hash="h" * 64,
                )
            )
        assert await repo.batch_trace_id(batch_id) == "req-abc-123"
    finally:
        await engine.dispose()


async def test_downgrading_removes_the_columns_and_indexes_and_keeps_the_rows(
    legacy: str, sync_engine: Engine
):
    await run(command.upgrade, legacy, "head")
    await run(command.downgrade, legacy, "0003")

    assert not LLM_COLUMNS & columns(sync_engine, "llm_calls")
    assert "trace_id" not in columns(sync_engine, "batches")
    assert not NEW_INDEXES & indexes(sync_engine)
    assert indexes(sync_engine) >= OLD_INDEXES
    with sync_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM llm_calls")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM batches")).scalar_one() == 1


async def test_data_written_after_the_upgrade_is_dropped_cleanly_by_the_downgrade(
    legacy: str, sync_engine: Engine
):
    await run(command.upgrade, legacy, "head")
    with sync_engine.begin() as conn:
        conn.execute(
            text("UPDATE llm_calls SET trace_id = 't1', document_id = 'd1', claim_id = 'c1'")
        )
        conn.execute(text("UPDATE batches SET trace_id = 't1'"))

    await run(command.downgrade, legacy, "0003")
    await run(command.upgrade, legacy, "head")  # and back again

    with sync_engine.connect() as conn:
        call = conn.execute(text("SELECT trace_id, document_id, claim_id FROM llm_calls")).one()
        assert tuple(call) == (None, None, None)  # what the downgrade dropped does not come back
        assert conn.execute(text("SELECT trace_id FROM batches")).scalar_one() is None
    await asyncio.to_thread(command.check, config(legacy))


async def test_upgrading_a_fresh_database_straight_to_head_has_the_same_schema(
    url: str, sync_engine: Engine
):
    await run(command.upgrade, url, "head")

    assert columns(sync_engine, "llm_calls") >= LLM_COLUMNS
    assert indexes(sync_engine) >= NEW_INDEXES
    assert indexes(sync_engine) >= OLD_INDEXES
    await asyncio.to_thread(command.check, config(url))
