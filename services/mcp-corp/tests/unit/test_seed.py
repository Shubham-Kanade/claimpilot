"""The committed seed (employees.json, calendar.json) and the script that builds it."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import build_seed
import pytest
from pydantic import TypeAdapter

from claimpilot_mcp_corp.models import EmployeeRecord, SeedEvent

SEED_DIR = Path(__file__).resolve().parents[2] / "seed"
GOLDEN_DIR = build_seed.GOLDEN_DIR
HAS_GOLDEN = (GOLDEN_DIR / "personas.json").exists()
needs_golden = pytest.mark.skipif(not HAS_GOLDEN, reason="data/synth/golden is not available")

GOLDEN_IDS = ("P001", "P002", "P003", "P004", "P005")
DEMO_IDS = ("DEMO-ASHA", "DEMO-RAVI", "DEMO-MEERA")


def read(name: str) -> Any:
    return json.loads((SEED_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def employees() -> list[EmployeeRecord]:
    return TypeAdapter(list[EmployeeRecord]).validate_python(read("employees.json"))


@pytest.fixture(scope="module")
def events() -> list[SeedEvent]:
    return TypeAdapter(list[SeedEvent]).validate_python(read("calendar.json"))


def truth_records() -> list[dict[str, Any]]:
    return build_seed.load_truth(GOLDEN_DIR)


# -- employees ----------------------------------------------------------------------------------


def test_the_directory_holds_five_golden_and_three_demo_personas(employees: list[EmployeeRecord]):
    assert [e.id for e in employees] == [*GOLDEN_IDS, *DEMO_IDS]


def test_identifiers_are_unique_and_well_formed(employees: list[EmployeeRecord]):
    assert len({e.id for e in employees}) == len(employees)
    assert len({e.employee_id for e in employees}) == len(employees)
    for e in employees:
        assert re.fullmatch(r"EMP\d{5}", e.employee_id), e
        assert e.grade in {"L1", "L2", "L3", "L4", "L5"}, e
        assert e.base_state_code and re.fullmatch(r"\d{2}", e.base_state_code), e


def test_every_address_is_on_a_reserved_example_domain(employees: list[EmployeeRecord]):
    for e in employees:
        assert e.email is not None and e.email.endswith("@example.com"), e


@needs_golden
def test_golden_personas_are_kept_as_they_are(employees: list[EmployeeRecord]):
    personas = {p["id"]: p for p in json.loads((GOLDEN_DIR / "personas.json").read_text("utf-8"))}
    by_id = {e.id: e for e in employees}
    for pid in GOLDEN_IDS:
        mine, golden = by_id[pid], personas[pid]
        for field in ("name", "employee_id", "grade", "base_city", "base_state_code", "email"):
            assert getattr(mine, field) == golden[field], (pid, field)


def test_the_demo_personas(employees: list[EmployeeRecord]):
    by_id = {e.id: e for e in employees}
    asha, ravi, meera = by_id["DEMO-ASHA"], by_id["DEMO-RAVI"], by_id["DEMO-MEERA"]
    assert (asha.name, asha.grade, asha.base_city) == ("Asha Menon", "L3", "Pune")
    assert (ravi.name, ravi.grade, ravi.base_city) == ("Ravi Iyer", "L5", "Bengaluru")
    assert (meera.name, meera.grade, meera.base_city) == ("Meera Shah", "L2", "Mumbai")


def test_ravi_is_the_approver_for_everyone_else(employees: list[EmployeeRecord]):
    by_id = {e.id: e for e in employees}
    assert by_id["DEMO-RAVI"].manager_id is None
    assert by_id["DEMO-RAVI"].grade == "L5"
    for e in employees:
        if e.id != "DEMO-RAVI":
            assert e.manager_id == "DEMO-RAVI", e.id


# -- calendar -----------------------------------------------------------------------------------


def test_events_are_unique_and_belong_to_known_employees(
    employees: list[EmployeeRecord], events: list[SeedEvent]
):
    assert len({e.id for e in events}) == len(events)
    known = {e.id for e in employees}
    assert {e.owner for e in events} <= known


def test_every_kind_of_event_appears(events: list[SeedEvent]):
    assert {e.kind for e in events} == {
        "client_meeting",
        "client_dinner",
        "offsite",
        "training",
        "travel",
    }


def test_client_dinners_and_meetings_have_people_and_times(events: list[SeedEvent]):
    for e in events:
        if e.kind in {"client_dinner", "client_meeting"}:
            assert e.start_time and e.end_time and e.location, e.id
            assert e.end_date is None, e.id
            assert 2 <= len(e.attendees) <= 4, e.id
            companies = {re.fullmatch(r"[A-Z][a-z]+ [A-Z][a-z]+ \((.+)\)", a) for a in e.attendees}
            assert None not in companies, e.attendees
            assert len({m.group(1) for m in companies if m}) == 1, e.attendees


def test_trips_offsites_and_training_are_all_day_with_a_place(events: list[SeedEvent]):
    for e in events:
        if e.kind in {"travel", "offsite", "training"}:
            assert e.start_time is None and e.end_time is None, e.id
            assert e.location, e.id
            assert e.end_date is None or e.end_date > e.date, e.id


def test_event_ids_say_whose_calendar_and_what_day(events: list[SeedEvent]):
    for e in events:
        assert e.id.startswith(f"CAL-{e.owner}-{e.date:%Y%m%d}-"), e.id


def test_the_seed_files_use_lf_line_endings_and_end_with_a_newline():
    for name in ("employees.json", "calendar.json"):
        raw = (SEED_DIR / name).read_bytes()
        assert raw.endswith(b"\n") and b"\r" not in raw, name


# -- the calendar follows the golden receipts ---------------------------------------------------


def golden_dinners() -> list[dict[str, Any]]:
    return [
        r
        for r in truth_records()
        if r["category"] == "client_entertainment"
        and r["persona_id"] in GOLDEN_IDS
        and r["receipt"].get("date")
    ]


def dinner_events_of(events: list[SeedEvent], persona: str, day: str) -> list[SeedEvent]:
    return [
        e
        for e in events
        if e.owner == persona and e.kind == "client_dinner" and e.date.isoformat() == day
    ]


@needs_golden
def test_a_dinner_gets_an_event_exactly_when_its_document_hashes_in(events: list[SeedEvent]):
    dinners = golden_dinners()
    assert dinners  # the golden set has client dinners
    for record in dinners:
        found = dinner_events_of(events, record["persona_id"], record["receipt"]["date"])
        assert bool(found) == build_seed.wants_dinner_event(record["id"]), record["id"]


@needs_golden
def test_a_dinner_event_sits_at_the_receipts_venue_and_covers_its_time(events: list[SeedEvent]):
    for record in golden_dinners():
        for event in dinner_events_of(events, record["persona_id"], record["receipt"]["date"]):
            receipt = record["receipt"]
            assert event.location == f"{receipt['merchant_name']}, {receipt['merchant_city']}"
            hours, minutes = (int(p) for p in receipt["time"].split(":"))
            billed = hours * 60 + minutes
            assert event.start_time and event.end_time
            start = event.start_time.hour * 60 + event.start_time.minute
            end = event.end_time.hour * 60 + event.end_time.minute
            assert start <= billed <= end, (record["id"], receipt["time"], event.start_time)


@needs_golden
def test_about_sixty_percent_of_golden_dinners_have_an_event(events: list[SeedEvent]):
    dinners = golden_dinners()
    with_event = [
        r for r in dinners if dinner_events_of(events, r["persona_id"], r["receipt"]["date"])
    ]
    share = len(with_event) / len(dinners)
    assert 0.4 <= share <= 0.8, (len(with_event), len(dinners))
    assert (len(with_event), len(dinners)) == (4, 7)


@needs_golden
def test_some_dinners_deliberately_have_no_event_so_the_agent_has_to_ask(
    events: list[SeedEvent],
):
    without = [
        r
        for r in golden_dinners()
        if not dinner_events_of(events, r["persona_id"], r["receipt"]["date"])
    ]
    assert len(without) >= 2
    assert len({r["persona_id"] for r in without}) >= 2


@needs_golden
def test_every_trip_has_a_travel_event_spanning_its_dates(events: list[SeedEvent]):
    trips: dict[str, list[dict[str, Any]]] = {}
    for record in truth_records():
        if record.get("trip_id") and record["persona_id"] in GOLDEN_IDS:
            trips.setdefault(record["trip_id"], []).append(record)
    assert trips
    for trip_id, docs in trips.items():
        dates = sorted(d["receipt"]["date"] for d in docs if d["receipt"].get("date"))
        owner = docs[0]["persona_id"]
        found = [
            e
            for e in events
            if e.owner == owner and e.kind == "travel" and e.date.isoformat() == dates[0]
        ]
        assert len(found) == 1, trip_id
        assert (found[0].end_date or found[0].date).isoformat() == dates[-1], trip_id


@needs_golden
def test_only_directory_personas_get_events(events: list[SeedEvent]):
    assert {e.owner for e in events} <= {*GOLDEN_IDS, *DEMO_IDS}
    # P904 and P919 each have a client dinner in the golden set but are not in the directory
    assert not [e for e in events if e.owner in {"P904", "P919"}]


# -- the builder --------------------------------------------------------------------------------


@needs_golden
def test_the_committed_seed_is_what_the_builder_produces():
    built = build_seed.build(GOLDEN_DIR)
    for name, text in built.items():
        assert (SEED_DIR / name).read_text(encoding="utf-8") == text, f"{name} is stale"
    assert build_seed.main(["--check"]) == 0


@needs_golden
def test_the_builder_is_deterministic():
    assert build_seed.build(GOLDEN_DIR) == build_seed.build(GOLDEN_DIR)


@needs_golden
def test_running_the_builder_twice_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture):
    assert build_seed.main(["--out-dir", str(tmp_path)]) == 0
    first = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert set(first) == {"employees.json", "calendar.json"}
    assert build_seed.main(["--out-dir", str(tmp_path)]) == 0
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == first
    assert "wrote" in capsys.readouterr().out
    assert build_seed.main(["--check", "--out-dir", str(tmp_path)]) == 0


@needs_golden
def test_check_fails_on_a_stale_or_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture):
    assert build_seed.main(["--check", "--out-dir", str(tmp_path)]) == 1  # nothing written yet
    build_seed.main(["--out-dir", str(tmp_path)])
    (tmp_path / "calendar.json").write_text("[]\n", encoding="utf-8")
    capsys.readouterr()
    assert build_seed.main(["--check", "--out-dir", str(tmp_path)]) == 1
    assert "calendar.json is out of date" in capsys.readouterr().err
    assert (tmp_path / "calendar.json").read_text(
        encoding="utf-8"
    ) == "[]\n"  # --check never writes


# -- the builder's pieces, on made-up data ------------------------------------------------------


def make_truth(doc_id: str, **receipt: Any) -> dict[str, Any]:
    fields = {"category": "client_entertainment", "persona_id": "P001", "trip_id": None}
    return {"id": doc_id, "receipt": receipt, **fields}


def test_the_hash_choice_is_stable_and_close_to_the_target_share():
    assert build_seed.wants_dinner_event("s42-0015") is True
    assert build_seed.wants_dinner_event("s42-0015") is True
    assert build_seed.wants_dinner_event("s42-0010") is False
    chosen = sum(build_seed.wants_dinner_event(f"doc-{i:05d}") for i in range(4000))
    assert 0.57 <= chosen / 4000 <= 0.63


def test_attendees_are_two_to_four_unique_people_from_one_company():
    sizes = Counter()
    for i in range(300):
        company, people = build_seed.pick_client(f"doc-{i}")
        assert company in build_seed.CLIENT_COMPANIES
        assert 2 <= len(people) <= 4 and len(set(people)) == len(people)
        assert all(p.endswith(f"({company})") for p in people)
        assert build_seed.pick_client(f"doc-{i}") == (company, people)  # deterministic
        sizes[len(people)] += 1
    assert set(sizes) == {2, 3, 4}


@pytest.mark.parametrize(
    ("bill_time", "window"),
    [
        (None, ("20:00", "22:30")),
        ("garbage", ("20:00", "22:30")),
        ("19:01", ("18:30", "21:00")),  # early bills are clamped to an 18:30 start
        ("21:26", ("20:00", "22:30")),
        ("22:28", ("21:00", "23:30")),  # late bills are clamped to a 21:00 start
        ("22:59", ("21:00", "23:30")),
    ],
)
def test_dinner_windows(bill_time: str | None, window: tuple[str, str]):
    assert build_seed.dinner_window(bill_time) == window


def test_dinners_without_a_date_or_outside_the_directory_get_no_event():
    truth = [
        make_truth("a", merchant_name="Cafe", merchant_city="Pune"),  # no date
        make_truth("b", date="2026-08-04", merchant_name="Cafe", merchant_city="Pune"),
        {**make_truth("c", date="2026-08-05"), "persona_id": "P777"},  # not in the directory
        {**make_truth("d", date="2026-08-06"), "category": "meals"},  # not a client dinner
    ]
    chosen = build_seed.dinner_events(truth, {"P001"})
    expected = 1 if build_seed.wants_dinner_event("b") else 0
    assert len(chosen) == expected
    assert all(e["date"] == "2026-08-04" and e["location"] == "Cafe, Pune" for e in chosen)


def test_a_dinner_without_a_venue_has_no_location(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(build_seed, "wants_dinner_event", lambda doc_id: True)
    events = build_seed.dinner_events([make_truth("a", date="2026-08-04")], {"P001"})
    assert [e["location"] for e in events] == [None]


def test_trip_destination_prefers_the_hotel_then_the_outward_ticket():
    hotel = {"category": "accommodation", "id": "h", "receipt": {"merchant_city": "Pune"}}
    outward = {
        "category": "travel_domestic",
        "id": "t1",
        "receipt": {"date": "2026-08-01", "travel_to": "Goa"},
    }
    back = {
        "category": "travel_domestic",
        "id": "t2",
        "receipt": {"date": "2026-08-03", "travel_to": "Delhi"},
    }
    assert build_seed.trip_destination([back, outward, hotel]) == "Pune"
    assert build_seed.trip_destination([back, outward]) == "Goa"
    assert build_seed.trip_destination([{"category": "meals", "id": "m", "receipt": {}}]) is None


def test_travel_events_span_the_trip_and_skip_undated_trips():
    def doc(identifier: str, trip: str | None, day: str | None, **extra: Any) -> dict[str, Any]:
        receipt = {"date": day, **extra}
        return {
            "id": identifier,
            "category": "meals",
            "persona_id": "P001",
            "trip_id": trip,
            "receipt": receipt,
        }

    truth = [
        doc("1", "T1", "2026-08-01"),
        doc("2", "T1", "2026-08-03"),
        doc("3", "T2", "2026-08-10", merchant_city="Pune"),  # a one-day trip
        doc("4", "T3", None),  # no dates at all
        doc("5", None, "2026-08-20"),  # not part of a trip
        {**doc("6", "T4", "2026-08-21"), "persona_id": "P777"},  # not in the directory
    ]
    events = build_seed.travel_events(truth, {"P001"})
    assert [(e["date"], e["end_date"], e["title"]) for e in events] == [
        ("2026-08-01", "2026-08-03", "Business trip"),
        ("2026-08-10", None, "Business trip"),
    ]


def test_finalize_orders_events_and_makes_ids_unique():
    base = {"owner": "P001", "kind": "client_dinner", "title": "Dinner", "date": "2026-08-04"}
    events = build_seed.finalize(
        [
            {**base, "start_time": "21:00", "end_time": "23:00"},
            {**base, "start_time": "19:00", "end_time": "20:00", "title": "Early dinner"},
            {**base, "date": "2026-08-01", "kind": "travel"},
        ]
    )
    assert [e["id"] for e in events] == [
        "CAL-P001-20260801-TRV",
        "CAL-P001-20260804-DIN",
        "CAL-P001-20260804-DIN-2",
    ]
    assert [e["start_time"] for e in events] == [None, "19:00", "21:00"]
    assert list(events[0]) == list(build_seed.EVENT_FIELDS)
