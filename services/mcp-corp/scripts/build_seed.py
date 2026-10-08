"""Build ``seed/employees.json`` and ``seed/calendar.json`` for the mock corporate systems.

Deterministic and idempotent: the same inputs always produce byte-identical files, so the output
is committed and ``--check`` can prove it is current (the unit tests do).

Inputs (synthetic data only):

* ``data/synth/golden/personas.json``: the five golden-set personas P001-P005 (kept as they are).
* ``data/synth/golden/truth/*.json``: the ground truth of the golden receipts.

Employees: the five personas, plus three clearly fictional demo personas for the hosted demo.

Calendar:

* For ~60% of the ``client_entertainment`` receipts, chosen by a stable hash of the document id,
  a ``client_dinner`` event on the receipt's date, in the receipt's venue, with 2-4 fictional
  attendees. The other ~40% get NO event on purpose, so the agent still has something to ask.
* For every trip (``trip_id``) one ``travel`` event spanning the dates of the trip's receipts.
* A few hand-written events for the demo personas (``DEMO_EVENTS``).

Usage::

    python services/mcp-corp/scripts/build_seed.py           # write the seed files
    python services/mcp-corp/scripts/build_seed.py --check   # exit 1 if they are stale
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
GOLDEN_DIR = REPO_ROOT / "data" / "synth" / "golden"
SEED_DIR = SERVICE_ROOT / "seed"

GOLDEN_PERSONAS = ("P001", "P002", "P003", "P004", "P005")
APPROVER_ID = "DEMO-RAVI"

# Share of client dinners that get a calendar event. The salt is part of the "random" choice and
# was picked so the seven golden dinners of P001-P005 split 4 with an event / 3 without.
DINNER_SHARE_PERCENT = 60
HASH_SALT = "claimpilot-calendar-v7"

DEMO_EMPLOYEES: tuple[dict[str, Any], ...] = (
    {
        "id": "DEMO-ASHA",
        "name": "Asha Menon",
        "employee_id": "EMP90001",
        "grade": "L3",
        "base_city": "Pune",
        "base_state_code": "27",
        "manager_id": APPROVER_ID,
        "email": "asha.menon@example.com",
    },
    {
        "id": APPROVER_ID,
        "name": "Ravi Iyer",
        "employee_id": "EMP90002",
        "grade": "L5",
        "base_city": "Bengaluru",
        "base_state_code": "29",
        "manager_id": None,
        "email": "ravi.iyer@example.com",
    },
    {
        "id": "DEMO-MEERA",
        "name": "Meera Shah",
        "employee_id": "EMP90003",
        "grade": "L2",
        "base_city": "Mumbai",
        "base_state_code": "27",
        "manager_id": APPROVER_ID,
        "email": "meera.shah@example.com",
    },
)

# Fictional people and client companies; nothing here is a real person or business.
FIRST_NAMES = (
    "Neha",
    "Rohan",
    "Priya",
    "Arjun",
    "Kavya",
    "Vikram",
    "Anita",
    "Sameer",
    "Divya",
    "Karan",
    "Meenal",
    "Tarun",
    "Ishita",
    "Naveen",
    "Pooja",
    "Rahul",
)
LAST_NAMES = (
    "Rao",
    "Kapoor",
    "Nair",
    "Bhatt",
    "Desai",
    "Kulkarni",
    "Sethi",
    "Joshi",
    "Banerjee",
    "Chawla",
    "Pillai",
    "Varma",
)
CLIENT_COMPANIES = (
    "Kestrel Logistics",
    "Blue Lotus Foods",
    "Harbour Analytics",
    "Zephyr Textiles",
    "Maple Ridge Pharma",
    "Sunbeam Retail",
    "Cobalt Mobility",
    "Juniper Health",
)

# Hand-written events for the demo personas, around the submission deadline of 12 Oct 2026.
DEMO_EVENTS: tuple[dict[str, Any], ...] = (
    {
        "owner": "DEMO-ASHA",
        "kind": "client_meeting",
        "title": "Quarterly review with Kestrel Logistics",
        "date": "2026-10-06",
        "start_time": "11:00",
        "end_time": "12:30",
        "location": "Kestrel Logistics office, Pune",
        "attendees": ["Neha Rao (Kestrel Logistics)", "Rohan Kapoor (Kestrel Logistics)"],
    },
    {
        "owner": "DEMO-ASHA",
        "kind": "client_dinner",
        "title": "Dinner with Kestrel Logistics",
        "date": "2026-10-06",
        "start_time": "20:00",
        "end_time": "22:00",
        "location": "Saffron Terrace, Pune",
        "attendees": [
            "Neha Rao (Kestrel Logistics)",
            "Rohan Kapoor (Kestrel Logistics)",
            "Priya Nair (Kestrel Logistics)",
        ],
    },
    {
        "owner": "DEMO-ASHA",
        "kind": "travel",
        "title": "Client visit to Mumbai",
        "date": "2026-10-09",
        "end_date": "2026-10-10",
        "location": "Mumbai",
    },
    {
        "owner": "DEMO-RAVI",
        "kind": "client_meeting",
        "title": "Annual contract review with Blue Lotus Foods",
        "date": "2026-10-07",
        "start_time": "15:00",
        "end_time": "16:00",
        "location": "Blue Lotus Foods office, Bengaluru",
        "attendees": ["Anita Desai (Blue Lotus Foods)", "Sameer Joshi (Blue Lotus Foods)"],
    },
    {
        "owner": "DEMO-RAVI",
        "kind": "offsite",
        "title": "Leadership offsite",
        "date": "2026-10-14",
        "end_date": "2026-10-15",
        "location": "Lakeview Resort, Lonavala",
        "attendees": ["Asha Menon", "Meera Shah"],
    },
    {
        "owner": "DEMO-MEERA",
        "kind": "training",
        "title": "Data privacy essentials",
        "date": "2026-10-07",
        "end_date": "2026-10-08",
        "location": "Mumbai Learning Centre",
    },
    {
        "owner": "DEMO-MEERA",
        "kind": "client_dinner",
        "title": "Dinner with Harbour Analytics",
        "date": "2026-10-08",
        "start_time": "20:00",
        "end_time": "22:00",
        "location": "Harbour Lights Bistro, Mumbai",
        "attendees": ["Kavya Pillai (Harbour Analytics)", "Tarun Varma (Harbour Analytics)"],
    },
)

KIND_CODES = {
    "client_meeting": "MTG",
    "client_dinner": "DIN",
    "offsite": "OFF",
    "training": "TRN",
    "travel": "TRV",
}
EVENT_FIELDS = (
    "owner",
    "id",
    "kind",
    "title",
    "date",
    "end_date",
    "start_time",
    "end_time",
    "location",
    "attendees",
)


# -- deterministic choices -----------------------------------------------------------------------


def hash_int(token: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{HASH_SALT}:{token}".encode()).digest()[:8], "big")


def wants_dinner_event(doc_id: str) -> bool:
    """Whether this client-dinner receipt gets a calendar event (stable per document id)."""
    return hash_int(doc_id) % 100 < DINNER_SHARE_PERCENT


def pick_client(doc_id: str) -> tuple[str, list[str]]:
    """A fictional client company and 2-4 fictional people from it."""
    company = CLIENT_COMPANIES[hash_int(f"{doc_id}:company") % len(CLIENT_COMPANIES)]
    wanted = 2 + hash_int(f"{doc_id}:count") % 3
    attendees: list[str] = []
    attempt = 0
    while len(attendees) < wanted:
        first = FIRST_NAMES[hash_int(f"{doc_id}:first:{attempt}") % len(FIRST_NAMES)]
        last = LAST_NAMES[hash_int(f"{doc_id}:last:{attempt}") % len(LAST_NAMES)]
        person = f"{first} {last} ({company})"
        if person not in attendees:
            attendees.append(person)
        attempt += 1
    return company, attendees


def dinner_window(receipt_time: str | None) -> tuple[str, str]:
    """A 2.5 hour window that contains the bill time; 20:00-22:30 when the bill has no time."""
    start = 20 * 60
    if receipt_time:
        try:
            hours, minutes = (int(part) for part in receipt_time.split(":")[:2])
        except ValueError:
            hours = minutes = None  # unparsable bill time: keep the default window
        if hours is not None and minutes is not None:
            start = min(max((hours * 60 + minutes - 75) // 30 * 30, 18 * 60 + 30), 21 * 60)
    return _clock(start), _clock(start + 150)


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# -- inputs --------------------------------------------------------------------------------------


def load_employees(golden_dir: Path) -> list[dict[str, Any]]:
    personas = {p["id"]: p for p in json.loads((golden_dir / "personas.json").read_text("utf-8"))}
    golden = [
        {
            "id": pid,
            "name": personas[pid]["name"],
            "employee_id": personas[pid]["employee_id"],
            "grade": personas[pid]["grade"],
            "base_city": personas[pid]["base_city"],
            "base_state_code": personas[pid]["base_state_code"],
            "manager_id": APPROVER_ID,
            "email": personas[pid]["email"],
        }
        for pid in GOLDEN_PERSONAS
    ]
    return golden + [dict(demo) for demo in DEMO_EMPLOYEES]


def load_truth(golden_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text("utf-8")) for path in sorted(golden_dir.glob("truth/*.json"))]


# -- events --------------------------------------------------------------------------------------


def dinner_events(truth: Sequence[dict[str, Any]], owners: set[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in truth:
        receipt = record["receipt"]
        if (
            record["category"] != "client_entertainment"
            or record["persona_id"] not in owners
            or not receipt.get("date")
            or not wants_dinner_event(record["id"])
        ):
            continue
        company, attendees = pick_client(record["id"])
        start, end = dinner_window(receipt.get("time"))
        venue = ", ".join(
            p for p in (receipt.get("merchant_name"), receipt.get("merchant_city")) if p
        )
        events.append(
            {
                "owner": record["persona_id"],
                "kind": "client_dinner",
                "title": f"Client dinner with {company}",
                "date": receipt["date"],
                "start_time": start,
                "end_time": end,
                "location": venue or None,
                "attendees": attendees,
            }
        )
    return events


def trip_destination(docs: Sequence[dict[str, Any]]) -> str | None:
    """Where the trip went: the hotel's city, else the outward ticket's destination."""
    for record in docs:
        if record["category"] == "accommodation" and record["receipt"].get("merchant_city"):
            return record["receipt"]["merchant_city"]
    tickets = sorted(
        (r for r in docs if r["receipt"].get("travel_to") and r["receipt"].get("date")),
        key=lambda r: (r["receipt"]["date"], r["id"]),
    )
    return tickets[0]["receipt"]["travel_to"] if tickets else None


def travel_events(truth: Sequence[dict[str, Any]], owners: set[str]) -> list[dict[str, Any]]:
    trips: dict[str, list[dict[str, Any]]] = {}
    for record in truth:
        if record.get("trip_id") and record["persona_id"] in owners:
            trips.setdefault(record["trip_id"], []).append(record)
    events: list[dict[str, Any]] = []
    for trip_id in sorted(trips):
        docs = trips[trip_id]
        dates = sorted(r["receipt"]["date"] for r in docs if r["receipt"].get("date"))
        if not dates:
            continue
        destination = trip_destination(docs)
        events.append(
            {
                "owner": docs[0]["persona_id"],
                "kind": "travel",
                "title": f"Business trip to {destination}" if destination else "Business trip",
                "date": dates[0],
                "end_date": dates[-1] if dates[-1] != dates[0] else None,
                "location": destination,
            }
        )
    return events


def finalize(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order events, give each a stable id and fix the key order of the JSON."""
    ordered = sorted(
        events,
        key=lambda e: (e["owner"], e["date"], e.get("start_time") or "", e["kind"], e["title"]),
    )
    seen: Counter[tuple[str, str, str]] = Counter()
    result: list[dict[str, Any]] = []
    for event in ordered:
        slot = (event["owner"], event["date"], event["kind"])
        seen[slot] += 1
        event_id = (
            f"CAL-{event['owner']}-{event['date'].replace('-', '')}-{KIND_CODES[event['kind']]}"
        )
        full = {
            **event,
            "id": event_id if seen[slot] == 1 else f"{event_id}-{seen[slot]}",
            "end_date": event.get("end_date"),
            "start_time": event.get("start_time"),
            "end_time": event.get("end_time"),
            "location": event.get("location"),
            "attendees": list(event.get("attendees", [])),
        }
        result.append({field: full[field] for field in EVENT_FIELDS})
    return result


def build(golden_dir: Path) -> dict[str, str]:
    """The seed files as ``{file name: text}``."""
    employees = load_employees(golden_dir)
    owners = {e["id"] for e in employees}
    truth = load_truth(golden_dir)
    events = finalize(
        dinner_events(truth, owners) + travel_events(truth, owners) + list(DEMO_EVENTS)
    )
    return {
        "employees.json": _dump(employees),
        "calendar.json": _dump(events),
    }


def _dump(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# -- command line --------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the mcp-corp seed files.")
    parser.add_argument("--golden-dir", type=Path, default=GOLDEN_DIR)
    parser.add_argument("--out-dir", type=Path, default=SEED_DIR)
    parser.add_argument("--check", action="store_true", help="exit 1 if the seed is out of date")
    args = parser.parse_args(argv)

    files = build(args.golden_dir)
    stale = [
        name
        for name, text in files.items()
        if not (args.out_dir / name).exists() or (args.out_dir / name).read_text("utf-8") != text
    ]
    if args.check:
        for name in stale:
            print(f"{args.out_dir / name} is out of date; run build_seed.py", file=sys.stderr)
        return 1 if stale else 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        # newline="" keeps the "\n" endings on every platform (the repo is LF)
        (args.out_dir / name).write_text(text, encoding="utf-8", newline="")
        print(f"wrote {args.out_dir / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
