"""The Alembic migrations and the ORM models must describe the same schema."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from claimpilot.config import API_ROOT


def config(url: str) -> Config:
    cfg = Config(API_ROOT / "alembic.ini")
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture
def url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / 'm.db').as_posix()}"


async def test_upgrade_creates_every_table_and_matches_the_models(url: str):
    await asyncio.to_thread(command.upgrade, config(url), "head")
    await asyncio.to_thread(command.check, config(url))  # raises if models drift from migrations

    sync = create_engine(url.replace("+aiosqlite", ""))
    tables = set(inspect(sync).get_table_names())
    sync.dispose()
    assert {"llm_calls", "batches", "documents", "claims", "audit_log"} <= tables


async def test_downgrade_to_base_removes_everything(url: str):
    await asyncio.to_thread(command.upgrade, config(url), "head")
    await asyncio.to_thread(command.downgrade, config(url), "base")
    sync = create_engine(url.replace("+aiosqlite", ""))
    tables = set(inspect(sync).get_table_names()) - {"alembic_version"}
    sync.dispose()
    assert tables == set()
