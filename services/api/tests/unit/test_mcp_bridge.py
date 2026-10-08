"""The MCP -> Claude bridge: strict tool definitions and tool_use execution."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from anthropic.types import ToolUseBlock

from claimpilot.mcp import (
    EMPLOYEE_AGENT_TOOLS,
    FakeMcpClient,
    McpBridge,
    McpProtocolError,
    McpToolError,
    McpToolSpec,
    McpUnavailableError,
    claude_tool,
    tools_for_claude,
)

# What the finance / corp servers (pydantic via the MCP SDK) publish, trimmed to what matters.
SUBMIT_SCHEMA: dict[str, Any] = {
    "properties": {
        "claim_id": {"description": "Claim id.", "title": "Claim Id", "type": "string"},
        "title": {"description": "Short claim title.", "title": "Title", "type": "string"},
        "total": {"description": "Claim total.", "title": "Total", "type": "number"},
        "document_ids": {
            "description": "Receipt documents.",
            "items": {"type": "string"},
            "title": "Document Ids",
            "type": "array",
        },
    },
    "required": ["claim_id", "title", "total", "document_ids"],
    "title": "submit_claimArguments",
    "type": "object",
}
LIST_SCHEMA: dict[str, Any] = {
    "properties": {
        "status": {
            "anyOf": [
                {"enum": ["received", "under_review", "approved", "rejected"], "type": "string"},
                {"type": "null"},
            ],
            "default": None,
            "description": "Only claims in this status.",
            "title": "Status",
        }
    },
    "title": "list_claimsArguments",
    "type": "object",
}
SEARCH_SCHEMA: dict[str, Any] = {
    "properties": {
        "employee_id": {"description": "Directory id.", "title": "Employee Id", "type": "string"},
        "start_date": {
            "description": "First day.",
            "format": "date",
            "title": "Start Date",
            "type": "string",
        },
    },
    "required": ["employee_id", "start_date"],
    "title": "search_calendarArguments",
    "type": "object",
}
CONSTRAINED_SCHEMA: dict[str, Any] = {
    "properties": {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "default": 10,
            "description": "Page size.",
        },
        "name": {"type": "string", "minLength": 2, "maxLength": 40},
    },
    "type": "object",
}
NESTED_SCHEMA: dict[str, Any] = {
    "$defs": {
        "Line": {
            "properties": {"amount": {"type": "number"}, "memo": {"type": "string"}},
            "required": ["amount"],
            "title": "Line",
            "type": "object",
        }
    },
    "properties": {"lines": {"items": {"$ref": "#/$defs/Line"}, "type": "array"}},
    "required": ["lines"],
    "type": "object",
}


def spec(name: str, schema: dict[str, Any], description: str = "Does a thing.") -> McpToolSpec:
    return McpToolSpec(name=name, description=description, input_schema=schema)


def find_keys(node: Any, key: str) -> list[Any]:
    """Every value stored under ``key`` anywhere in a nested structure."""
    found: list[Any] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                found.append(v)
            found.extend(find_keys(v, key))
    elif isinstance(node, list):
        for item in node:
            found.extend(find_keys(item, key))
    return found


# -- claude_tool --------------------------------------------------------------------------------


def test_a_tool_is_strict_with_a_closed_schema():
    tool = claude_tool(spec("submit_claim", SUBMIT_SCHEMA, "Submit a claim."))
    assert set(tool) == {"name", "description", "input_schema", "strict"}
    assert tool["name"] == "submit_claim"
    assert tool["description"] == "Submit a claim."
    assert tool["strict"] is True
    schema = tool["input_schema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["claim_id", "title", "total", "document_ids"]
    assert set(schema["properties"]) == {"claim_id", "title", "total", "document_ids"}
    assert schema["properties"]["document_ids"]["items"] == {"type": "string"}
    assert schema["properties"]["claim_id"]["description"] == "Claim id."


def test_every_object_in_a_strict_schema_is_closed():
    schema = claude_tool(spec("record", NESTED_SCHEMA))["input_schema"]
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Line"]["additionalProperties"] is False
    assert schema["properties"]["lines"]["items"] == {"$ref": "#/$defs/Line"}
    assert all(flag is False for flag in find_keys(schema, "additionalProperties"))


def test_pydantic_titles_are_dropped_but_a_property_called_title_stays():
    schema = claude_tool(spec("submit_claim", SUBMIT_SCHEMA))["input_schema"]
    assert "title" in schema["properties"]  # the claim's title field
    assert schema["properties"]["title"] == {"type": "string", "description": "Short claim title."}
    assert [t for t in find_keys(schema, "title") if isinstance(t, str)] == []  # no annotations
    assert "Arguments" not in json.dumps(schema)


def test_enums_formats_and_optional_types_survive():
    listing = claude_tool(spec("list_claims", LIST_SCHEMA))["input_schema"]
    status = listing["properties"]["status"]
    assert status["anyOf"][0]["enum"] == ["received", "under_review", "approved", "rejected"]
    assert status["anyOf"][1] == {"type": "null"}
    assert "required" not in listing  # everything optional stays optional
    search = claude_tool(spec("search_calendar", SEARCH_SCHEMA))["input_schema"]
    assert search["properties"]["start_date"]["format"] == "date"


def test_unsupported_keywords_move_into_the_description():
    schema = claude_tool(spec("page", CONSTRAINED_SCHEMA))["input_schema"]
    limit, name = schema["properties"]["limit"], schema["properties"]["name"]
    for node in (limit, name):
        assert set(node) <= {"type", "description"}
    assert "minimum: 1" in limit["description"] and "maximum: 50" in limit["description"]
    assert limit["description"].startswith("Page size.")
    assert "default: 10" in limit["description"]
    assert "minLength: 2" in name["description"] and "maxLength: 40" in name["description"]


def test_a_schema_the_strict_subset_cannot_express_falls_back_to_a_closed_plain_tool():
    schema = {"type": "object", "properties": {"anything": {}}, "title": "Odd"}
    tool = claude_tool(spec("odd", schema))
    assert "strict" not in tool
    assert tool["input_schema"] == {
        "type": "object",
        "properties": {"anything": {}},
        "additionalProperties": False,
    }


def test_a_schema_with_an_unsupported_type_also_falls_back():
    schema = {"type": "object", "properties": {"x": {"type": ["string", "null"]}}}
    tool = claude_tool(spec("odd", schema))
    assert "strict" not in tool and tool["input_schema"]["additionalProperties"] is False


def test_strict_can_be_switched_off():
    tool = claude_tool(spec("page", CONSTRAINED_SCHEMA), strict=False)
    assert "strict" not in tool
    schema = tool["input_schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["limit"]["minimum"] == 1  # untouched: no strict folding


def test_non_object_roots_are_not_force_closed():
    tool = claude_tool(spec("odd", {"type": "string"}), strict=False)
    assert tool["input_schema"] == {"type": "string"}


def test_values_inside_default_and_const_are_not_mistaken_for_titles():
    schema = {
        "type": "object",
        "properties": {"mode": {"default": {"title": "keep me"}, "const": {"title": "me too"}}},
    }
    tool = claude_tool(spec("keep", schema), strict=False)
    mode = tool["input_schema"]["properties"]["mode"]
    assert mode == {"default": {"title": "keep me"}, "const": {"title": "me too"}}


def test_the_source_schema_is_never_mutated():
    original = copy.deepcopy(SUBMIT_SCHEMA)
    claude_tool(spec("submit_claim", SUBMIT_SCHEMA))
    claude_tool(spec("submit_claim", SUBMIT_SCHEMA), strict=False)
    assert original == SUBMIT_SCHEMA


def test_a_missing_description_falls_back_to_the_title_then_the_name():
    assert claude_tool(McpToolSpec(name="t", title="Nice title"))["description"] == "Nice title"
    assert claude_tool(McpToolSpec(name="t"))["description"] == "t"


@pytest.mark.parametrize("name", ["", "has space", "dotted.name", "x" * 65, "ünï"])
def test_names_claude_would_reject_are_refused(name: str):
    with pytest.raises(ValueError, match="not a valid Claude tool name"):
        claude_tool(spec(name, {"type": "object"}))


def test_definitions_are_plain_json():
    tool = claude_tool(spec("submit_claim", SUBMIT_SCHEMA))
    assert json.loads(json.dumps(tool)) == tool


# -- tools_for_claude ---------------------------------------------------------------------------


@pytest.fixture
def finance() -> FakeMcpClient:
    fake = FakeMcpClient(name="finance")
    fake.add_tool(
        "submit_claim",
        {"reference": "FIN-2026-000001", "status": "received", "duplicate": False},
        description="Submit a claim.",
        input_schema=SUBMIT_SCHEMA,
    )
    fake.add_tool("list_claims", {"result": []}, input_schema=LIST_SCHEMA, read_only=True)
    fake.add_tool("decide_claim", {"status": "approved"}, input_schema={"type": "object"})
    return fake


@pytest.fixture
def corp() -> FakeMcpClient:
    fake = FakeMcpClient(name="corp")
    fake.add_tool("search_calendar", {"result": []}, input_schema=SEARCH_SCHEMA)
    fake.add_tool("get_policy", {"result": "Tier-1 cities: Pune"}, input_schema={"type": "object"})
    return fake


async def test_tools_for_claude_converts_every_tool(finance: FakeMcpClient):
    tools = await tools_for_claude(finance)
    assert [t["name"] for t in tools] == ["submit_claim", "list_claims", "decide_claim"]
    assert all(
        t["strict"] is True and t["input_schema"]["additionalProperties"] is False for t in tools
    )


async def test_tools_for_claude_can_limit_to_an_allow_list(finance: FakeMcpClient):
    tools = await tools_for_claude(finance, allow={"submit_claim", "not_a_tool"})
    assert [t["name"] for t in tools] == ["submit_claim"]
    assert await tools_for_claude(finance, allow=set()) == []


async def test_tools_for_claude_passes_the_strict_switch_on(finance: FakeMcpClient):
    tools = await tools_for_claude(finance, strict=False)
    assert all("strict" not in t for t in tools)


# -- McpBridge: discovery -----------------------------------------------------------------------


async def test_the_bridge_offers_the_tools_of_every_server_in_order(
    finance: FakeMcpClient, corp: FakeMcpClient
):
    bridge = McpBridge({"finance": finance, "corp": corp})
    names = [t["name"] for t in await bridge.tools()]
    assert names == ["submit_claim", "list_claims", "decide_claim", "search_calendar", "get_policy"]


async def test_the_bridge_only_offers_allowed_tools(finance: FakeMcpClient, corp: FakeMcpClient):
    bridge = McpBridge({"finance": finance, "corp": corp}, allow=EMPLOYEE_AGENT_TOOLS)
    names = [t["name"] for t in await bridge.tools()]
    assert names == ["submit_claim", "search_calendar", "get_policy"]


async def test_the_bridge_discovers_once_and_hands_out_copies(finance: FakeMcpClient):
    listed = 0
    original = finance.list_tools

    async def counting() -> list[McpToolSpec]:
        nonlocal listed
        listed += 1
        return await original()

    finance.list_tools = counting  # type: ignore[method-assign]
    bridge = McpBridge({"finance": finance})
    first = await bridge.tools()
    first[-1]["cache_control"] = {"type": "ephemeral"}  # what a caching chat agent does
    first[0]["input_schema"]["properties"].clear()
    second = await bridge.tools()
    await bridge.call({"id": "t1", "name": "list_claims", "input": {}})
    assert listed == 1
    assert "cache_control" not in second[-1]
    assert second[0]["input_schema"]["properties"]  # the cache was not damaged
    assert second == await bridge.tools()


async def test_a_tool_offered_by_two_servers_is_refused():
    one, two = FakeMcpClient(), FakeMcpClient()
    one.add_tool("lookup", {})
    two.add_tool("lookup", {})
    with pytest.raises(ValueError, match="'lookup' is offered by both 'one' and 'two'"):
        await McpBridge({"one": one, "two": two}).tools()
    # unless the allow list keeps only one of them out of the clash
    assert [t["name"] for t in await McpBridge({"one": one}).tools()] == ["lookup"]


async def test_failed_discovery_is_not_cached(finance: FakeMcpClient):
    bridge = McpBridge({"finance": finance})
    finance.failure = McpUnavailableError("finance is down")
    with pytest.raises(McpUnavailableError):
        await bridge.tools()
    finance.failure = None
    assert len(await bridge.tools()) == 3


# -- McpBridge: executing tool_use blocks -------------------------------------------------------


def tool_use(name: str, arguments: Any, identifier: str = "toolu_01") -> dict[str, Any]:
    return {"type": "tool_use", "id": identifier, "name": name, "input": arguments}


async def test_a_tool_call_is_routed_to_the_owning_server(
    finance: FakeMcpClient, corp: FakeMcpClient
):
    bridge = McpBridge({"finance": finance, "corp": corp})
    arguments = {"claim_id": "clm-1", "title": "Trip", "total": 10.5, "document_ids": ["d1"]}
    result = await bridge.call(tool_use("submit_claim", arguments, "toolu_42"))
    assert finance.calls == [("submit_claim", arguments)]
    assert corp.calls == []
    assert result == {
        "type": "tool_result",
        "tool_use_id": "toolu_42",
        "content": '{"reference":"FIN-2026-000001","status":"received","duplicate":false}',
        "is_error": False,
    }


async def test_a_real_anthropic_tool_use_block_works(finance: FakeMcpClient):
    block = ToolUseBlock(
        type="tool_use", id="toolu_77", name="list_claims", input={"status": "received"}
    )
    result = await McpBridge({"finance": finance}).call(block)
    assert finance.calls == [("list_claims", {"status": "received"})]
    assert result["tool_use_id"] == "toolu_77" and result["content"] == "[]"


async def test_results_are_rendered_compactly_for_the_model(corp: FakeMcpClient):
    corp.add_tool("plain", {"result": "Tier-1 cities: Pune, Mumbai - limit ₹5,000"})
    corp.add_tool("rows", {"result": [{"a": 1}, {"a": 2}]})
    corp.add_tool("text", {"text": "unstructured"})
    corp.add_tool("scalar", {"result": 7})
    corp.add_tool("object", {"a": "₹", "b": [1, 2]})
    bridge = McpBridge({"corp": corp})
    outputs = {
        name: (await bridge.call(tool_use(name, {})))["content"]
        for name in ("plain", "rows", "text", "scalar", "object")
    }
    assert outputs == {
        "plain": "Tier-1 cities: Pune, Mumbai - limit ₹5,000",
        "rows": '[{"a":1},{"a":2}]',
        "text": "unstructured",
        "scalar": "7",
        "object": '{"a":"₹","b":[1,2]}',
    }


async def test_a_tool_error_goes_back_to_the_model_as_an_error_result(finance: FakeMcpClient):
    finance.add_tool("submit_claim", McpToolError("idempotency_key 'k' was already used"))
    result = await McpBridge({"finance": finance}).call(tool_use("submit_claim", {}))
    assert result["is_error"] is True
    assert result["content"] == "idempotency_key 'k' was already used"


async def test_an_outage_is_reported_without_internals(finance: FakeMcpClient):
    finance.add_tool("submit_claim", McpUnavailableError("finance: cannot reach (ConnectError)"))
    result = await McpBridge({"finance": finance}).call(tool_use("submit_claim", {}))
    assert result["is_error"] is True
    assert "finance system is unavailable" in result["content"]
    assert "ConnectError" not in result["content"]


async def test_a_protocol_failure_tells_the_model_not_to_retry(finance: FakeMcpClient):
    finance.add_tool("submit_claim", McpProtocolError("finance: unexpected RuntimeError"))
    result = await McpBridge({"finance": finance}).call(tool_use("submit_claim", {}))
    assert result["is_error"] is True
    assert "unexpected response" in result["content"] and "Do not retry" in result["content"]
    assert "RuntimeError" not in result["content"]


async def test_unknown_tools_are_refused_without_a_call(finance: FakeMcpClient):
    result = await McpBridge({"finance": finance}).call(tool_use("drop_database", {}))
    assert result["is_error"] is True and result["content"] == "Unknown tool: drop_database"
    assert finance.calls == []


async def test_tools_outside_the_allow_list_can_never_be_executed(finance: FakeMcpClient):
    bridge = McpBridge({"finance": finance}, allow=EMPLOYEE_AGENT_TOOLS)
    result = await bridge.call(
        tool_use("decide_claim", {"reference": "FIN-1", "decision": "approved"})
    )
    assert result["is_error"] is True and result["content"] == "Unknown tool: decide_claim"
    assert finance.calls == []  # the server never heard about it


@pytest.mark.parametrize("arguments", [None, "oops", ["a"], 3])
async def test_non_object_input_is_an_error_result(finance: FakeMcpClient, arguments: Any):
    result = await McpBridge({"finance": finance}).call(tool_use("list_claims", arguments))
    assert result["is_error"] is True and "JSON object" in result["content"]
    assert finance.calls == []


async def test_a_call_survives_discovery_trouble(finance: FakeMcpClient):
    finance.failure = McpUnavailableError("finance is down")
    bridge = McpBridge({"finance": finance})
    result = await bridge.call(tool_use("list_claims", {}))
    assert result["is_error"] is True and result["content"].startswith("Tools are unavailable")
    finance.failure = None
    assert (await bridge.call(tool_use("list_claims", {})))["is_error"] is False


async def test_a_call_works_before_tools_were_ever_requested(finance: FakeMcpClient):
    # e.g. a worker that resumes a conversation: discovery happens lazily on the first call
    result = await McpBridge({"finance": finance}).call(tool_use("list_claims", {}))
    assert result["is_error"] is False


# -- the employee-facing tool set ---------------------------------------------------------------


def test_the_employee_agent_never_gets_approver_actions_or_the_whole_directory():
    assert {"start_review", "decide_claim", "list_claims", "list_employees"}.isdisjoint(
        EMPLOYEE_AGENT_TOOLS
    )
    assert {
        "submit_claim",
        "get_claim_status",
        "search_calendar",
        "get_policy",
    } <= EMPLOYEE_AGENT_TOOLS


def test_boolean_additional_properties_pass_through_the_title_stripper():
    schema = {"type": "object", "properties": {}, "additionalProperties": True, "title": "Loose"}
    tool = claude_tool(spec("loose", schema), strict=False)
    assert tool["input_schema"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,  # the root is always closed
    }


async def test_a_single_field_result_with_another_name_is_rendered_as_json(corp: FakeMcpClient):
    corp.add_tool("count", {"total": 3})
    result = await McpBridge({"corp": corp}).call(tool_use("count", {}))
    assert result["content"] == '{"total":3}'
