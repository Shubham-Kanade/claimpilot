"""The HR directory and calendars, loaded from the JSON seed. Pure Python: no MCP in here."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable
from pathlib import Path

from pydantic import TypeAdapter

from claimpilot_mcp_corp.models import CalendarEvent, EmployeeRecord, SeedEvent

EMPLOYEES_FILE = "employees.json"
CALENDAR_FILE = "calendar.json"
MAX_RANGE_DAYS = 366


class DirectoryError(Exception):
    """A lookup the caller can fix; the message is safe to show a model."""


class EmployeeNotFoundError(DirectoryError):
    """No employee has the requested directory id or HR number."""


class InvalidRangeError(DirectoryError):
    """The requested date range is back to front or too wide."""


def _key(identifier: str) -> str:
    return identifier.strip().upper()


class Directory:
    """Employees (looked up by directory id *or* HR number) and their calendar events."""

    def __init__(
        self, employees: Iterable[EmployeeRecord], events: Iterable[SeedEvent] = ()
    ) -> None:
        self._employees = tuple(employees)
        self._index: dict[str, EmployeeRecord] = {}
        for employee in self._employees:
            for identifier in (employee.id, employee.employee_id):
                if _key(identifier) in self._index:
                    raise ValueError(f"duplicate employee identifier {identifier!r}")
                self._index[_key(identifier)] = employee
        for employee in self._employees:
            if employee.manager_id and _key(employee.manager_id) not in self._index:
                raise ValueError(f"{employee.id}: unknown manager_id {employee.manager_id!r}")

        self._events: dict[str, list[CalendarEvent]] = {}
        seen: set[str] = set()
        for event in events:
            owner = self._index.get(_key(event.owner))
            if owner is None:
                raise ValueError(f"event {event.id}: unknown owner {event.owner!r}")
            if event.id in seen:
                raise ValueError(f"duplicate event id {event.id!r}")
            seen.add(event.id)
            # keep only the public fields: the owner is implied by whose calendar holds the event
            public = CalendarEvent.model_validate(event.model_dump(exclude={"owner"}))
            self._events.setdefault(owner.id, []).append(public)
        for owned in self._events.values():
            owned.sort(key=lambda e: (e.date, e.start_time or dt.time.min, e.id))

    @classmethod
    def load(cls, seed_dir: Path) -> Directory:
        """Read ``employees.json`` and ``calendar.json`` from ``seed_dir`` (and validate them)."""
        employees = _read(seed_dir / EMPLOYEES_FILE, TypeAdapter(list[EmployeeRecord]))
        events = _read(seed_dir / CALENDAR_FILE, TypeAdapter(list[SeedEvent]))
        return cls(employees, events)

    @property
    def event_count(self) -> int:
        return sum(len(owned) for owned in self._events.values())

    def employees(self) -> list[EmployeeRecord]:
        return list(self._employees)

    def get(self, identifier: str) -> EmployeeRecord:
        """Find an employee by directory id (``P001``) or HR number (``EMP85968``)."""
        employee = self._index.get(_key(identifier))
        if employee is None:
            raise EmployeeNotFoundError(
                f"no employee with directory id or HR number '{identifier.strip()}'"
            )
        return employee

    def events(self, identifier: str, start: dt.date, end: dt.date) -> list[CalendarEvent]:
        """The employee's events that touch ``start``..``end`` (inclusive), oldest first."""
        employee = self.get(identifier)
        if end < start:
            raise InvalidRangeError("start_date must not be after end_date")
        if (end - start).days > MAX_RANGE_DAYS:
            raise InvalidRangeError(f"the date range must not exceed {MAX_RANGE_DAYS} days")
        return [
            event
            for event in self._events.get(employee.id, [])
            if event.date <= end and event.last_day >= start
        ]


def _read[T](path: Path, adapter: TypeAdapter[T]) -> T:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"seed file not found: {path}") from exc
    return adapter.validate_python(raw)
