"""The MCP surface (tools and the policy resource) driven through the SDK client, in-process."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp import Client, MCPError
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS, CallToolResult, Tool

from claimpilot_mcp_corp.directory import Directory
from claimpilot_mcp_corp.policy import PolicyProvider
from claimpilot_mcp_corp.server import POLICY_URI, CorpTools, create_server

FIXTURE_TEXT = (Path(__file__).resolve().parents[1] / "fixtures" / "policy.yaml").read_text(
    encoding="utf-8"
)
TOOL_NAMES = {"get_employee", "list_employees", "search_calendar", "get_policy"}


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
def call(directory: Directory, policy: PolicyProvider) -> Caller:
    return Caller(create_server(directory, policy))


@pytest.fixture
def call_without_policy(directory: Directory, tmp_path: Path) -> Caller:
    return Caller(create_server(directory, PolicyProvider(tmp_path / "missing.yaml")))


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
        for name, schema in tool.input_schema.get("properties", {}).items():
            assert schema.get("description"), f"{tool.name}.{name} has no description"


async def test_every_tool_is_read_only(tools: dict[str, Tool]):
    for tool in tools.values():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.open_world_hint is False, tool.name


async def test_calendar_search_takes_iso_dates(tools: dict[str, Tool]):
    schema = tools["search_calendar"].input_schema
    assert set(schema["required"]) == {"employee_id", "start_date", "end_date"}
    assert schema["properties"]["start_date"]["format"] == "date"
    assert schema["properties"]["end_date"]["format"] == "date"


async def test_get_employee_describes_both_identifiers(tools: dict[str, Tool]):
    description = tools["get_employee"].input_schema["properties"]["employee_id"]["description"]
    assert "P001" in description and "EMP85968" in description


async def test_the_policy_is_a_resource_as_well(call: Caller):
    async with Client(call.server) as client:
        resources = (await client.list_resources()).resources
    assert [str(r.uri) for r in resources] == [POLICY_URI]
    assert resources[0].mime_type == "application/yaml"
    assert resources[0].title and resources[0].description


async def test_server_identifies_itself(call: Caller):
    async with Client(call.server) as client:
        assert client.server_info is not None
        assert client.server_info.name == "claimpilot-mcp-corp"


# -- employees ----------------------------------------------------------------------------------


async def test_get_employee_by_directory_id(call: Caller):
    result = await call("get_employee", employee_id="P001")
    assert not result.is_error
    assert result.structured_content == {
        "id": "P001",
        "name": "Advika Hayer",
        "employee_id": "EMP85968",
        "grade": "L4",
        "base_city": "Hyderabad",
        "base_state_code": "36",
        "manager_id": "DEMO-RAVI",
        "email": "advika.hayer@example.com",
    }


async def test_get_employee_by_hr_number(call: Caller):
    by_hr = await call("get_employee", employee_id="EMP85968")
    by_id = await call("get_employee", employee_id="P001")
    assert by_hr.structured_content == by_id.structured_content


async def test_an_unknown_employee_is_a_readable_error(call: Caller):
    message = error_text(await call("get_employee", employee_id="P999"))
    assert "no employee with directory id or HR number 'P999'" in message


async def test_list_employees(call: Caller):
    result = await call("list_employees")
    people = result.structured_content["result"]
    assert [p["id"] for p in people] == [
        "P001",
        "P002",
        "P003",
        "P004",
        "P005",
        "DEMO-ASHA",
        "DEMO-RAVI",
        "DEMO-MEERA",
    ]
    ravi = next(p for p in people if p["id"] == "DEMO-RAVI")
    assert (ravi["name"], ravi["grade"], ravi["base_city"], ravi["manager_id"]) == (
        "Ravi Iyer",
        "L5",
        "Bengaluru",
        None,
    )


# -- calendar -----------------------------------------------------------------------------------


async def test_search_calendar_returns_events_in_the_documented_shape(call: Caller):
    result = await call(
        "search_calendar", employee_id="P004", start_date="2026-07-01", end_date="2026-07-31"
    )
    events = result.structured_content["result"]
    assert [(e["kind"], e["date"]) for e in events] == [
        ("client_dinner", "2026-07-07"),
        ("travel", "2026-07-10"),
    ]
    dinner, trip = events
    assert set(dinner) == {
        "id",
        "title",
        "date",
        "end_date",
        "start_time",
        "end_time",
        "location",
        "attendees",
        "kind",
    }
    assert dinner["start_time"] == "18:30:00" and dinner["end_time"] == "21:00:00"
    assert dinner["location"] == "Kesar Grill House, Kochi"
    assert 2 <= len(dinner["attendees"]) <= 4
    assert trip["end_date"] == "2026-07-13" and trip["start_time"] is None


async def test_search_calendar_with_nothing_in_range(call: Caller):
    result = await call(
        "search_calendar", employee_id="P004", start_date="2026-01-01", end_date="2026-01-31"
    )
    assert not result.is_error
    assert result.structured_content == {"result": []}


async def test_search_calendar_works_with_the_hr_number(call: Caller):
    arguments = {"start_date": "2026-08-01", "end_date": "2026-08-31"}
    by_hr = await call("search_calendar", employee_id="EMP74851", **arguments)
    by_id = await call("search_calendar", employee_id="P002", **arguments)
    assert by_hr.structured_content == by_id.structured_content
    assert len(by_id.structured_content["result"]) == 3


async def test_search_calendar_errors_are_readable(call: Caller):
    unknown = await call(
        "search_calendar", employee_id="P999", start_date="2026-08-01", end_date="2026-08-02"
    )
    assert "no employee" in error_text(unknown)
    backwards = await call(
        "search_calendar", employee_id="P002", start_date="2026-08-05", end_date="2026-08-01"
    )
    assert "start_date must not be after end_date" in error_text(backwards)
    too_wide = await call(
        "search_calendar", employee_id="P002", start_date="2020-01-01", end_date="2026-01-01"
    )
    assert "must not exceed 366 days" in error_text(too_wide)


@pytest.mark.parametrize("bad", ["next week", "31/08/2026", "2026-13-01", ""])
async def test_search_calendar_rejects_malformed_dates(call: Caller, bad: str):
    result = await call(
        "search_calendar", employee_id="P002", start_date=bad, end_date="2026-08-31"
    )
    assert result.is_error


# -- policy -------------------------------------------------------------------------------------


async def test_get_policy_returns_the_file_verbatim(call: Caller):
    result = await call("get_policy")
    assert not result.is_error
    assert result.structured_content == {"result": FIXTURE_TEXT}
    assert "₹" in result.structured_content["result"]


async def test_the_resource_returns_the_same_text(call: Caller):
    async with Client(call.server) as client:
        read = await client.read_resource(POLICY_URI)
    assert len(read.contents) == 1
    content = read.contents[0]
    assert str(content.uri) == POLICY_URI
    assert content.mime_type == "application/yaml"
    assert content.text == FIXTURE_TEXT  # type: ignore[union-attr]


async def test_a_missing_policy_is_reported_clearly_by_the_tool(call_without_policy: Caller):
    message = error_text(await call_without_policy("get_policy"))
    assert message.endswith("policy not available: no policy file on this server")


async def test_a_missing_policy_is_reported_clearly_by_the_resource(call_without_policy: Caller):
    async with Client(call_without_policy.server) as client:
        with pytest.raises(MCPError, match="policy not available") as caught:
            await client.read_resource(POLICY_URI)
    assert caught.value.code == INTERNAL_ERROR


async def test_unknown_resources_are_invalid_params(call: Caller):
    async with Client(call.server) as client:
        with pytest.raises(MCPError) as caught:
            await client.read_resource("policy://expense/v2")
    assert caught.value.code == INVALID_PARAMS


# -- calling the tool functions directly --------------------------------------------------------


def test_tool_functions_raise_tool_errors(directory: Directory, policy: PolicyProvider):
    tools = CorpTools(directory, policy)
    with pytest.raises(ToolError, match="no employee"):
        tools.get_employee("P999")
    with pytest.raises(ToolError, match="policy not available"):
        CorpTools(directory, PolicyProvider()).get_policy()


def test_tool_functions_return_typed_models(directory: Directory, policy: PolicyProvider):
    import datetime as dt

    tools = CorpTools(directory, policy)
    assert tools.get_employee("EMP85968").id == "P001"
    assert len(tools.list_employees()) == 8
    found = tools.search_calendar("P002", dt.date(2026, 8, 4), dt.date(2026, 8, 4))
    assert [e.kind for e in found] == ["client_dinner"]
    assert tools.get_policy() == FIXTURE_TEXT
