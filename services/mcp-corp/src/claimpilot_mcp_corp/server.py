"""The MCP server of the mock corporate systems: tools, the policy resource, health routes, app.

Transport: streamable HTTP at ``/mcp`` (stateless, plain JSON responses, so a restart never
strands a client on a dead session). ``/healthz`` and ``/readyz`` sit beside it.

The policy is exposed twice: as the resource ``policy://expense/v3`` and as the ``get_policy``
tool, because some MCP clients cannot read resources.
"""

from __future__ import annotations

import datetime as dt
import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from claimpilot_mcp_corp import __version__
from claimpilot_mcp_corp.directory import Directory, DirectoryError
from claimpilot_mcp_corp.models import CalendarEvent, EmployeeRecord
from claimpilot_mcp_corp.policy import PolicyProvider, PolicyUnavailableError

SERVICE_NAME = "claimpilot-mcp-corp"
MCP_PATH = "/mcp"
POLICY_URI = "policy://expense/v3"

INSTRUCTIONS = (
    "Mock corporate systems: the HR directory (get_employee, list_employees), employee calendars "
    "(search_calendar) and the expense policy (get_policy, or the resource policy://expense/v3). "
    "Employees are found by directory id (e.g. P001) or HR number (e.g. EMP85968)."
)

EMPLOYEE_ID = Field(
    description="Directory id (e.g. 'P001') or HR employee number (e.g. 'EMP85968')."
)


@contextmanager
def _as_tool_error() -> Iterator[None]:
    """Report lookup failures as tool errors the model can read (the SDK hides anything else)."""
    try:
        yield
    except (DirectoryError, PolicyUnavailableError) as exc:
        raise ToolError(str(exc)) from exc


class CorpTools:
    """The tool implementations: thin adapters over the directory and the policy provider.

    Docstrings and ``Field`` descriptions are the tool schema the model sees.
    """

    def __init__(self, directory: Directory, policy: PolicyProvider) -> None:
        self._directory = directory
        self._policy = policy

    def get_employee(self, employee_id: Annotated[str, EMPLOYEE_ID]) -> EmployeeRecord:
        """Look up one employee in the HR directory: name, grade, base city and manager.

        Accepts either the directory id (e.g. 'P001') or the HR number (e.g. 'EMP85968').
        """
        with _as_tool_error():
            return self._directory.get(employee_id)

    def list_employees(self) -> list[EmployeeRecord]:
        """List every employee in the HR directory."""
        return self._directory.employees()

    def search_calendar(
        self,
        employee_id: Annotated[str, EMPLOYEE_ID],
        start_date: Annotated[
            dt.date, Field(description="First day of the range, as YYYY-MM-DD (inclusive).")
        ],
        end_date: Annotated[
            dt.date, Field(description="Last day of the range, as YYYY-MM-DD (inclusive).")
        ],
    ) -> list[CalendarEvent]:
        """Find an employee's calendar events that touch a date range, oldest first.

        Use it to see who attended a client meal, or whether the employee was travelling. Each
        event has a kind (client_meeting, client_dinner, offsite, training or travel), a date
        (multi-day events also an end_date), times, a location and the other attendees.
        """
        with _as_tool_error():
            return self._directory.events(employee_id, start_date, end_date)

    def get_policy(self) -> str:
        """Return the full expense policy document (version 3) as text.

        Same content as the resource policy://expense/v3, for clients that cannot read resources.
        """
        with _as_tool_error():
            return self._policy.text()


def _read_only(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )


def create_server(directory: Directory, policy: PolicyProvider) -> MCPServer:
    """Build the MCP server (tools, policy resource, health routes) over the seed data."""
    server = MCPServer(
        SERVICE_NAME,
        title="ClaimPilot mock corporate systems",
        instructions=INSTRUCTIONS,
        version=__version__,
    )
    tools = CorpTools(directory, policy)
    registrations = (
        (tools.get_employee, _read_only("Get employee")),
        (tools.list_employees, _read_only("List employees")),
        (tools.search_calendar, _read_only("Search calendar")),
        (tools.get_policy, _read_only("Get expense policy")),
    )
    for fn, hints in registrations:
        server.add_tool(
            fn,
            title=hints.title,
            description=inspect.cleandoc(fn.__doc__ or ""),
            annotations=hints,
        )

    @server.resource(
        POLICY_URI,
        name="expense_policy_v3",
        title="Expense policy (v3)",
        description="The company travel and expense policy: limits, rules and clause numbers.",
        mime_type="application/yaml",
    )
    def expense_policy() -> str:
        try:
            return policy.text()
        except PolicyUnavailableError as exc:
            raise ResourceError(str(exc)) from exc

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": SERVICE_NAME, "version": __version__})

    @server.custom_route("/readyz", methods=["GET"])
    async def readyz(_: Request) -> JSONResponse:
        checks = {
            "directory": "ok" if directory.employees() else "empty",
            "policy": "ok" if policy.available() else "missing",
        }
        ready = all(state == "ok" for state in checks.values())
        return JSONResponse(
            {"status": "ok" if ready else "unavailable", "checks": checks},
            status_code=200 if ready else 503,
        )

    return server


def create_app(
    directory: Directory, policy: PolicyProvider, *, host: str = "127.0.0.1"
) -> Starlette:
    """The ASGI app. ``host`` is the bind address: loopback binds get DNS-rebinding protection."""
    return create_server(directory, policy).streamable_http_app(
        streamable_http_path=MCP_PATH, json_response=True, stateless_http=True, host=host
    )
