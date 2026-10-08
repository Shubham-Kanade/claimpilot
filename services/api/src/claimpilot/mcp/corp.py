"""Typed client for the mock corporate systems (``services/mcp-corp``): directory, calendar, policy.

Employees come back as the shared ``claimpilot.domain.claims.Employee`` (directory data is minimised
on purpose: no e-mail, no manager). ``get_employee_profile`` is for the approver routing that needs
the manager.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from claimpilot import ports
from claimpilot.config import Settings
from claimpilot.domain.claims import Employee
from claimpilot.mcp.client import (
    DEFAULT_TIMEOUT,
    McpProtocolError,
    McpToolError,
    StreamableHttpClient,
    TypedClient,
)

POLICY_URI = "policy://expense/v3"

# How the server words "no such employee" (a tool error, since the model should read it too).
# The integration tests run against the real server, so a reworded message fails loudly there.
NOT_FOUND_PREFIX = "no employee with directory id or HR number"


class EmployeeNotFoundError(McpToolError):
    """The directory has no employee with the given directory id or HR number."""


class CalendarEvent(ports.CalendarEvent):
    """The corporate calendar's entry: the port's event plus the span, times and place.

    ``kind`` is one of ``client_meeting``, ``client_dinner``, ``offsite``, ``training``, ``travel``.
    Multi-day events (travel, offsites) carry ``end_date`` and no times.
    """

    model_config = ConfigDict(frozen=True)

    end_date: dt.date | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    location: str | None = None

    @property
    def last_day(self) -> dt.date:
        return self.end_date or self.date

    def covers(self, day: dt.date) -> bool:
        """Whether ``day`` falls inside the event (inclusive)."""
        return self.date <= day <= self.last_day


class EmployeeProfile(Employee):
    """The directory entry in full: ``Employee`` plus the manager (the approver) and the e-mail."""

    manager_id: str | None = None
    email: str | None = None


def _parse[T: BaseModel](model: type[T], data: Any, tool: str) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        fields = sorted({".".join(str(part) for part in err["loc"]) for err in exc.errors()})
        raise McpProtocolError(
            f"corp returned an unexpected {tool} result (fields: {', '.join(fields)})",
            server="corp",
            tool=tool,
        ) from exc


def _employee(data: dict[str, Any], tool: str) -> Employee:
    # the shared Employee model forbids extra fields, so keep only what it knows
    known = {k: v for k, v in data.items() if k in Employee.model_fields}
    return _parse(Employee, known, tool)


class CorpClient(TypedClient):
    """The employee directory, calendars and the expense policy."""

    async def _lookup(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool that takes an employee id, naming "no such employee" precisely."""
        try:
            return await self.call_tool(tool, arguments)
        except McpToolError as exc:
            if str(exc).startswith(NOT_FOUND_PREFIX):
                raise EmployeeNotFoundError(str(exc), server=exc.server, tool=exc.tool) from exc
            raise

    async def get_employee(self, employee_id: str) -> Employee:
        """Look up by directory id (``P001``) or HR number (``EMP85968``).

        Raises ``EmployeeNotFoundError`` when nobody has that id.
        """
        result = await self._lookup("get_employee", {"employee_id": employee_id})
        return _employee(result, "get_employee")

    async def get_employee_profile(self, employee_id: str) -> EmployeeProfile:
        """Like ``get_employee`` but with the manager and e-mail."""
        result = await self._lookup("get_employee", {"employee_id": employee_id})
        return _parse(EmployeeProfile, result, "get_employee")

    async def list_employees(self) -> list[Employee]:
        result = await self.call_tool("list_employees")
        items = result.get("result")
        if not isinstance(items, list):
            raise McpProtocolError(
                "corp returned an unexpected list_employees result",
                server="corp",
                tool="list_employees",
            )
        return [_employee(item, "list_employees") for item in items]

    async def search_calendar(
        self, employee_id: str, start_date: dt.date, end_date: dt.date
    ) -> list[CalendarEvent]:
        """The employee's calendar events touching ``start_date``..``end_date`` (inclusive).

        Raises ``EmployeeNotFoundError`` when nobody has that id.
        """
        result = await self._lookup(
            "search_calendar",
            {
                "employee_id": employee_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )
        items = result.get("result")
        if not isinstance(items, list):
            raise McpProtocolError(
                "corp returned an unexpected search_calendar result",
                server="corp",
                tool="search_calendar",
            )
        return [_parse(CalendarEvent, item, "search_calendar") for item in items]

    async def get_policy_text(self) -> str:
        """The expense policy document, as the server holds it (YAML text)."""
        return await self.read_resource(POLICY_URI)


def get_corp_client(settings: Settings, *, timeout: float = DEFAULT_TIMEOUT) -> CorpClient:
    """A client for the corporate-systems MCP server at ``settings.mcp_corp_url``."""
    return CorpClient(StreamableHttpClient(settings.mcp_corp_url, name="corp", timeout=timeout))
