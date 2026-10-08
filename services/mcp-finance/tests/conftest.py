from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
import uvicorn
from starlette.applications import Starlette

from claimpilot_mcp_finance.store import FinanceStore


class TickingClock:
    """A deterministic clock that advances one minute per reading."""

    def __init__(self, start: datetime | None = None, step: timedelta | None = None) -> None:
        self.now = start or datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
        self.step = step or timedelta(minutes=1)

    def __call__(self) -> datetime:
        reading = self.now
        self.now += self.step
        return reading


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@asynccontextmanager
async def serve_app(app: Starlette, host: str = "127.0.0.1") -> AsyncIterator[str]:
    """Run ``app`` under uvicorn on a free port in this event loop; yield its base URL."""
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            if task.done():
                await task  # surfaces a startup failure
            await asyncio.sleep(0.01)
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        await task


@pytest.fixture
def clock() -> TickingClock:
    return TickingClock()


@pytest.fixture
def store(clock: TickingClock) -> Iterator[FinanceStore]:
    finance = FinanceStore(clock=clock)
    yield finance
    finance.close()


@pytest.fixture
def serve() -> Callable[[Starlette], AbstractAsyncContextManager[str]]:
    return serve_app
