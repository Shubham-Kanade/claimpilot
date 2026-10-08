"""The ports (claimpilot.ports) implemented on the typed MCP clients."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from claimpilot import ports
from claimpilot.config import Settings
from claimpilot.domain.claims import Claim, ClaimMode, Employee
from claimpilot.mcp import (
    CorpClient,
    FakeMcpClient,
    FinanceClient,
    McpCalendar,
    McpDirectory,
    McpFinance,
    McpPorts,
    McpProtocolError,
    McpToolError,
    McpUnavailableError,
    StreamableHttpClient,
    get_mcp_ports,
)
from claimpilot.ports import FakeFinance

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
NOT_FOUND = "no employee with directory id or HR number 'P999'"


def event(identifier: str, day: str, kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": identifier,
        "title": f"{kind} {day}",
        "date": day,
        "end_date": None,
        "start_time": None,
        "end_time": None,
        "location": None,
        "attendees": ["Neha Rao (Kestrel Logistics)"],
        "kind": kind,
        **extra,
    }


def claim(**overrides: Any) -> Claim:
    base: dict[str, Any] = {
        "id": "clm-001",
        "employee_id": "P001",
        "title": "Pune trip 12-14 Aug",
        "mode": ClaimMode.trip,
        "document_ids": ["doc-a", "doc-b"],
        "total": 4500.5,
        "currency": "INR",
    }
    return Claim(**{**base, **overrides})


def receipt(reference: str = "FIN-2026-000001", *, duplicate: bool = False) -> dict[str, Any]:
    return {
        "reference": reference,
        "status": "received",
        "received_at": "2026-10-08T09:00:00Z",
        "duplicate": duplicate,
    }


def decided(status: str) -> dict[str, Any]:
    return {
        "reference": "FIN-2026-000001",
        "claim_id": "clm-001",
        "employee_id": "P001",
        "title": "Pune trip 12-14 Aug",
        "total": 4500.5,
        "currency": "INR",
        "document_ids": ["doc-a", "doc-b"],
        "status": status,
        "received_at": "2026-10-08T09:00:00Z",
        "updated_at": "2026-10-08T10:00:00Z",
        "history": [],
    }


@pytest.fixture
def corp_fake() -> FakeMcpClient:
    return FakeMcpClient(name="corp")


@pytest.fixture
def finance_fake() -> FakeMcpClient:
    return FakeMcpClient(name="finance")


# -- McpDirectory -------------------------------------------------------------------------------


async def test_the_directory_finds_an_employee_by_either_id(corp_fake: FakeMcpClient):
    corp_fake.add_tool("get_employee", P001)
    directory: ports.EmployeeDirectory = McpDirectory(CorpClient(corp_fake))
    by_id = await directory.get("P001")
    by_hr = await directory.get("EMP85968")
    assert isinstance(by_id, Employee) and by_id == by_hr
    assert by_id.name == "Advika Hayer"
    assert [args["employee_id"] for _, args in corp_fake.calls] == ["P001", "EMP85968"]


async def test_the_directory_returns_none_for_an_unknown_employee(corp_fake: FakeMcpClient):
    corp_fake.add_tool("get_employee", McpToolError(NOT_FOUND, tool="get_employee"))
    assert await McpDirectory(CorpClient(corp_fake)).get("P999") is None


async def test_the_directory_does_not_hide_real_failures(corp_fake: FakeMcpClient):
    directory = McpDirectory(CorpClient(corp_fake))
    corp_fake.add_tool("get_employee", McpToolError("directory is read-only today"))
    with pytest.raises(McpToolError):
        await directory.get("P001")
    corp_fake.failure = McpUnavailableError("corp is down")
    with pytest.raises(McpUnavailableError):
        await directory.get("P001")
    with pytest.raises(McpUnavailableError):
        await directory.list()


async def test_the_directory_lists_everyone(corp_fake: FakeMcpClient):
    corp_fake.add_tool(
        "list_employees", {"result": [P001, {**P001, "id": "P002", "employee_id": "EMP2"}]}
    )
    people = await McpDirectory(CorpClient(corp_fake)).list()
    assert [p.id for p in people] == ["P001", "P002"]
    assert all(type(p) is Employee for p in people)


# -- McpCalendar --------------------------------------------------------------------------------


async def test_the_calendar_returns_port_events(corp_fake: FakeMcpClient):
    corp_fake.add_tool(
        "search_calendar",
        {
            "result": [
                event("E1", "2026-08-04", "client_dinner", location="Chandni Cafe, Bhubaneswar"),
                event("E2", "2026-08-12", "travel", end_date="2026-08-14"),
            ]
        },
    )
    calendar: ports.CalendarSource = McpCalendar(CorpClient(corp_fake))
    events = await calendar.events("P002", dt.date(2026, 8, 1), dt.date(2026, 8, 31))
    assert corp_fake.calls == [
        (
            "search_calendar",
            {"employee_id": "P002", "start_date": "2026-08-01", "end_date": "2026-08-31"},
        )
    ]
    assert all(isinstance(e, ports.CalendarEvent) for e in events)
    assert [(e.id, e.kind, e.date) for e in events] == [
        ("E1", "client_dinner", dt.date(2026, 8, 4)),
        ("E2", "travel", dt.date(2026, 8, 12)),
    ]
    assert events[0].attendees == ["Neha Rao (Kestrel Logistics)"]
    # the richer fields ride along for callers that want them
    assert getattr(events[0], "location") == "Chandni Cafe, Bhubaneswar"  # noqa: B009
    assert getattr(events[1], "end_date") == dt.date(2026, 8, 14)  # noqa: B009


async def test_the_calendar_of_an_unknown_employee_is_empty(corp_fake: FakeMcpClient):
    corp_fake.add_tool("search_calendar", McpToolError(NOT_FOUND, tool="search_calendar"))
    calendar = McpCalendar(CorpClient(corp_fake))
    assert await calendar.events("P999", dt.date(2026, 8, 1), dt.date(2026, 8, 2)) == []


async def test_the_calendar_does_not_hide_real_failures(corp_fake: FakeMcpClient):
    corp_fake.add_tool("search_calendar", McpToolError("start_date must not be after end_date"))
    calendar = McpCalendar(CorpClient(corp_fake))
    with pytest.raises(McpToolError):
        await calendar.events("P002", dt.date(2026, 8, 5), dt.date(2026, 8, 1))
    corp_fake.add_tool("search_calendar", {"result": "oops"})
    with pytest.raises(McpProtocolError):
        await calendar.events("P002", dt.date(2026, 8, 1), dt.date(2026, 8, 2))


# -- McpFinance ---------------------------------------------------------------------------------


async def test_submit_claim_passes_the_claim_and_the_key_to_the_finance_tool(
    finance_fake: FakeMcpClient,
):
    finance_fake.add_tool("submit_claim", receipt())
    finance: ports.FinanceSystem = McpFinance(FinanceClient(finance_fake))
    result = await finance.submit_claim(claim(), idempotency_key="submit-clm-001-v1")
    assert finance_fake.calls == [
        (
            "submit_claim",
            {
                "claim_id": "clm-001",
                "employee_id": "P001",
                "title": "Pune trip 12-14 Aug",
                "total": 4500.5,
                "currency": "INR",
                "document_ids": ["doc-a", "doc-b"],
                "idempotency_key": "submit-clm-001-v1",
            },
        )
    ]
    assert isinstance(result, ports.SubmissionResult)
    assert (result.reference, result.status, result.duplicate) == (
        "FIN-2026-000001",
        "received",
        False,
    )


async def test_a_replayed_submission_comes_back_flagged(finance_fake: FakeMcpClient):
    finance_fake.add_tool("submit_claim", receipt(duplicate=True))
    result = await McpFinance(FinanceClient(finance_fake)).submit_claim(
        claim(), idempotency_key="k"
    )
    assert result.duplicate is True


async def test_submission_failures_are_not_swallowed(finance_fake: FakeMcpClient):
    finance = McpFinance(FinanceClient(finance_fake))
    finance_fake.add_tool("submit_claim", McpToolError("idempotency_key 'k' was already used"))
    with pytest.raises(McpToolError, match="already used"):
        await finance.submit_claim(claim(), idempotency_key="k")
    finance_fake.failure = McpUnavailableError("finance is down")
    with pytest.raises(McpUnavailableError):
        await finance.submit_claim(claim(), idempotency_key="k")


async def test_approving_returns_the_new_status(finance_fake: FakeMcpClient):
    finance_fake.add_tool("decide_claim", decided("approved"))
    status = await McpFinance(FinanceClient(finance_fake)).decide_claim(
        "FIN-2026-000001", approved=True, approver_id="DEMO-RAVI"
    )
    assert status == "approved"
    assert finance_fake.calls == [
        (
            "decide_claim",
            {
                "reference": "FIN-2026-000001",
                "decision": "approved",
                "approver_id": "DEMO-RAVI",
                "comment": "",
            },
        )
    ]


async def test_rejecting_sends_the_reason_and_returns_the_new_status(finance_fake: FakeMcpClient):
    finance_fake.add_tool("decide_claim", decided("rejected"))
    status = await McpFinance(FinanceClient(finance_fake)).decide_claim(
        "FIN-2026-000001", approved=False, approver_id="DEMO-RAVI", comment="Hotel invoice missing"
    )
    assert status == "rejected"
    arguments = finance_fake.calls[0][1]
    assert (arguments["decision"], arguments["comment"]) == ("rejected", "Hotel invoice missing")


async def test_a_rejection_without_a_reason_surfaces_the_finance_error(finance_fake: FakeMcpClient):
    finance_fake.add_tool(
        "decide_claim",
        McpToolError("a comment explaining the reason is required to reject a claim"),
    )
    with pytest.raises(McpToolError, match="comment"):
        await McpFinance(FinanceClient(finance_fake)).decide_claim(
            "FIN-2026-000001", approved=False, approver_id="DEMO-RAVI"
        )


async def test_the_decision_arguments_are_keyword_only(finance_fake: FakeMcpClient):
    finance = McpFinance(FinanceClient(finance_fake))
    with pytest.raises(TypeError):
        await finance.decide_claim("FIN-2026-000001", True, "DEMO-RAVI")  # type: ignore[misc]


async def test_the_adapter_behaves_like_the_in_memory_fake(finance_fake: FakeMcpClient):
    """The same calls give the same shapes on FakeFinance and on McpFinance."""
    seen: dict[str, dict[str, Any]] = {}

    def submit(arguments: dict[str, Any]) -> dict[str, Any]:
        key = arguments["idempotency_key"]
        if key in seen:
            return {**seen[key], "duplicate": True}
        seen[key] = receipt(f"FIN-2026-{len(seen) + 1:06d}")
        return seen[key]

    finance_fake.add_tool("submit_claim", submit)
    finance_fake.add_tool("decide_claim", lambda a: decided(a["decision"]))
    systems: list[ports.FinanceSystem] = [FakeFinance(), McpFinance(FinanceClient(finance_fake))]
    outcomes = []
    for system in systems:
        first = await system.submit_claim(claim(), idempotency_key="k1")
        again = await system.submit_claim(claim(), idempotency_key="k1")
        other = await system.submit_claim(claim(id="clm-002"), idempotency_key="k2")
        verdict = await system.decide_claim(
            first.reference, approved=False, approver_id="A", comment="no"
        )
        outcomes.append(
            (
                first.reference,
                first.duplicate,
                again.reference,
                again.duplicate,
                other.reference,
                verdict,
            )
        )
    assert (
        outcomes[0]
        == outcomes[1]
        == (
            "FIN-2026-000001",
            False,
            "FIN-2026-000001",
            True,
            "FIN-2026-000002",
            "rejected",
        )
    )


# -- wiring helpers -----------------------------------------------------------------------------


def test_ports_share_the_clients_they_are_built_from(
    corp_fake: FakeMcpClient, finance_fake: FakeMcpClient
):
    corp, finance = CorpClient(corp_fake), FinanceClient(finance_fake)
    built = McpPorts.from_clients(corp, finance)
    assert isinstance(built.directory, McpDirectory) and isinstance(built.calendar, McpCalendar)
    assert isinstance(built.finance, McpFinance)
    assert built.directory._corp is corp is built.calendar._corp
    assert built.finance._finance is finance


def test_get_mcp_ports_uses_the_configured_urls():
    settings = Settings(
        mcp_corp_url="http://mcp-corp:8102/mcp", mcp_finance_url="http://mcp-finance:8101/mcp"
    )
    built = get_mcp_ports(settings)
    corp_http = built.directory._corp._inner
    finance_http = built.finance._finance._inner
    assert isinstance(corp_http, StreamableHttpClient) and isinstance(
        finance_http, StreamableHttpClient
    )
    assert (corp_http.url, finance_http.url) == (
        "http://mcp-corp:8102/mcp",
        "http://mcp-finance:8101/mcp",
    )
