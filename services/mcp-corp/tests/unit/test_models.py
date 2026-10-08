"""CalendarEvent / EmployeeRecord validation."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from pydantic import ValidationError

from claimpilot_mcp_corp.models import CalendarEvent, EmployeeRecord, SeedEvent


def event(**overrides: Any) -> CalendarEvent:
    base: dict[str, Any] = {
        "id": "E1",
        "title": "Dinner",
        "date": dt.date(2026, 8, 4),
        "kind": "client_dinner",
    }
    return CalendarEvent(**{**base, **overrides})


def test_a_single_day_event_needs_only_the_basics():
    e = event()
    assert e.end_date is None
    assert e.last_day == dt.date(2026, 8, 4)
    assert e.attendees == []
    assert e.start_time is None and e.location is None


def test_times_parse_from_hh_mm_strings():
    e = event(start_time="20:00", end_time="22:30")
    assert e.start_time == dt.time(20, 0)
    assert e.end_time == dt.time(22, 30)


def test_a_multi_day_event_reports_its_last_day():
    e = event(kind="travel", end_date=dt.date(2026, 8, 6))
    assert e.last_day == dt.date(2026, 8, 6)


def test_an_event_cannot_end_before_it_starts():
    with pytest.raises(ValidationError, match="end_date must not be before date"):
        event(end_date=dt.date(2026, 8, 3))


@pytest.mark.parametrize("end", ["20:00", "19:59"])
def test_a_single_day_event_must_end_after_it_starts(end: str):
    with pytest.raises(ValidationError, match="end_time must be after start_time"):
        event(start_time="20:00", end_time=end)


def test_times_may_cross_midnight_only_on_multi_day_events():
    e = event(kind="offsite", end_date=dt.date(2026, 8, 5), start_time="22:00", end_time="09:00")
    assert e.end_time == dt.time(9, 0)


def test_unknown_kinds_are_rejected():
    with pytest.raises(ValidationError):
        event(kind="party")


def test_models_are_closed_and_frozen():
    with pytest.raises(ValidationError):
        event(surprise=1)
    with pytest.raises(ValidationError):
        event().title = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        EmployeeRecord(
            id="P1",
            name="A",
            employee_id="EMP1",
            grade="L1",
            base_city="Pune",
            surprise=1,  # type: ignore[call-arg]
        )


def test_seed_events_carry_their_owner():
    seed = SeedEvent(
        owner="P001", id="E1", title="Dinner", date=dt.date(2026, 8, 4), kind="client_dinner"
    )
    assert seed.owner == "P001"
    assert "owner" not in event().model_dump()


def test_employee_optional_fields_default_to_none():
    e = EmployeeRecord(id="P1", name="A", employee_id="EMP1", grade="L1", base_city="Pune")
    assert (e.base_state_code, e.manager_id, e.email) == (None, None, None)
