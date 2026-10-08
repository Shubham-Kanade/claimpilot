"""The MCP tool surface: schemas the model sees, and the tools driven through the SDK client."""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, Tool

from claimpilot_mcp_finance.server import FinanceTools, create_server
from claimpilot_mcp_finance.store import FinanceStore

TOOL_NAMES = {"submit_claim", "get_claim_status", "list_claims", "start_review", "decide_claim"}

SUBMIT_ARGS: dict[str, Any] = {
    "claim_id": "clm-001",
    "employee_id": "P001",
    "title": "Pune trip 12-14 Aug",
    "total": 4500.0,
    "currency": "INR",
    "document_ids": ["doc-a", "doc-b"],
    "idempotency_key": "key-1",
}


class Caller:
    """Opens a fresh in-process session per call (as production does: no long-lived sessions)."""

    def __init__(self, server: MCPServer) -> None:
        self.server = server

    async def __call__(self, name: str, **arguments: Any) -> CallToolResult:
        async with Client(self.server) as client:
            return await client.call_tool(name, arguments)

    async def tools(self) -> dict[str, Tool]:
        async with Client(self.server) as client:
            return {tool.name: tool for tool in (await client.list_tools()).tools}


@pytest.fixture
def call(store: FinanceStore) -> Caller:
    return Caller(create_server(store))


def error_text(result: CallToolResult) -> str:
    assert result.is_error
    return " ".join(block.text for block in result.content if block.type == "text")


# -- the schema the model sees ------------------------------------------------------------------


@pytest.fixture
async def tools(call: Caller) -> dict[str, Tool]:
    return await call.tools()


async def test_the_expected_tools_are_exposed(tools: dict[str, Tool]):
    assert set(tools) == TOOL_NAMES


async def test_every_tool_is_documented_for_the_model(tools: dict[str, Tool]):
    for tool in tools.values():
        assert tool.description and len(tool.description) > 20, tool.name
        assert tool.title, tool.name
        assert tool.output_schema, tool.name
        properties = tool.input_schema.get("properties", {})
        assert properties, tool.name
        for name, schema in properties.items():
            assert schema.get("description"), f"{tool.name}.{name} has no description"


async def test_required_and_optional_parameters(tools: dict[str, Tool]):
    required = {name: set(tool.input_schema.get("required", [])) for name, tool in tools.items()}
    assert required["submit_claim"] == set(SUBMIT_ARGS)
    assert required["get_claim_status"] == {"reference"}
    assert required["list_claims"] == set()
    assert required["start_review"] == {"reference", "reviewer_id"}
    assert required["decide_claim"] == {"reference", "decision", "approver_id"}


async def test_decision_and_status_are_closed_enums(tools: dict[str, Tool]):
    decision = tools["decide_claim"].input_schema["properties"]["decision"]
    assert decision["enum"] == ["approved", "rejected"]
    status = tools["list_claims"].input_schema["properties"]["status"]
    options = [o for o in status["anyOf"] if "enum" in o]
    assert options[0]["enum"] == ["received", "under_review", "approved", "rejected"]


async def test_tool_annotations_tell_readers_from_writers(tools: dict[str, Tool]):
    def hints(name: str):
        annotations = tools[name].annotations
        assert annotations is not None
        return annotations

    assert hints("get_claim_status").read_only_hint is True
    assert hints("list_claims").read_only_hint is True
    for name in ("submit_claim", "start_review", "decide_claim"):
        assert hints(name).read_only_hint is False
    assert hints("submit_claim").idempotent_hint is True
    assert hints("decide_claim").idempotent_hint is False
    assert all(hints(name).open_world_hint is False for name in tools)


async def test_server_identifies_itself(call: Caller):
    async with Client(call.server) as client:
        assert client.server_info is not None
        assert client.server_info.name == "claimpilot-mcp-finance"


# -- the tools through the SDK client -----------------------------------------------------------


async def test_submit_claim_returns_a_receipt(call: Caller):
    result = await call("submit_claim", **SUBMIT_ARGS)
    assert not result.is_error
    receipt = result.structured_content
    assert receipt["reference"] == "FIN-2026-000001"
    assert receipt["status"] == "received"
    assert receipt["duplicate"] is False
    assert receipt["received_at"].startswith("2026-10-08T09:00:00")


async def test_submit_claim_is_idempotent(call: Caller):
    first = (await call("submit_claim", **SUBMIT_ARGS)).structured_content
    again = (await call("submit_claim", **SUBMIT_ARGS)).structured_content
    assert again["reference"] == first["reference"]
    assert again["duplicate"] is True
    assert again["received_at"] == first["received_at"]


async def test_reusing_a_key_for_another_claim_is_a_readable_error(call: Caller):
    await call("submit_claim", **SUBMIT_ARGS)
    result = await call("submit_claim", **{**SUBMIT_ARGS, "total": 9.0})
    message = error_text(result)
    assert "idempotency_key 'key-1'" in message
    assert "FIN-2026-000001" in message


async def test_invalid_arguments_are_reported_to_the_model(call: Caller):
    result = await call("submit_claim", **{**SUBMIT_ARGS, "total": -1})
    assert "greater than zero" in error_text(result)
    # a wrong type never reaches the store: the SDK rejects it against the schema
    result = await call("submit_claim", **{**SUBMIT_ARGS, "document_ids": "doc-a"})
    assert result.is_error


async def test_get_claim_status_returns_the_history(call: Caller):
    await call("submit_claim", **SUBMIT_ARGS)
    result = await call("get_claim_status", reference="fin-2026-000001")
    claim = result.structured_content
    assert claim["reference"] == "FIN-2026-000001"
    assert claim["status"] == "received"
    assert claim["total"] == 4500.0
    assert claim["document_ids"] == ["doc-a", "doc-b"]
    assert [event["status"] for event in claim["history"]] == ["received"]


async def test_unknown_reference_is_a_readable_error(call: Caller):
    result = await call("get_claim_status", reference="FIN-2026-000777")
    assert "no claim with reference 'FIN-2026-000777'" in error_text(result)


async def test_list_claims_filters(call: Caller):
    await call("submit_claim", **SUBMIT_ARGS)
    await call("submit_claim", **{**SUBMIT_ARGS, "idempotency_key": "k2", "employee_id": "P2"})
    await call("decide_claim", reference="FIN-2026-000002", decision="approved", approver_id="A")

    everything = (await call("list_claims")).structured_content["result"]
    assert [c["reference"] for c in everything] == ["FIN-2026-000001", "FIN-2026-000002"]
    assert "history" not in everything[0]

    approved = await call("list_claims", status="approved")
    assert [c["reference"] for c in approved.structured_content["result"]] == ["FIN-2026-000002"]
    mine = await call("list_claims", employee_id="P001")
    assert [c["reference"] for c in mine.structured_content["result"]] == ["FIN-2026-000001"]
    assert (await call("list_claims", status="rejected")).structured_content == {"result": []}
    assert (await call("list_claims", status="bogus")).is_error


async def test_the_approver_flow(call: Caller):
    await call("submit_claim", **SUBMIT_ARGS)
    review = await call("start_review", reference="FIN-2026-000001", reviewer_id="DEMO-RAVI")
    assert review.structured_content["status"] == "under_review"

    decided = await call(
        "decide_claim",
        reference="FIN-2026-000001",
        decision="rejected",
        approver_id="DEMO-RAVI",
        comment="Hotel invoice is missing",
    )
    claim = decided.structured_content
    assert claim["status"] == "rejected"
    assert [e["status"] for e in claim["history"]] == ["received", "under_review", "rejected"]
    assert claim["history"][-1]["actor"] == "DEMO-RAVI"
    assert claim["history"][-1]["comment"] == "Hotel invoice is missing"


async def test_invalid_transitions_are_readable_errors(call: Caller):
    await call("submit_claim", **SUBMIT_ARGS)
    approve = {"reference": "FIN-2026-000001", "decision": "approved", "approver_id": "DEMO-RAVI"}
    assert not (await call("decide_claim", **approve)).is_error
    again = await call("decide_claim", **approve)
    assert "already approved" in error_text(again)
    review = await call("start_review", reference="FIN-2026-000001", reviewer_id="DEMO-RAVI")
    assert "only a 'received' claim" in error_text(review)


async def test_rejection_without_a_reason_is_an_error(call: Caller):
    await call("submit_claim", **SUBMIT_ARGS)
    result = await call(
        "decide_claim",
        reference="FIN-2026-000001",
        decision="rejected",
        approver_id="DEMO-RAVI",
    )
    assert "comment" in error_text(result)


async def test_a_decision_outside_the_enum_never_reaches_the_store(call: Caller):
    await call("submit_claim", **SUBMIT_ARGS)
    result = await call(
        "decide_claim", reference="FIN-2026-000001", decision="maybe", approver_id="A"
    )
    assert result.is_error
    assert (await call("get_claim_status", reference="FIN-2026-000001")).structured_content[
        "status"
    ] == "received"


# -- calling the tool functions directly --------------------------------------------------------


def test_tool_functions_raise_tool_errors_for_domain_failures(store: FinanceStore):
    tools = FinanceTools(store)
    with pytest.raises(ToolError, match="no claim with reference"):
        tools.get_claim_status("FIN-2026-000001")
    with pytest.raises(ToolError, match="must not be empty"):
        tools.submit_claim(**{**SUBMIT_ARGS, "title": " "})


def test_tool_functions_return_typed_models(store: FinanceStore):
    tools = FinanceTools(store)
    receipt = tools.submit_claim(**SUBMIT_ARGS)
    assert receipt.reference == "FIN-2026-000001"
    assert tools.get_claim_status(receipt.reference).history[0].status == "received"
    assert [c.reference for c in tools.list_claims()] == [receipt.reference]
    assert tools.start_review(receipt.reference, "A").status == "under_review"
    assert tools.decide_claim(receipt.reference, "approved", "A").status == "approved"
