"""The finance server on a real socket, driven by the real MCP client over streamable HTTP."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

import httpx2
from mcp import Client
from starlette.applications import Starlette

from claimpilot_mcp_finance.server import create_app
from claimpilot_mcp_finance.store import FinanceStore

Serve = Callable[[Starlette], AbstractAsyncContextManager[str]]

SUBMIT_ARGS: dict[str, Any] = {
    "claim_id": "clm-001",
    "employee_id": "P001",
    "title": "Pune trip 12-14 Aug",
    "total": 4500.0,
    "currency": "INR",
    "document_ids": ["doc-a", "doc-b"],
    "idempotency_key": "key-1",
}


async def test_lists_the_tools_over_http(store: FinanceStore, serve: Serve):
    async with serve(create_app(store)) as base, Client(f"{base}/mcp") as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert client.protocol_version == "2026-07-28"
    assert names == {
        "submit_claim",
        "get_claim_status",
        "list_claims",
        "start_review",
        "decide_claim",
    }


async def test_a_claim_goes_from_submission_to_decision(store: FinanceStore, serve: Serve):
    async with serve(create_app(store)) as base, Client(f"{base}/mcp") as client:
        receipt = (await client.call_tool("submit_claim", SUBMIT_ARGS)).structured_content
        assert receipt["reference"] == "FIN-2026-000001"

        replay = (await client.call_tool("submit_claim", SUBMIT_ARGS)).structured_content
        assert replay["duplicate"] is True
        assert replay["reference"] == receipt["reference"]

        conflict = await client.call_tool("submit_claim", {**SUBMIT_ARGS, "total": 1.0})
        assert conflict.is_error

        status = await client.call_tool("get_claim_status", {"reference": receipt["reference"]})
        assert status.structured_content["status"] == "received"

        queue = await client.call_tool("list_claims", {"status": "received"})
        assert [c["reference"] for c in queue.structured_content["result"]] == [
            receipt["reference"]
        ]

        decided = await client.call_tool(
            "decide_claim",
            {
                "reference": receipt["reference"],
                "decision": "approved",
                "approver_id": "DEMO-RAVI",
            },
        )
        assert decided.structured_content["status"] == "approved"

        final = await client.call_tool("get_claim_status", {"reference": receipt["reference"]})
        history = final.structured_content["history"]
        assert [event["status"] for event in history] == [
            "received",
            "under_review",
            "approved",
        ]

        again = await client.call_tool(
            "decide_claim",
            {
                "reference": receipt["reference"],
                "decision": "rejected",
                "approver_id": "DEMO-RAVI",
                "comment": "second thoughts",
            },
        )
        assert again.is_error
        assert "already approved" in again.content[0].text  # type: ignore[union-attr]


async def test_clients_that_use_the_initialize_handshake_work_too(
    store: FinanceStore, serve: Serve
):
    async with serve(create_app(store)) as base, Client(f"{base}/mcp", mode="legacy") as client:
        assert client.protocol_version != "2026-07-28"
        result = await client.call_tool("submit_claim", SUBMIT_ARGS)
    assert result.structured_content["reference"] == "FIN-2026-000001"


async def test_plain_json_rpc_over_post_works_without_an_sdk(store: FinanceStore, serve: Serve):
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "submit_claim", "arguments": SUBMIT_ARGS},
    }
    headers = {"Accept": "application/json, text/event-stream"}
    async with serve(create_app(store)) as base, httpx2.AsyncClient() as http:
        response = await http.post(f"{base}/mcp", json=request, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["result"]["structuredContent"]["reference"] == "FIN-2026-000001"


async def test_health_endpoints(store: FinanceStore, serve: Serve):
    async with serve(create_app(store)) as base, httpx2.AsyncClient() as http:
        health = await http.get(f"{base}/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["service"] == "claimpilot-mcp-finance"

        ready = await http.get(f"{base}/readyz")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ok", "checks": {"database": "ok"}}

        store.close()  # the database goes away: still alive, no longer ready
        assert (await http.get(f"{base}/healthz")).status_code == 200
        broken = await http.get(f"{base}/readyz")
        assert broken.status_code == 503
        assert broken.json()["checks"] == {"database": "error"}


async def test_loopback_binds_reject_foreign_host_headers(store: FinanceStore, serve: Serve):
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Accept": "application/json, text/event-stream", "Host": "evil.example"}
    async with serve(create_app(store, host="127.0.0.1")) as base, httpx2.AsyncClient() as http:
        response = await http.post(f"{base}/mcp", json=request, headers=headers)
    assert response.status_code == 421


async def test_container_binds_accept_service_host_names(store: FinanceStore, serve: Serve):
    # Inside Docker the api reaches us as http://mcp-finance:8101: no DNS-rebinding allow-list.
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Accept": "application/json, text/event-stream", "Host": "mcp-finance:8101"}
    async with serve(create_app(store, host="0.0.0.0")) as base, httpx2.AsyncClient() as http:
        response = await http.post(f"{base}/mcp", json=request, headers=headers)
    assert response.status_code == 200


async def test_unknown_paths_are_not_found(store: FinanceStore, serve: Serve):
    async with serve(create_app(store)) as base, httpx2.AsyncClient() as http:
        assert (await http.get(f"{base}/nope")).status_code == 404


async def test_claims_survive_a_server_restart(tmp_path: Path, serve: Serve):
    db = tmp_path / "finance.db"

    first = FinanceStore(db)
    async with serve(create_app(first)) as base, Client(f"{base}/mcp") as client:
        receipt = (await client.call_tool("submit_claim", SUBMIT_ARGS)).structured_content
    first.close()

    second = FinanceStore(db)
    async with serve(create_app(second)) as base, Client(f"{base}/mcp") as client:
        status = await client.call_tool("get_claim_status", {"reference": receipt["reference"]})
        replay = await client.call_tool("submit_claim", SUBMIT_ARGS)
    second.close()

    assert status.structured_content["title"] == "Pune trip 12-14 Aug"
    assert replay.structured_content["duplicate"] is True
    assert replay.structured_content["reference"] == receipt["reference"]
