"""``python -m claimpilot_mcp --transport http`` as a real process, the way Compose runs it."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import httpx
from mcp import Client
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from tests import factories as f
from tests.conftest import free_port, serve_app


def fake_api() -> Starlette:
    async def ready(_: object) -> JSONResponse:
        return JSONResponse({"status": "ready"})

    async def claims(_: object) -> JSONResponse:
        return JSONResponse([f.claim()])

    return Starlette(routes=[Route("/readyz", ready), Route("/v1/claims", claims)])


@asynccontextmanager
async def http_process(api_url: str, port: int) -> AsyncIterator[str]:
    """Start the server process, wait until it answers, stop it afterwards."""
    env = {
        **os.environ,
        "CLAIMPILOT_API_URL": api_url,
        "CLAIMPILOT_PERSONA": f.PERSONA,
        "MCP_PORT": str(port),
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "claimpilot_mcp", "--transport", "http", env=env
    )
    base = f"http://127.0.0.1:{port}"
    try:
        with anyio.fail_after(60):
            async with httpx.AsyncClient() as http:
                while True:
                    try:
                        if (await http.get(f"{base}/healthz")).status_code == 200:
                            break
                    except httpx.TransportError:
                        await anyio.sleep(0.2)
        yield base
    finally:
        process.terminate()
        with anyio.fail_after(30):
            await process.wait()


async def test_the_http_transport_serves_mcp_and_health_from_the_command_line():
    async with (
        serve_app(fake_api()) as api_url,
        http_process(api_url, free_port()) as base,
        httpx.AsyncClient() as http,
    ):
        ready = await http.get(f"{base}/readyz")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ok", "checks": {"api": "ok"}}
        async with Client(f"{base}/mcp") as mcp:
            listed = (await mcp.call_tool("list_claims", {})).structured_content
    assert listed is not None
    assert listed["claims"][0]["claim_id"] == f.CLAIM_ID


async def test_readyz_turns_red_when_the_api_goes_away():
    async with serve_app(fake_api()) as api_url:
        dead_url = api_url
    async with http_process(dead_url, free_port()) as base, httpx.AsyncClient() as http:
        health = await http.get(f"{base}/healthz")
        ready = await http.get(f"{base}/readyz")
    assert health.status_code == 200
    assert ready.status_code == 503
    assert ready.json()["checks"] == {"api": "error"}
