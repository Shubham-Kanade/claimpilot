"""The real process over stdio: ``python -m claimpilot_mcp`` as Claude Desktop launches it.

The server runs as a subprocess and talks to the real MCP client over its stdin and stdout. Its API
is a small Starlette app on a real socket, so a tool call travels MCP over stdio, then HTTP, and
back, the way it does for a person.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tests import factories as f
from tests.conftest import serve_app

PERSONA = "TESTER-7"


def fake_api(seen: list[dict[str, Any]]) -> Starlette:
    """The few endpoints the walk below needs, recording the persona each one received."""

    def route(path: str, body: Any):
        async def handler(request: Request) -> JSONResponse:
            seen.append({"path": request.url.path, "persona": request.headers.get("x-persona")})
            return JSONResponse(body)

        return Route(path, handler)

    async def document(request: Request) -> JSONResponse:
        return JSONResponse(f.document(request.path_params["document_id"]))

    return Starlette(
        routes=[
            route("/readyz", {"status": "ready"}),
            route("/v1/me", f.me(approver=False)),
            route("/v1/claims", [f.claim()]),
            route("/v1/claims/{claim_id}", f.ready_claim()),
            Route("/v1/documents/{document_id}", document),
        ]
    )


@asynccontextmanager
async def stdio_session(api_url: str, **env: str) -> AsyncIterator[Client]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "claimpilot_mcp"],
        env={"CLAIMPILOT_API_URL": api_url, "CLAIMPILOT_PERSONA": PERSONA, **env},
    )
    with anyio.fail_after(60):
        async with Client(params) as mcp:
            yield mcp


async def test_a_person_can_list_and_review_claims_through_the_real_process():
    seen: list[dict[str, Any]] = []
    async with serve_app(fake_api(seen)) as api_url, stdio_session(api_url) as mcp:
        tools = {tool.name for tool in (await mcp.list_tools()).tools}
        listed = (await mcp.call_tool("list_claims", {})).structured_content
        detail = (await mcp.call_tool("get_claim", {"claim_id": f.CLAIM_ID})).structured_content
        preview = (await mcp.call_tool("submit_claim", {"claim_id": f.CLAIM_ID})).structured_content
        prompt = await mcp.get_prompt("file_expenses")

    assert "decide_claim" in tools
    assert listed is not None and listed["claims"][0]["claim_id"] == f.CLAIM_ID
    assert detail is not None and detail["documents"][0]["merchant"] == "Chai Point Express Cabs"
    assert preview is not None and preview["state"] == "needs_confirmation"
    assert "upload_receipts" in prompt.messages[0].content.text  # type: ignore[union-attr]
    # every call that takes a persona carried the configured one; readiness carried none
    assert {s["persona"] for s in seen if s["path"].startswith("/v1")} == {PERSONA}


async def test_the_process_reports_an_unreachable_api_as_a_tool_error_and_stays_up():
    with anyio.fail_after(60):
        free = serve_app(fake_api([]))
        async with free as api_url:
            dead_url = api_url  # the app below stops, so this address will refuse connections
    async with stdio_session(dead_url) as mcp:
        first = await mcp.call_tool("list_claims", {})
        second = await mcp.call_tool("list_claims", {})
    for result in (first, second):
        assert result.is_error
        assert "Cannot reach the ClaimPilot API" in result.content[0].text  # type: ignore[union-attr]


async def test_files_on_this_machine_can_be_uploaded_over_stdio(tmp_path: Any):
    posted: list[int] = []

    async def batches(request: Request) -> JSONResponse:
        posted.append(len(await request.body()))
        return JSONResponse(f.batch_created(["taxi.jpg"]), status_code=202)

    api = Starlette(routes=[Route("/v1/batches", batches, methods=["POST"])])
    (tmp_path / "taxi.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 100)
    async with serve_app(api) as api_url, stdio_session(api_url) as mcp:
        result = await mcp.call_tool("upload_receipts", {"paths": [str(tmp_path / "taxi.jpg")]})
    assert result.structured_content is not None
    assert result.structured_content["batch_id"] == f.BATCH_ID
    assert posted and posted[0] > 100  # the file really went over the wire


def test_help_and_version_work_as_a_command_line_tool():
    help_ = subprocess.run(
        [sys.executable, "-m", "claimpilot_mcp", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    version = subprocess.run(
        [sys.executable, "-m", "claimpilot_mcp", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert help_.returncode == 0
    assert "--transport {stdio,http}" in help_.stdout
    assert version.stdout.strip() == "claimpilot-mcp 0.1.0"


def test_a_bad_configuration_stops_the_process_before_it_speaks_on_stdout():
    result = subprocess.run(
        [sys.executable, "-m", "claimpilot_mcp"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "CLAIMPILOT_PERSONA": "NOT A VALID PERSONA"},
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "CLAIMPILOT_PERSONA" in result.stderr
    assert "NOT A VALID" not in result.stderr


def test_the_process_exits_when_the_client_closes_stdin():
    process = subprocess.Popen(
        [sys.executable, "-m", "claimpilot_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    process.stdin.close()
    assert process.wait(timeout=30) == 0
