"""Directory: employee lookup by both ids, calendar filtering, validation of the seed."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from claimpilot_mcp_corp.directory import (
    MAX_RANGE_DAYS,
    Directory,
    EmployeeNotFoundError,
    InvalidRangeError,
)
from claimpilot_mcp_corp.models import EmployeeRecord, SeedEvent


def date(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def person(identifier: str, hr: str, **overrides: Any) -> EmployeeRecord:
    base: dict[str, Any] = {
        "id": identifier,
        "name": f"Person {identifier}",
        "employee_id": hr,
        "grade": "L3",
        "base_city": "Pune",
    }
    return EmployeeRecord(**{**base, **overrides})


def seed_event(owner: str, identifier: str, day: str, **overrides: Any) -> SeedEvent:
    base: dict[str, Any] = {
        "owner": owner,
        "id": identifier,
        "title": f"Event {identifier}",
        "date": date(day),
        "kind": "client_meeting",
    }
    return SeedEvent(**{**base, **overrides})


# -- lookup -------------------------------------------------------------------------------------


def test_finds_an_employee_by_directory_id(directory: Directory):
    employee = directory.get("P001")
    assert employee.name == "Advika Hayer"
    assert employee.employee_id == "EMP85968"
    assert employee.grade == "L4"
    assert employee.base_city == "Hyderabad"
    assert employee.base_state_code == "36"


def test_finds_the_same_employee_by_hr_number(directory: Directory):
    assert directory.get("EMP85968") == directory.get("P001")


@pytest.mark.parametrize("spelling", ["p001", " P001 ", "emp85968", "\tEmp85968\n"])
def test_lookup_ignores_case_and_surrounding_whitespace(directory: Directory, spelling: str):
    assert directory.get(spelling).id == "P001"


def test_demo_personas_are_found_by_both_ids(directory: Directory):
    assert directory.get("DEMO-ASHA").name == "Asha Menon"
    assert directory.get("demo-ravi").employee_id == directory.get("EMP90002").employee_id


@pytest.mark.parametrize("identifier", ["P999", "EMP00000", "", "  ", "Advika Hayer"])
def test_unknown_employees_are_reported(directory: Directory, identifier: str):
    with pytest.raises(EmployeeNotFoundError, match="no employee with directory id or HR number"):
        directory.get(identifier)


def test_lists_everyone_in_seed_order(directory: Directory):
    ids = [e.id for e in directory.employees()]
    assert ids == ["P001", "P002", "P003", "P004", "P005", "DEMO-ASHA", "DEMO-RAVI", "DEMO-MEERA"]


def test_the_employee_list_is_a_copy(directory: Directory):
    directory.employees().clear()
    assert len(directory.employees()) == 8


# -- calendar -----------------------------------------------------------------------------------


def test_a_golden_client_dinner_is_found_with_its_attendees(directory: Directory):
    events = directory.events("P002", date("2026-08-04"), date("2026-08-04"))
    assert [e.kind for e in events] == ["client_dinner"]
    dinner = events[0]
    assert dinner.date == date("2026-08-04")
    assert dinner.location == "Chandni Cafe, Bhubaneswar"
    assert 2 <= len(dinner.attendees) <= 4
    assert dinner.start_time is not None and dinner.end_time is not None


def test_events_come_back_oldest_first(directory: Directory):
    events = directory.events("P002", date("2026-08-01"), date("2026-08-31"))
    assert [(e.kind, e.date.isoformat()) for e in events] == [
        ("client_dinner", "2026-08-04"),
        ("travel", "2026-08-12"),
        ("travel", "2026-08-18"),
    ]


def test_the_range_is_inclusive_at_both_ends(directory: Directory):
    assert directory.events("P002", date("2026-08-04"), date("2026-08-04"))
    assert directory.events("P002", date("2026-08-03"), date("2026-08-04"))
    assert directory.events("P002", date("2026-08-04"), date("2026-08-05"))
    assert not directory.events("P002", date("2026-08-05"), date("2026-08-11"))
    assert not directory.events("P002", date("2026-07-01"), date("2026-08-03"))


def test_multi_day_events_match_any_day_they_span(directory: Directory):
    # P002's trip runs 12-14 Aug
    for day in ("2026-08-12", "2026-08-13", "2026-08-14"):
        events = directory.events("P002", date(day), date(day))
        assert [e.kind for e in events] == ["travel"], day
    assert not directory.events("P002", date("2026-08-15"), date("2026-08-15"))
    # a window that starts inside the trip and ends after it still sees it
    assert directory.events("P002", date("2026-08-14"), date("2026-08-17"))[0].kind == "travel"


def test_calendars_are_per_employee(directory: Directory):
    assert directory.events("P001", date("2026-08-01"), date("2026-08-31")) == []
    for owner in ("P001", "P003", "P004", "P005"):
        for event in directory.events(owner, date("2026-01-01"), date("2026-12-31")):
            assert event.id.startswith(f"CAL-{owner}-")


def test_the_calendar_accepts_the_hr_number_too(directory: Directory):
    by_id = directory.events("P002", date("2026-08-01"), date("2026-08-31"))
    assert directory.events("EMP74851", date("2026-08-01"), date("2026-08-31")) == by_id


def test_calendar_events_do_not_expose_their_owner(directory: Directory):
    event = directory.events("P002", date("2026-08-04"), date("2026-08-04"))[0]
    assert "owner" not in event.model_dump()
    assert type(event).__name__ == "CalendarEvent"


def test_every_kind_of_event_can_be_found(directory: Directory):
    kinds = set()
    for employee in directory.employees():
        for event in directory.events(employee.id, date("2026-01-01"), date("2026-12-31")):
            kinds.add(event.kind)
    assert kinds == {"client_meeting", "client_dinner", "offsite", "training", "travel"}


def test_unknown_employee_in_a_calendar_search(directory: Directory):
    with pytest.raises(EmployeeNotFoundError):
        directory.events("P999", date("2026-08-01"), date("2026-08-02"))


def test_a_backwards_range_is_rejected(directory: Directory):
    with pytest.raises(InvalidRangeError, match="start_date must not be after end_date"):
        directory.events("P002", date("2026-08-05"), date("2026-08-04"))


def test_the_range_is_capped(directory: Directory):
    start = date("2026-01-01")
    directory.events("P002", start, start + dt.timedelta(days=MAX_RANGE_DAYS))  # allowed
    with pytest.raises(InvalidRangeError, match=str(MAX_RANGE_DAYS)):
        directory.events("P002", start, start + dt.timedelta(days=MAX_RANGE_DAYS + 1))


def test_event_count(directory: Directory):
    assert directory.event_count == 17


# -- validation of the data ---------------------------------------------------------------------


def test_duplicate_directory_ids_are_refused():
    with pytest.raises(ValueError, match="duplicate employee identifier 'p1'"):
        Directory([person("P1", "EMP1"), person("p1", "EMP2")])


def test_duplicate_hr_numbers_are_refused():
    with pytest.raises(ValueError, match="duplicate employee identifier"):
        Directory([person("P1", "EMP1"), person("P2", "EMP1")])


def test_a_directory_id_may_not_equal_another_hr_number():
    with pytest.raises(ValueError, match="duplicate employee identifier"):
        Directory([person("P1", "EMP1"), person("EMP1", "EMP2")])


def test_managers_must_exist():
    with pytest.raises(ValueError, match="unknown manager_id 'GHOST'"):
        Directory([person("P1", "EMP1", manager_id="GHOST")])
    Directory([person("P1", "EMP1", manager_id="P2"), person("P2", "EMP2")])  # fine


def test_event_owners_must_exist():
    with pytest.raises(ValueError, match="unknown owner 'GHOST'"):
        Directory([person("P1", "EMP1")], [seed_event("GHOST", "E1", "2026-08-04")])


def test_event_ids_must_be_unique():
    events = [seed_event("P1", "E1", "2026-08-04"), seed_event("P1", "E1", "2026-08-05")]
    with pytest.raises(ValueError, match="duplicate event id 'E1'"):
        Directory([person("P1", "EMP1")], events)


def test_event_owners_may_be_given_by_hr_number():
    directory = Directory([person("P1", "EMP1")], [seed_event("emp1", "E1", "2026-08-04")])
    assert [e.id for e in directory.events("P1", date("2026-08-04"), date("2026-08-04"))] == ["E1"]


def test_same_day_events_are_ordered_by_start_time_then_id():
    events = [
        seed_event("P1", "B", "2026-08-04", start_time="20:00", end_time="21:00"),
        seed_event("P1", "A", "2026-08-04", start_time="09:00", end_time="10:00"),
        seed_event("P1", "C", "2026-08-04"),  # all-day sorts first (no start time)
    ]
    directory = Directory([person("P1", "EMP1")], events)
    found = directory.events("P1", date("2026-08-04"), date("2026-08-04"))
    assert [e.id for e in found] == ["C", "A", "B"]


# -- loading ------------------------------------------------------------------------------------


def write_seed(folder: Path, employees: object, calendar: object) -> Path:
    (folder / "employees.json").write_text(json.dumps(employees), encoding="utf-8")
    (folder / "calendar.json").write_text(json.dumps(calendar), encoding="utf-8")
    return folder


def test_load_reads_both_files(tmp_path: Path):
    employees = [person("P1", "EMP1").model_dump()]
    calendar = [json.loads(seed_event("P1", "E1", "2026-08-04").model_dump_json())]
    directory = Directory.load(write_seed(tmp_path, employees, calendar))
    assert directory.get("P1").name == "Person P1"
    assert directory.event_count == 1


def test_load_reports_a_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match=r"employees\.json"):
        Directory.load(tmp_path)
    (tmp_path / "employees.json").write_text("[]", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match=r"calendar\.json"):
        Directory.load(tmp_path)


def test_load_validates_the_content(tmp_path: Path):
    bad_employee = [{"id": "P1", "name": "A"}]  # missing required fields
    with pytest.raises(ValidationError):
        Directory.load(write_seed(tmp_path, bad_employee, []))
    bad_event = [{"owner": "P1", "id": "E1", "title": "x", "date": "not-a-date", "kind": "travel"}]
    with pytest.raises(ValidationError):
        Directory.load(write_seed(tmp_path, [person("P1", "EMP1").model_dump()], bad_event))
