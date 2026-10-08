"""Async engine and session factory built from ``Settings.database_url``."""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from claimpilot.config import Settings

SessionFactory = async_sessionmaker[AsyncSession]


SQLITE_BUSY_TIMEOUT_S = 15


def create_engine(settings: Settings) -> AsyncEngine:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        _tune_sqlite(engine)
    return engine


def _tune_sqlite(engine: AsyncEngine) -> None:
    """The embedded runtime keeps its data in one SQLite file written by the API's own tasks.

    Write-ahead logging lets readers (the SSE stream, the claim list) proceed during a write, and a
    busy timeout turns a brief lock into a short wait instead of an error.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_S * 1000}")
        cursor.close()


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    return async_sessionmaker(engine, expire_on_commit=False)
