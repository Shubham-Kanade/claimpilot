from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette

from claimpilot_mcp.client import ClaimPilotClient
from claimpilot_mcp.server import create_server
from claimpilot_mcp.settings import Settings
from tests.factories import PERSONA
from tests.fakeapi import FakeApi

ENV_VARS = (
    "CLAIMPILOT_API_URL",
    "CLAIMPILOT_PERSONA",
    "CLAIMPILOT_TIMEOUT_S",
    "CLAIMPILOT_UPLOAD_TIMEOUT_S",
    "CLAIMPILOT_MAX_FILES",
    "CLAIMPILOT_MAX_FILE_MB",
    "CLAIMPILOT_UPLOAD_ROOT",
    "MCP_HOST",
    "MCP_PORT",
    "LOG_LEVEL",
)


class Sleeper:
    """A ``sleep`` that returns at once and remembers how long it was asked to wait."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


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


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings come from the environment: keep the developer's own out of the tests."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings() -> Settings:
    return Settings(claimpilot_api_url="http://api.test", claimpilot_persona=PERSONA)


@pytest.fixture
def api() -> Iterator[FakeApi]:
    fake = FakeApi()
    yield fake
    assert not fake.problems, fake.problems  # no contract violation, no unscripted call


@pytest.fixture
def client(api: FakeApi, settings: Settings) -> ClaimPilotClient:
    return ClaimPilotClient.from_settings(settings, transport=api.transport)


@pytest.fixture
def sleeper() -> Sleeper:
    return Sleeper()


@pytest.fixture
def server(settings: Settings, client: ClaimPilotClient, sleeper: Sleeper) -> MCPServer:
    return create_server(settings, client, allow_any_path=True, sleep=sleeper)


@pytest.fixture
def serve() -> Callable[[Starlette], AbstractAsyncContextManager[str]]:
    return serve_app
