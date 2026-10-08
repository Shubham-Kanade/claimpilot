"""The corporate-systems server on a real socket, driven by the real MCP client over HTTP."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import httpx2
import pytest
from mcp import Client, MCPError
from starlette.applications import Starlette

from claimpilot_mcp_corp.directory import Directory
from claimpilot_mcp_corp.policy import PolicyProvider
from claimpilot_mcp_corp.server import POLICY_URI, create_app

Serve = Callable[[Starlette], AbstractAsyncContextManager[str]]

FIXTURE_TEXT = (Path(__file__).resolve().parents[1] / "fixtures" / "policy.yaml").read_text(
    encoding="utf-8"
)


async def test_lists_the_tools_and_the_resource_over_http(
    directory: Directory, policy: PolicyProvider, serve: Serve
):
    async with serve(create_app(directory, policy)) as base, Client(f"{base}/mcp") as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
        resources = [str(r.uri) for r in (await client.list_resources()).resources]
        assert client.protocol_version == "2026-07-28"
    assert tools == {"get_employee", "list_employees", "search_calendar", "get_policy"}
    assert resources == [POLICY_URI]


async def test_every_tool_and_the_resource_work_over_http(
    directory: Directory, policy: PolicyProvider, serve: Serve
):
    async with serve(create_app(directory, policy)) as base, Client(f"{base}/mcp") as client:
        who = await client.call_tool("get_employee", {"employee_id": "EMP44590"})
        assert who.structured_content["name"] == "Abha Balan"
        assert who.structured_content["base_city"] == "Kochi"

        everyone = await client.call_tool("list_employees", {})
        assert len(everyone.structured_content["result"]) == 8

        calendar = await client.call_tool(
            "search_calendar",
            {"employee_id": "P003", "start_date": "2026-09-01", "end_date": "2026-09-30"},
        )
        events = calendar.structured_content["result"]
        assert [(e["kind"], e["date"]) for e in events] == [
            ("travel", "2026-09-07"),
            ("client_dinner", "2026-09-09"),
        ]
        # P003's second client dinner (22 Sep) has no calendar entry on purpose
        assert not any(e["date"] == "2026-09-22" for e in events)

        policy_tool = await client.call_tool("get_policy", {})
        assert policy_tool.structured_content["result"] == FIXTURE_TEXT

        resource = await client.read_resource(POLICY_URI)
        assert resource.contents[0].text == FIXTURE_TEXT  # type: ignore[union-attr]

        missing = await client.call_tool("get_employee", {"employee_id": "P999"})
        assert missing.is_error


async def test_clients_that_use_the_initialize_handshake_work_too(
    directory: Directory, policy: PolicyProvider, serve: Serve
):
    async with (
        serve(create_app(directory, policy)) as base,
        Client(f"{base}/mcp", mode="legacy") as client,
    ):
        assert client.protocol_version != "2026-07-28"
        result = await client.call_tool("get_employee", {"employee_id": "P001"})
        resource = await client.read_resource(POLICY_URI)
    assert result.structured_content["id"] == "P001"
    assert resource.contents[0].text == FIXTURE_TEXT  # type: ignore[union-attr]


async def test_a_missing_policy_fails_clearly_over_http(
    directory: Directory, tmp_path: Path, serve: Serve
):
    app = create_app(directory, PolicyProvider(tmp_path / "missing.yaml"))
    async with serve(app) as base, Client(f"{base}/mcp") as client:
        tool = await client.call_tool("get_policy", {})
        assert tool.is_error
        assert "policy not available" in tool.content[0].text  # type: ignore[union-attr]
        with pytest.raises(MCPError, match="policy not available"):
            await client.read_resource(POLICY_URI)


async def test_health_endpoints(directory: Directory, policy: PolicyProvider, serve: Serve):
    async with serve(create_app(directory, policy)) as base, httpx2.AsyncClient() as http:
        health = await http.get(f"{base}/healthz")
        assert health.status_code == 200
        assert health.json()["service"] == "claimpilot-mcp-corp"
        ready = await http.get(f"{base}/readyz")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ok", "checks": {"directory": "ok", "policy": "ok"}}


async def test_readiness_reports_a_missing_policy_but_liveness_stays_up(
    directory: Directory, tmp_path: Path, serve: Serve
):
    app = create_app(directory, PolicyProvider(tmp_path / "missing.yaml"))
    async with serve(app) as base, httpx2.AsyncClient() as http:
        assert (await http.get(f"{base}/healthz")).status_code == 200
        ready = await http.get(f"{base}/readyz")
    assert ready.status_code == 503
    assert ready.json()["checks"] == {"directory": "ok", "policy": "missing"}


async def test_readiness_reports_an_empty_directory(policy: PolicyProvider, serve: Serve):
    async with serve(create_app(Directory([]), policy)) as base, httpx2.AsyncClient() as http:
        ready = await http.get(f"{base}/readyz")
    assert ready.status_code == 503
    assert ready.json()["checks"]["directory"] == "empty"


async def test_loopback_binds_reject_foreign_host_headers(
    directory: Directory, policy: PolicyProvider, serve: Serve
):
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Accept": "application/json, text/event-stream", "Host": "evil.example"}
    app = create_app(directory, policy, host="127.0.0.1")
    async with serve(app) as base, httpx2.AsyncClient() as http:
        response = await http.post(f"{base}/mcp", json=request, headers=headers)
    assert response.status_code == 421


async def test_container_binds_accept_service_host_names(
    directory: Directory, policy: PolicyProvider, serve: Serve
):
    # Inside Docker the api reaches us as http://mcp-corp:8102: no DNS-rebinding allow-list.
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Accept": "application/json, text/event-stream", "Host": "mcp-corp:8102"}
    app = create_app(directory, policy, host="0.0.0.0")
    async with serve(app) as base, httpx2.AsyncClient() as http:
        response = await http.post(f"{base}/mcp", json=request, headers=headers)
    assert response.status_code == 200
