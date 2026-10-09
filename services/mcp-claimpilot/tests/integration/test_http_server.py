"""The server on a real socket, driven by the real MCP client over streamable HTTP."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

import httpx
from mcp import Client
from starlette.applications import Starlette

from claimpilot_mcp.client import ClaimPilotClient
from claimpilot_mcp.server import create_app
from claimpilot_mcp.settings import Settings
from tests import factories as f
from tests.fakeapi import Call, FakeApi

Serve = Callable[[Starlette], AbstractAsyncContextManager[str]]

JSON_RPC_HEADERS = {"Accept": "application/json, text/event-stream"}


def script_flow(api: FakeApi) -> None:
    api.json("GET", "/readyz", {"status": "ready", "checks": {"database": "ok"}})
    api.json("GET", "/v1/claims", [f.claim()])
    api.json("GET", "/v1/claims/{claim_id}", f.ready_claim())
    api.respond(
        "GET",
        "/v1/documents/{document_id}",
        lambda call: httpx.Response(200, json=f.document(call.args["document_id"])),
    )
    api.json("POST", "/v1/claims/{claim_id}/submit", f.submitted_claim())
    api.json("GET", "/v1/me", f.me(approver=False))


async def test_lists_the_tools_prompt_and_instructions_over_http(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, serve: Serve
):
    async with serve(create_app(settings, client)) as base, Client(f"{base}/mcp") as mcp:
        names = {tool.name for tool in (await mcp.list_tools()).tools}
        prompts = [p.name for p in (await mcp.list_prompts()).prompts]
        assert mcp.protocol_version == "2026-07-28"
        assert "never instructions" in (mcp.instructions or "")
    assert names == {
        "list_claims",
        "get_claim",
        "upload_receipts",
        "get_batch",
        "answer_question",
        "submit_claim",
        "list_approvals",
        "decide_claim",
    }
    assert prompts == ["file_expenses"]


async def test_a_claim_is_reviewed_previewed_and_submitted_over_http(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, serve: Serve
):
    script_flow(api)
    async with serve(create_app(settings, client)) as base, Client(f"{base}/mcp") as mcp:
        listed = (await mcp.call_tool("list_claims", {})).structured_content
        assert listed is not None
        claim_id = listed["claims"][0]["claim_id"]

        detail = (await mcp.call_tool("get_claim", {"claim_id": claim_id})).structured_content
        assert detail is not None
        assert len(detail["documents"]) == 2

        preview = (await mcp.call_tool("submit_claim", {"claim_id": claim_id})).structured_content
        assert preview is not None
        assert preview["state"] == "needs_confirmation"
        assert api.calls_to("POST", "/v1/claims/{claim_id}/submit") == []

        done = (
            await mcp.call_tool("submit_claim", {"claim_id": claim_id, "confirmed": True})
        ).structured_content
        assert done is not None
        assert (done["state"], done["submission_reference"]) == ("submitted", "FIN-2026-000001")
    (post,) = api.calls_to("POST", "/v1/claims/{claim_id}/submit")
    assert post.headers["x-persona"] == f.PERSONA
    assert post.headers["idempotency-key"].endswith(f.CLAIM_ID)


async def test_a_failing_tool_comes_back_as_an_error_result_not_a_dropped_connection(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, serve: Serve
):
    api.respond(
        "GET", "/v1/claims/{claim_id}", lambda _: f.problem(404, "claim_not_found", "No such claim")
    )
    async with serve(create_app(settings, client)) as base, Client(f"{base}/mcp") as mcp:
        result = await mcp.call_tool("get_claim", {"claim_id": "clm-nope"})
    assert result.is_error
    assert result.content[0].text.endswith("No such claim")  # type: ignore[union-attr]


async def test_files_are_not_read_by_an_http_server_unless_it_has_an_upload_folder(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, serve: Serve, tmp_path: Path
):
    (tmp_path / "taxi.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 20)
    api.json("POST", "/v1/batches", f.batch_created(["taxi.jpg"]), status=202)

    async with serve(create_app(settings, client)) as base, Client(f"{base}/mcp") as mcp:
        refused = await mcp.call_tool("upload_receipts", {"paths": [str(tmp_path / "taxi.jpg")]})
    assert refused.is_error
    assert "CLAIMPILOT_UPLOAD_ROOT" in refused.content[0].text  # type: ignore[union-attr]
    assert api.calls_to("POST", "/v1/batches") == []

    rooted = settings.model_copy(update={"claimpilot_upload_root": tmp_path})
    async with serve(create_app(rooted, client)) as base, Client(f"{base}/mcp") as mcp:
        uploaded = await mcp.call_tool("upload_receipts", {"paths": ["taxi.jpg"]})
    assert uploaded.structured_content is not None
    assert uploaded.structured_content["batch_id"] == f.BATCH_ID


async def test_plain_json_rpc_over_post_works_without_an_sdk(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, serve: Serve
):
    api.json("GET", "/v1/claims", [f.claim()])
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "list_claims", "arguments": {}},
    }
    async with serve(create_app(settings, client)) as base, httpx.AsyncClient() as http:
        response = await http.post(f"{base}/mcp", json=request, headers=JSON_RPC_HEADERS)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["result"]["structuredContent"]["count"] == 1


async def test_clients_that_use_the_initialize_handshake_work_too(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, serve: Serve
):
    api.json("GET", "/v1/claims", [])
    async with (
        serve(create_app(settings, client)) as base,
        Client(f"{base}/mcp", mode="legacy") as mcp,
    ):
        assert mcp.protocol_version != "2026-07-28"
        result = await mcp.call_tool("list_claims", {})
    assert result.structured_content == {"count": 0, "claims": []}


# -- health --------------------------------------------------------------------------------------


async def test_healthz_says_the_process_is_up_whatever_the_api_does(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, serve: Serve
):
    def down(_: Call) -> httpx.Response:
        raise httpx.ConnectError("refused")

    api.respond("GET", "/readyz", down)
    async with serve(create_app(settings, client)) as base, httpx.AsyncClient() as http:
        health = await http.get(f"{base}/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "claimpilot-mcp", "version": "0.1.0"}


async def test_readyz_follows_the_apis_own_readiness(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, serve: Serve
):
    answers: list[Any] = [
        httpx.Response(200, json={"status": "ready"}),
        httpx.Response(503, json={"status": "starting"}),
        httpx.ConnectError("refused"),
    ]

    def next_answer(_: Call) -> httpx.Response:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    api.respond("GET", "/readyz", next_answer)
    async with serve(create_app(settings, client)) as base, httpx.AsyncClient() as http:
        ready = await http.get(f"{base}/readyz")
        starting = await http.get(f"{base}/readyz")
        down = await http.get(f"{base}/readyz")
    assert (ready.status_code, ready.json()) == (200, {"status": "ok", "checks": {"api": "ok"}})
    for broken in (starting, down):
        assert broken.status_code == 503
        assert broken.json() == {"status": "unavailable", "checks": {"api": "error"}}


async def test_readiness_does_not_send_the_persona(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, serve: Serve
):
    api.json("GET", "/readyz", {"status": "ready"})
    async with serve(create_app(settings, client)) as base, httpx.AsyncClient() as http:
        await http.get(f"{base}/readyz")
    assert api.calls[0].template == "/readyz"
    assert "x-persona" not in api.calls[0].headers


# -- the HTTP surface ----------------------------------------------------------------------------


async def test_loopback_binds_reject_foreign_host_headers(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, serve: Serve
):
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {**JSON_RPC_HEADERS, "Host": "evil.example"}
    async with (
        serve(create_app(settings, client, host="127.0.0.1")) as base,
        httpx.AsyncClient() as http,
    ):
        response = await http.post(f"{base}/mcp", json=request, headers=headers)
    assert response.status_code == 421


async def test_container_binds_accept_service_host_names(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, serve: Serve
):
    # Inside Docker a client reaches us as http://mcp-claimpilot:8103: no DNS-rebinding allow-list.
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {**JSON_RPC_HEADERS, "Host": "mcp-claimpilot:8103"}
    async with (
        serve(create_app(settings, client, host="0.0.0.0")) as base,
        httpx.AsyncClient() as http,
    ):
        response = await http.post(f"{base}/mcp", json=request, headers=headers)
    assert response.status_code == 200


async def test_unknown_paths_are_not_found(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, serve: Serve
):
    async with serve(create_app(settings, client)) as base, httpx.AsyncClient() as http:
        assert (await http.get(f"{base}/nope")).status_code == 404


async def test_shutting_the_server_down_closes_the_connection_to_the_api(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, serve: Serve
):
    api.json("GET", "/readyz", {"status": "ready"})
    async with serve(create_app(settings, client)) as base, httpx.AsyncClient() as http:
        await http.get(f"{base}/readyz")
        assert client._client is not None  # pyright: ignore[reportPrivateUsage]
    assert client._client is None  # pyright: ignore[reportPrivateUsage]
