"""Migration 0003 (the ``sandbox`` columns) on a database that already holds legacy rows."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text

from claimpilot.config import API_ROOT, Settings
from claimpilot.db import create_engine as create_async_engine
from claimpilot.db import create_session_factory
from claimpilot.domain.claims import Claim, ClaimMode
from claimpilot.pipeline.repo import NewFile, Repository, collect_stats

SANDBOXED = ("batches", "documents", "claims", "llm_calls")
NEW_INDEXES = {f"ix_{t}_sandbox" for t in SANDBOXED} | {"ix_llm_calls_batch_id"}
LEGACY_CLAIM = Claim(
    id="clm-legacy",
    employee_id="P001",
    title="Local conveyance Oct 2026",
    mode=ClaimMode.period,
    document_ids=["doc-legacy"],
    total=320.0,
    start_date=date(2026, 10, 3),
)


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
    """What a database from before the sandboxes holds: written with the 0002 columns only."""
    when = "2026-10-01 10:00:00.000000"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO batches (id, employee_id, status, total, processed, failed,"
                " created_at) VALUES ('batch-legacy', 'P001', 'done', 1, 1, 0, :t)"
            ),
            {"t": when},
        )
        conn.execute(
            text(
                "INSERT INTO documents (id, batch_id, employee_id, filename, sha256, storage_key,"
                " position, status, created_at) VALUES ('doc-legacy', 'batch-legacy', 'P001',"
                " 'a.png', :sha, 'k/a.png', 0, 'processed', :t)"
            ),
            {"sha": "a" * 64, "t": when},
        )
        conn.execute(
            text(
                "INSERT INTO claims (id, batch_id, employee_id, status, route, data, created_at,"
                " updated_at) VALUES ('clm-legacy', 'batch-legacy', 'P001', 'needs_info',"
                " 'finance_review', :data, :t, :t)"
            ),
            {"data": LEGACY_CLAIM.model_dump_json(), "t": when},
        )
        conn.execute(
            text(
                "INSERT INTO llm_calls (id, created_at, route, model_key, model_id, mode,"
                " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd,"
                " latency_ms, request_hash, batch) VALUES ('11111111111111111111111111111111', :t,"
                " 'extraction', 'haiku', 'm', 'replay', 10, 5, 0, 0, 0.0004, 100, 'h', 0)"
            ),
            {"t": "2026-10-01 10:00:05.000000"},
        )


@pytest.fixture
async def legacy(url: str, sync_engine: Engine) -> str:
    """A database at revision 0002 holding one batch, document, claim and LLM call."""
    await run(command.upgrade, url, "0002")
    insert_legacy_rows(sync_engine)
    return url


def columns(engine: Engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def indexes(engine: Engine) -> set[str]:
    found: set[str] = set()
    inspector = inspect(engine)
    for table in SANDBOXED:
        found |= {i["name"] for i in inspector.get_indexes(table) if i["name"]}
    return found


async def test_revision_0002_has_no_sandbox_columns_yet(legacy: str, sync_engine: Engine):
    for table in SANDBOXED:
        assert "sandbox" not in columns(sync_engine, table)
    assert "batch_id" not in columns(sync_engine, "llm_calls")


async def test_upgrading_adds_the_columns_and_indexes_and_matches_the_models(
    legacy: str, sync_engine: Engine
):
    await run(command.upgrade, legacy, "0003")
    for table in SANDBOXED:
        assert "sandbox" in columns(sync_engine, table)
    assert "batch_id" in columns(sync_engine, "llm_calls")
    assert indexes(sync_engine) >= NEW_INDEXES

    await run(command.upgrade, legacy, "head")
    await asyncio.to_thread(command.check, config(legacy))  # raises if the models drift


@pytest.mark.parametrize("top", ["0003", "head"])
async def test_legacy_rows_survive_with_a_null_sandbox(top: str, legacy: str, sync_engine: Engine):
    await run(command.upgrade, legacy, top)

    with sync_engine.connect() as conn:
        for table in SANDBOXED:
            rows = conn.execute(text(f"SELECT sandbox FROM {table}")).all()
            assert rows == [(None,)], table
        assert conn.execute(text("SELECT batch_id FROM llm_calls")).all() == [(None,)]
        assert conn.execute(text("SELECT cost_usd FROM llm_calls")).scalar_one() == pytest.approx(
            0.0004
        )


async def test_legacy_rows_are_visible_to_the_sandbox_less_scope_only(legacy: str):
    await run(command.upgrade, legacy, "head")
    engine = create_async_engine(Settings(database_url=legacy))
    try:
        sessions = create_session_factory(engine)
        repo = Repository(sessions)

        assert (await repo.get_batch("batch-legacy")) is not None  # the default scope: None
        assert await repo.get_batch("batch-legacy", sandbox=None) is not None
        assert await repo.get_document("doc-legacy") is not None
        assert await repo.get_claim("clm-legacy") is not None
        assert [c.id for c in await repo.list_claims()] == ["clm-legacy"]
        assert await repo.batch_sandbox("batch-legacy") is None
        for sandbox in ("visitor-aaaaaaaaaaaa", "visitor-bbbbbbbbbbbb"):
            assert await repo.get_batch("batch-legacy", sandbox=sandbox) is None
            assert await repo.get_claim("clm-legacy", sandbox=sandbox) is None
            assert await repo.list_claims(sandbox=sandbox) == []

        stats = await collect_stats(sessions)
        assert (stats["documents_processed"], stats["claims"], stats["llm_calls"]) == (1, 1, 1)
        other = await collect_stats(sessions, sandbox="visitor-aaaaaaaaaaaa")
        assert (other["documents_processed"], other["claims"], other["llm_calls"]) == (0, 0, 0)
    finally:
        await engine.dispose()


async def test_new_rows_can_be_added_next_to_the_legacy_ones(legacy: str):
    await run(command.upgrade, legacy, "head")
    engine = create_async_engine(Settings(database_url=legacy))
    try:
        repo = Repository(create_session_factory(engine))
        batch_id, _ = await repo.create_batch(
            "P001", [NewFile("b.png", "b" * 64, "k/b.png")], sandbox="visitor-aaaaaaaaaaaa"
        )
        assert await repo.batch_sandbox(batch_id) == "visitor-aaaaaaaaaaaa"
        assert await repo.batch_sandbox("batch-legacy") is None
    finally:
        await engine.dispose()


@pytest.mark.parametrize("top", ["0003", "head"])
async def test_downgrade_to_0002_drops_indexes_before_columns_and_keeps_the_data(
    top: str, legacy: str, sync_engine: Engine
):
    await run(command.upgrade, legacy, top)

    await run(command.downgrade, legacy, "0002")  # SQLite refuses to drop an indexed column

    for table in SANDBOXED:
        assert "sandbox" not in columns(sync_engine, table)
    assert "batch_id" not in columns(sync_engine, "llm_calls")
    assert not NEW_INDEXES & indexes(sync_engine)
    with sync_engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM claims")).scalar_one() == "clm-legacy"
        assert conn.execute(text("SELECT id FROM documents")).scalar_one() == "doc-legacy"
        assert conn.execute(text("SELECT id FROM batches")).scalar_one() == "batch-legacy"
        assert conn.execute(text("SELECT count(*) FROM llm_calls")).scalar_one() == 1


async def test_a_downgrade_and_a_second_upgrade_round_trip(legacy: str, sync_engine: Engine):
    await run(command.upgrade, legacy, "head")
    await run(command.downgrade, legacy, "0002")
    await run(command.upgrade, legacy, "head")
    await asyncio.to_thread(command.check, config(legacy))

    assert indexes(sync_engine) >= NEW_INDEXES
    with sync_engine.connect() as conn:
        assert conn.execute(text("SELECT sandbox FROM claims")).all() == [(None,)]
