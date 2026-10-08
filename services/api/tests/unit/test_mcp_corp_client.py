"""CorpClient: directory, calendar and policy through the corp tools, using FakeMcpClient."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from claimpilot import ports
from claimpilot.config import Settings
from claimpilot.domain.claims import Employee
from claimpilot.mcp import (
    POLICY_URI,
    CalendarEvent,
    CorpClient,
    EmployeeNotFoundError,
    EmployeeProfile,
    FakeMcpClient,
    McpProtocolError,
    McpToolClient,
    McpToolError,
    StreamableHttpClient,
    get_corp_client,
)

P001: dict[str, Any] = {
    "id": "P001",
    "name": "Advika Hayer",
    "employee_id": "EMP85968",
    "grade": "L4",
    "base_city": "Hyderabad",
    "base_state_code": "36",
    "manager_id": "DEMO-RAVI",
    "email": "advika.hayer@example.com",
}
P002: dict[str, Any] = {
    **P001,
    "id": "P002",
    "name": "Zansi Samra",
    "employee_id": "EMP74851",
    "base_city": "Bhubaneswar",
    "base_state_code": "21",
}
DINNER: dict[str, Any] = {
    "id": "CAL-P002-20260804-DIN",
    "title": "Client dinner with Juniper Health",
    "date": "2026-08-04",
    "end_date": None,
    "start_time": "20:00:00",
    "end_time": "22:30:00",
    "location": "Chandni Cafe, Bhubaneswar",
    "attendees": ["Neha Rao (Juniper Health)", "Karan Nair (Juniper Health)"],
    "kind": "client_dinner",
}
TRIP: dict[str, Any] = {
    "id": "CAL-P002-20260812-TRV",
    "title": "Business trip to Chandigarh",
    "date": "2026-08-12",
    "end_date": "2026-08-14",
    "start_time": None,
    "end_time": None,
    "location": "Chandigarh",
    "attendees": [],
    "kind": "travel",
}
NOT_FOUND = "no employee with directory id or HR number 'P999'"


@pytest.fixture
def fake() -> FakeMcpClient:
    return FakeMcpClient(name="corp")


@pytest.fixture
def corp(fake: FakeMcpClient) -> CorpClient:
    return CorpClient(fake)


# -- directory ----------------------------------------------------------------------------------


async def test_get_employee_returns_the_shared_employee_model_without_extras(
    fake: FakeMcpClient, corp: CorpClient
):
    fake.add_tool("get_employee", P001)
    employee = await corp.get_employee("EMP85968")
    assert fake.calls == [("get_employee", {"employee_id": "EMP85968"})]
    assert type(employee) is Employee  # not the profile: no e-mail or manager leaves this call
    assert employee.model_dump() == {
        "id": "P001",
        "name": "Advika Hayer",
        "employee_id": "EMP85968",
        "grade": "L4",
        "base_city": "Hyderabad",
        "base_state_code": "36",
    }


async def test_get_employee_profile_keeps_the_manager_and_email(
    fake: FakeMcpClient, corp: CorpClient
):
    fake.add_tool("get_employee", P001)
    profile = await corp.get_employee_profile("P001")
    assert isinstance(profile, EmployeeProfile) and isinstance(profile, Employee)
    assert (profile.manager_id, profile.email) == ("DEMO-RAVI", "advika.hayer@example.com")


async def test_a_directory_entry_without_optional_fields_is_fine(
    fake: FakeMcpClient, corp: CorpClient
):
    fake.add_tool(
        "get_employee", {k: P001[k] for k in Employee.model_fields if k != "base_state_code"}
    )
    employee = await corp.get_employee("P001")
    assert employee.base_state_code is None


async def test_an_unknown_employee_is_a_named_tool_error(fake: FakeMcpClient, corp: CorpClient):
    fake.add_tool("get_employee", McpToolError(NOT_FOUND, server="corp", tool="get_employee"))
    for lookup in (corp.get_employee("P999"), corp.get_employee_profile("P999")):
        with pytest.raises(EmployeeNotFoundError, match="P999") as caught:
            await lookup
        assert isinstance(caught.value, McpToolError)
        assert (caught.value.server, caught.value.tool) == ("corp", "get_employee")


async def test_other_tool_errors_are_not_mistaken_for_not_found(
    fake: FakeMcpClient, corp: CorpClient
):
    fake.add_tool("get_employee", McpToolError("directory is read-only today"))
    with pytest.raises(McpToolError) as caught:
        await corp.get_employee("P001")
    assert not isinstance(caught.value, EmployeeNotFoundError)


async def test_list_employees_drops_everything_but_the_shared_fields(
    fake: FakeMcpClient, corp: CorpClient
):
    fake.add_tool("list_employees", {"result": [P001, P002]})
    people = await corp.list_employees()
    assert [(p.id, p.base_city) for p in people] == [("P001", "Hyderabad"), ("P002", "Bhubaneswar")]
    assert all(type(p) is Employee for p in people)
    assert fake.calls == [("list_employees", {})]


@pytest.mark.parametrize("broken", [{}, {"result": "x"}, {"result": [{"id": "P1"}]}])
async def test_a_malformed_directory_is_a_protocol_error(
    fake: FakeMcpClient, corp: CorpClient, broken: dict[str, Any]
):
    fake.add_tool("list_employees", broken)
    with pytest.raises(McpProtocolError) as caught:
        await corp.list_employees()
    assert (caught.value.server, caught.value.tool) == ("corp", "list_employees")


# -- calendar -----------------------------------------------------------------------------------


async def test_search_calendar_sends_iso_dates_and_returns_typed_events(
    fake: FakeMcpClient, corp: CorpClient
):
    fake.add_tool("search_calendar", {"result": [DINNER, TRIP]})
    events = await corp.search_calendar("P002", dt.date(2026, 8, 1), dt.date(2026, 8, 31))
    assert fake.calls == [
        (
            "search_calendar",
            {"employee_id": "P002", "start_date": "2026-08-01", "end_date": "2026-08-31"},
        )
    ]
    dinner, trip = events
    assert isinstance(dinner, CalendarEvent) and isinstance(dinner, ports.CalendarEvent)
    assert (dinner.kind, dinner.date, dinner.end_date) == (
        "client_dinner",
        dt.date(2026, 8, 4),
        None,
    )
    assert (dinner.start_time, dinner.end_time) == (dt.time(20, 0), dt.time(22, 30))
    assert dinner.location == "Chandni Cafe, Bhubaneswar"
    assert dinner.attendees == ["Neha Rao (Juniper Health)", "Karan Nair (Juniper Health)"]
    assert (trip.kind, trip.date, trip.end_date) == (
        "travel",
        dt.date(2026, 8, 12),
        dt.date(2026, 8, 14),
    )
    assert trip.start_time is None and trip.attendees == []


async def test_events_know_which_days_they_cover(fake: FakeMcpClient, corp: CorpClient):
    fake.add_tool("search_calendar", {"result": [DINNER, TRIP]})
    dinner, trip = await corp.search_calendar("P002", dt.date(2026, 8, 1), dt.date(2026, 8, 31))
    assert dinner.last_day == dt.date(2026, 8, 4)
    assert dinner.covers(dt.date(2026, 8, 4)) and not dinner.covers(dt.date(2026, 8, 5))
    assert trip.last_day == dt.date(2026, 8, 14)
    assert [trip.covers(dt.date(2026, 8, d)) for d in (11, 12, 13, 14, 15)] == [
        False,
        True,
        True,
        True,
        False,
    ]


async def test_calendar_events_are_immutable(fake: FakeMcpClient, corp: CorpClient):
    fake.add_tool("search_calendar", {"result": [DINNER]})
    (event,) = await corp.search_calendar("P002", dt.date(2026, 8, 4), dt.date(2026, 8, 4))
    with pytest.raises(ValueError, match="frozen"):
        event.title = "other"


async def test_search_calendar_for_an_unknown_employee(fake: FakeMcpClient, corp: CorpClient):
    fake.add_tool("search_calendar", McpToolError(NOT_FOUND, tool="search_calendar"))
    with pytest.raises(EmployeeNotFoundError):
        await corp.search_calendar("P999", dt.date(2026, 8, 1), dt.date(2026, 8, 2))


async def test_a_bad_date_range_is_a_plain_tool_error(fake: FakeMcpClient, corp: CorpClient):
    fake.add_tool(
        "search_calendar",
        McpToolError("start_date must not be after end_date", tool="search_calendar"),
    )
    with pytest.raises(McpToolError) as caught:
        await corp.search_calendar("P002", dt.date(2026, 8, 5), dt.date(2026, 8, 1))
    assert not isinstance(caught.value, EmployeeNotFoundError)


@pytest.mark.parametrize(
    "broken",
    [{}, {"result": {}}, {"result": [{"id": "E1"}]}, {"result": [{**DINNER, "date": "someday"}]}],
)
async def test_a_malformed_calendar_is_a_protocol_error(
    fake: FakeMcpClient, corp: CorpClient, broken: dict[str, Any]
):
    fake.add_tool("search_calendar", broken)
    with pytest.raises(McpProtocolError) as caught:
        await corp.search_calendar("P002", dt.date(2026, 8, 1), dt.date(2026, 8, 31))
    assert caught.value.tool == "search_calendar"


# -- policy -------------------------------------------------------------------------------------


async def test_the_policy_is_read_from_the_resource(fake: FakeMcpClient, corp: CorpClient):
    fake.add_resource(POLICY_URI, "version: '3'\n")
    assert POLICY_URI == "policy://expense/v3"
    assert await corp.get_policy_text() == "version: '3'\n"


async def test_a_missing_policy_is_a_protocol_error(fake: FakeMcpClient, corp: CorpClient):
    with pytest.raises(McpProtocolError, match="Unknown resource"):
        await corp.get_policy_text()


# -- it is still an MCP tool client -------------------------------------------------------------


async def test_the_typed_client_exposes_the_generic_protocol(fake: FakeMcpClient, corp: CorpClient):
    fake.add_tool("get_policy", {"result": "text"})
    assert isinstance(corp, McpToolClient)
    assert [t.name for t in await corp.list_tools()] == ["get_policy"]
    assert await corp.call_tool("get_policy") == {"result": "text"}


def test_the_factory_points_at_the_configured_server():
    corp = get_corp_client(Settings(mcp_corp_url="http://mcp-corp:8102/mcp"))
    inner = corp._inner
    assert isinstance(inner, StreamableHttpClient)
    assert (inner.url, inner.name) == ("http://mcp-corp:8102/mcp", "corp")
