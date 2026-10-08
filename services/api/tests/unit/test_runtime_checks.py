"""Readiness checks per runtime, and the SQLite settings the embedded runtime relies on."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from claimpilot import health
from claimpilot.config import Settings
from claimpilot.db import create_engine


def test_the_distributed_runtime_depends_on_redis_and_postgres():
    checks = health.default_checks(Settings(runtime="distributed"))
    assert set(checks) == {"redis", "postgres"}


def test_the_embedded_runtime_depends_only_on_its_database():
    checks = health.default_checks(Settings(runtime="embedded"))
    assert set(checks) == {"database"}


async def test_the_database_check_passes_on_a_sqlite_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'ready.db').as_posix()}"
    monkeypatch.setattr(health, "get_settings", lambda: Settings(database_url=url))
    await health.check_database()  # raises when the database is unreachable


async def test_the_database_check_fails_when_the_database_is_unreachable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'missing-dir' / 'x.db').as_posix()}"
    monkeypatch.setattr(health, "get_settings", lambda: Settings(database_url=url))
    with pytest.raises(Exception, match="unable to open"):
        await health.check_database()


async def test_readyz_uses_the_checks_of_the_configured_runtime(
    client, monkeypatch: pytest.MonkeyPatch
):
    async def ok() -> None:
        return None

    monkeypatch.setattr(health, "get_settings", lambda: Settings(runtime="embedded"))
    monkeypatch.setattr(health, "check_database", ok)
    resp = await client.get("/readyz")
    assert resp.json() == {"status": "ready", "checks": {"database": "ok"}}


async def test_sqlite_uses_write_ahead_logging_and_waits_for_locks(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'wal.db').as_posix()}"
    engine = create_engine(Settings(database_url=url))
    try:
        async with engine.connect() as conn:
            mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
            wait = (await conn.execute(text("PRAGMA busy_timeout"))).scalar_one()
    finally:
        await engine.dispose()
    assert mode == "wal" and wait == 15_000
