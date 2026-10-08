"""The pipeline's ports (``claimpilot.ports``) implemented on top of the typed MCP clients.

* ``McpDirectory``  -> ``EmployeeDirectory``  (corp server: HR directory)
* ``McpCalendar``   -> ``CalendarSource``     (corp server: calendars)
* ``McpFinance``    -> ``FinanceSystem``      (finance server)

They are thin on purpose: the mapping between the pipeline's types and the tools' arguments lives
here and nowhere else. A lookup for someone who does not exist is "not found" (``None`` / no
events), not an error; every other failure is an ``McpError`` from the clients, unchanged.

The clients open a short-lived session per call, so nothing here needs closing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from claimpilot import ports
from claimpilot.config import Settings
from claimpilot.domain.claims import Claim, Employee
from claimpilot.mcp.client import McpError, McpToolError
from claimpilot.mcp.corp import CorpClient, EmployeeNotFoundError, get_corp_client
from claimpilot.mcp.finance import FinanceClient, get_finance_client

# `McpDirectory.list` shadows the builtin inside the class body, so annotate through an alias.
_Employees = list[Employee]


class McpDirectory:
    """``EmployeeDirectory`` over the corporate MCP server. Finds people by either id."""

    def __init__(self, corp: CorpClient) -> None:
        self._corp = corp

    async def get(self, employee_id: str) -> Employee | None:
        """The employee with this directory id (``P001``) or HR number, ``None`` if unknown."""
        try:
            return await self._corp.get_employee(employee_id)
        except EmployeeNotFoundError:
            return None

    async def list(self) -> _Employees:
        return await self._corp.list_employees()


class McpCalendar:
    """``CalendarSource`` over the corporate MCP server.

    Events come back as the richer ``claimpilot.mcp.corp.CalendarEvent`` (a ``ports.CalendarEvent``
    that also has ``end_date``, times and ``location``). Multi-day events (travel, offsites) are
    included when any of their days falls in the range.
    """

    def __init__(self, corp: CorpClient) -> None:
        self._corp = corp

    async def events(self, employee_id: str, start: date, end: date) -> list[ports.CalendarEvent]:
        try:
            found = await self._corp.search_calendar(employee_id, start, end)
        except EmployeeNotFoundError:
            return []  # an unknown employee has an empty calendar, as in the in-memory fake
        return [*found]


class McpFinance:
    """``FinanceSystem`` over the finance MCP server."""

    def __init__(self, finance: FinanceClient) -> None:
        self._finance = finance

    async def submit_claim(self, claim: Claim, *, idempotency_key: str) -> ports.SubmissionResult:
        """Submit ``claim``; retrying with the same key returns the original reference."""
        receipt = await self._finance.submit_claim(
            claim_id=claim.id,
            employee_id=claim.employee_id,
            title=claim.title,
            total=claim.total,
            currency=claim.currency,
            document_ids=claim.document_ids,
            idempotency_key=idempotency_key,
        )
        return ports.SubmissionResult(
            reference=receipt.reference, status=receipt.status, duplicate=receipt.duplicate
        )

    async def decide_claim(
        self, reference: str, *, approved: bool, approver_id: str, comment: str = ""
    ) -> str:
        """Approve or reject; returns the claim's new status (``approved`` / ``rejected``).

        Finance requires a ``comment`` when rejecting.
        """
        wanted = "approved" if approved else "rejected"
        try:
            decided = await self._finance.decide_claim(reference, wanted, approver_id, comment)
        except McpToolError as refusal:
            # Decisions are final. If finance already holds this very decision, an earlier attempt
            # got through and only our own record of it was lost: finish the job instead of failing.
            try:
                current = await self._finance.get_claim_status(reference)
            except McpError:
                raise refusal from None  # cannot tell: the original refusal is the honest answer
            if current.status != wanted:
                raise
            return current.status
        return decided.status


@dataclass(frozen=True)
class McpPorts:
    """The three adapters, sharing the two typed clients."""

    directory: McpDirectory
    calendar: McpCalendar
    finance: McpFinance

    @classmethod
    def from_clients(cls, corp: CorpClient, finance: FinanceClient) -> McpPorts:
        return cls(McpDirectory(corp), McpCalendar(corp), McpFinance(finance))


def get_mcp_ports(settings: Settings) -> McpPorts:
    """Adapters for the servers at ``settings.mcp_corp_url`` / ``settings.mcp_finance_url``."""
    return McpPorts.from_clients(get_corp_client(settings), get_finance_client(settings))
