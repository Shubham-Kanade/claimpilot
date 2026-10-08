from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import pytest
import uvicorn
from starlette.applications import Starlette

from claimpilot_mcp_corp.directory import Directory
from claimpilot_mcp_corp.policy import PolicyProvider

SERVICE_ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = SERVICE_ROOT / "seed"
FIXTURE_POLICY = Path(__file__).resolve().parent / "fixtures" / "policy.yaml"


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


@pytest.fixture(scope="session")
def directory() -> Directory:
    """The committed seed, loaded once."""
    return Directory.load(SEED_DIR)


@pytest.fixture
def policy() -> PolicyProvider:
    return PolicyProvider(FIXTURE_POLICY)


@pytest.fixture
def serve() -> Callable[[Starlette], AbstractAsyncContextManager[str]]:
    return serve_app
