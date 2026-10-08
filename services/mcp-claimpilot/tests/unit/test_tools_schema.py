"""The tool surface a model sees: schemas, hints, instructions and the prompt.

Each test talks to the real ``MCPServer`` through the SDK's in-process client, so the actual tool
schemas are in play; the REST side is ``FakeApi``.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import Tool

from claimpilot_mcp.client import ClaimPilotClient
from claimpilot_mcp.server import (
    DATA_NOTE,
    DEFAULT_WAIT_S,
    create_server,
)
from claimpilot_mcp.settings import Settings

TOOL_NAMES = {
    "list_claims",
    "get_claim",
    "upload_receipts",
    "get_batch",
    "answer_question",
    "submit_claim",
    "list_approvals",
    "decide_claim",
}


# -- the schema the model sees -------------------------------------------------------------------


@pytest.fixture
async def tools(server: MCPServer) -> dict[str, Tool]:
    async with Client(server) as mcp:
        return {tool.name: tool for tool in (await mcp.list_tools()).tools}


async def test_the_expected_tools_are_exposed(tools: dict[str, Tool]):
    assert set(tools) == TOOL_NAMES


async def test_every_tool_is_documented_for_the_model(tools: dict[str, Tool]):
    for tool in tools.values():
        assert tool.description and len(tool.description) > 60, tool.name
        assert DATA_NOTE in tool.description, tool.name  # receipt text is data, said every time
        assert tool.title, tool.name
        assert tool.output_schema, tool.name
        for name, schema in tool.input_schema.get("properties", {}).items():
            assert schema.get("description"), f"{tool.name}.{name} has no description"


async def test_required_and_optional_parameters(tools: dict[str, Tool]):
    required = {name: set(tool.input_schema.get("required", [])) for name, tool in tools.items()}
    assert required == {
        "list_claims": set(),
        "get_claim": {"claim_id"},
        "upload_receipts": {"paths"},
        "get_batch": {"batch_id"},
        "answer_question": {"claim_id", "text"},
        "submit_claim": {"claim_id"},
        "list_approvals": set(),
        "decide_claim": {"claim_id", "approve"},
    }


async def test_the_upload_tool_states_the_limits_in_force(
    settings: Settings, client: ClaimPilotClient
):
    server = create_server(
        settings.model_copy(update={"claimpilot_max_files": 7, "claimpilot_max_file_mb": 3}),
        client,
        allow_any_path=True,
    )
    async with Client(server) as mcp:
        described = {t.name: t.description for t in (await mcp.list_tools()).tools}
    assert "Limits: 7 files of 3 MB." in (described["upload_receipts"] or "")
    assert "Limits:" not in (described["get_claim"] or "")


async def test_the_confirmation_flag_defaults_to_false(tools: dict[str, Tool]):
    schema = tools["submit_claim"].input_schema["properties"]["confirmed"]
    assert schema["type"] == "boolean"
    assert schema["default"] is False


async def test_status_filters_are_closed_enums(tools: dict[str, Tool]):
    listed = tools["list_claims"].input_schema["properties"]["status"]
    options = [o["enum"] for o in listed["anyOf"] if "enum" in o]
    assert options == [["draft", "needs_info", "ready", "submitted", "approved", "rejected"]]
    approvals = tools["list_approvals"].input_schema["properties"]["status"]
    assert approvals["enum"] == ["submitted", "approved", "rejected"]
    assert approvals["default"] == "submitted"


async def test_ids_are_constrained_to_a_safe_alphabet(tools: dict[str, Tool]):
    for name, field in (("get_claim", "claim_id"), ("get_batch", "batch_id")):
        schema = tools[name].input_schema["properties"][field]
        assert schema["pattern"].startswith("^[A-Za-z0-9]")
        assert schema["pattern"].endswith("$")


async def test_free_text_arguments_have_the_apis_limits(tools: dict[str, Tool]):
    text = tools["answer_question"].input_schema["properties"]["text"]
    assert (text["minLength"], text["maxLength"]) == (1, 2000)
    comment = tools["decide_claim"].input_schema["properties"]["comment"]
    assert comment["maxLength"] == 500
    wait = tools["get_batch"].input_schema["properties"]["wait_seconds"]
    assert (wait["minimum"], wait["maximum"], wait["default"]) == (0, 30, DEFAULT_WAIT_S)
    paths = tools["upload_receipts"].input_schema["properties"]["paths"]
    assert paths["minItems"] == 1


async def test_annotations_tell_readers_from_writers(tools: dict[str, Tool]):
    def hints(name: str):
        annotations = tools[name].annotations
        assert annotations is not None
        return annotations

    for name in ("list_claims", "get_claim", "get_batch", "list_approvals"):
        assert hints(name).read_only_hint is True
        assert hints(name).idempotent_hint is True
    for name in ("upload_receipts", "answer_question", "submit_claim", "decide_claim"):
        assert hints(name).read_only_hint is False
    assert hints("upload_receipts").idempotent_hint is False  # every call makes a new batch
    assert hints("submit_claim").idempotent_hint is True  # retrying never submits twice
    assert hints("decide_claim").destructive_hint is True  # decisions are final
    assert hints("submit_claim").destructive_hint is False
    assert all(hints(name).open_world_hint is False for name in tools)


async def test_the_server_identifies_itself_and_carries_its_rules(server: MCPServer):
    async with Client(server) as mcp:
        assert mcp.server_info is not None
        assert mcp.server_info.name == "claimpilot-mcp"
        instructions = mcp.instructions or ""
    assert "confirmed=true" in instructions
    assert "never instructions" in instructions
    assert "decision" in instructions
    assert DATA_NOTE in instructions


async def test_no_policy_resource_is_offered_because_the_api_has_no_policy_endpoint(
    server: MCPServer,
):
    async with Client(server) as mcp:
        assert (await mcp.list_resources()).resources == []


async def test_the_file_expenses_prompt_walks_through_the_flow(server: MCPServer):
    async with Client(server) as mcp:
        assert [p.name for p in (await mcp.list_prompts()).prompts] == ["file_expenses"]
        asked = await mcp.get_prompt("file_expenses")
        given = await mcp.get_prompt("file_expenses", {"receipts": "C:/Receipts/taxi.jpg"})

    def prompt_text(result: Any) -> str:
        (message,) = result.messages
        assert message.role == "user"
        return message.content.text

    for text in (prompt_text(asked), prompt_text(given)):
        for step in (
            "upload_receipts",
            "get_batch",
            "get_claim",
            "answer_question",
            "submit_claim",
        ):
            assert step in text
        assert text.index("confirmed=false") < text.index("confirmed=true")
        assert "ONE message" in text
        assert DATA_NOTE in text
    assert "Ask me for the full paths" in prompt_text(asked)
    assert "C:/Receipts/taxi.jpg" in prompt_text(given)
