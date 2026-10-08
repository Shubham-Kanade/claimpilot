"""Ports: what the pipeline needs from the (mocked) enterprise systems.

The real implementations are the MCP clients (``claimpilot.mcp``); tests use the in-memory
fakes below. Keeping them as small protocols means the pipeline never knows whether the
finance system is an MCP server, a REST API or a test double.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from claimpilot.domain.claims import Claim, Employee


class CalendarEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    date: date
    kind: str = "meeting"  # client_dinner | client_meeting | travel | training | offsite
    attendees: list[str] = []
    end_date: date | None = None  # last day of a multi-day event (a trip, an offsite)

    @property
    def last_day(self) -> date:
        return self.end_date or self.date

    def overlaps(self, start: date, end: date) -> bool:
        """Whether any day of the event falls in ``start..end`` (inclusive)."""
        return self.date <= end and self.last_day >= start


class SubmissionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reference: str
    status: str = "received"
    duplicate: bool = False


class EmployeeDirectory(Protocol):
    async def get(self, employee_id: str) -> Employee | None: ...

    async def list(self) -> list[Employee]: ...


class CalendarSource(Protocol):
    async def events(self, employee_id: str, start: date, end: date) -> list[CalendarEvent]: ...


class FinanceSystem(Protocol):
    async def submit_claim(self, claim: Claim, *, idempotency_key: str) -> SubmissionResult: ...

    async def decide_claim(
        self, reference: str, *, approved: bool, approver_id: str, comment: str = ""
    ) -> str: ...


# --- in-memory fakes (tests, and the offline fallback when an MCP server is unreachable) -------


class StaticDirectory:
    def __init__(self, employees: Sequence[Employee]) -> None:
        self._by_id = {e.id: e for e in employees}

    async def get(self, employee_id: str) -> Employee | None:
        return self._by_id.get(employee_id)

    async def list(self) -> list[Employee]:
        return list(self._by_id.values())


class StaticCalendar:
    def __init__(self, events: dict[str, list[CalendarEvent]] | None = None) -> None:
        self._events = events or {}

    async def events(self, employee_id: str, start: date, end: date) -> list[CalendarEvent]:
        return [e for e in self._events.get(employee_id, []) if e.overlaps(start, end)]


class FakeFinance:
    """Idempotent in-memory finance system with the real server's rules (a rejection needs a
    reason)."""

    def __init__(self) -> None:
        self.submitted: dict[str, SubmissionResult] = {}
        self.decisions: list[tuple[str, bool, str]] = []

    async def submit_claim(self, claim: Claim, *, idempotency_key: str) -> SubmissionResult:
        if idempotency_key in self.submitted:
            return self.submitted[idempotency_key].model_copy(update={"duplicate": True})
        result = SubmissionResult(reference=f"FIN-2026-{len(self.submitted) + 1:06d}")
        self.submitted[idempotency_key] = result
        return result

    async def decide_claim(
        self, reference: str, *, approved: bool, approver_id: str, comment: str = ""
    ) -> str:
        if not approved and not comment.strip():  # the real finance server demands a reason too
            raise ValueError("a rejection needs a comment")
        self.decisions.append((reference, approved, approver_id))
        return "approved" if approved else "rejected"
