from __future__ import annotations

from datetime import date

import pytest

from claimpilot.domain import Claim, ClaimMode
from claimpilot.domain.claims import Employee
from claimpilot.ports import CalendarEvent, FakeFinance, StaticCalendar, StaticDirectory

ASHA = Employee(id="P001", name="Asha", employee_id="E1", grade="L3", base_city="Pune")
CLAIM = Claim(
    id="clm-1", employee_id="P001", title="t", mode=ClaimMode.event, document_ids=["d"], total=10.0
)


def event(day: int, end: int | None = None, **extra) -> CalendarEvent:
    return CalendarEvent(
        id=f"e{day}",
        title="x",
        date=date(2026, 8, day),
        end_date=date(2026, 8, end) if end else None,
        **extra,
    )


def test_event_overlap_is_inclusive_and_handles_multi_day_events():
    single, trip = event(14), event(12, 14, kind="travel")
    assert single.last_day == date(2026, 8, 14) and trip.last_day == date(2026, 8, 14)
    assert single.overlaps(date(2026, 8, 14), date(2026, 8, 14))
    assert not single.overlaps(date(2026, 8, 15), date(2026, 8, 20))
    assert trip.overlaps(date(2026, 8, 13), date(2026, 8, 13))  # a day in the middle
    assert trip.overlaps(date(2026, 8, 14), date(2026, 8, 20))  # the last day counts
    assert not trip.overlaps(date(2026, 8, 1), date(2026, 8, 11))


async def test_static_calendar_returns_events_overlapping_the_range():
    calendar = StaticCalendar({"P001": [event(14), event(12, 14), event(20)]})
    found = await calendar.events("P001", date(2026, 8, 13), date(2026, 8, 15))
    assert sorted(e.id for e in found) == ["e12", "e14"]
    assert await calendar.events("someone-else", date(2026, 8, 1), date(2026, 8, 31)) == []


async def test_static_directory_lookup_and_listing():
    directory = StaticDirectory([ASHA])
    assert await directory.get("P001") == ASHA and await directory.get("nope") is None
    assert await directory.list() == [ASHA]


async def test_fake_finance_is_idempotent_by_key():
    finance = FakeFinance()
    first = await finance.submit_claim(CLAIM, idempotency_key="k")
    again = await finance.submit_claim(CLAIM, idempotency_key="k")
    other = await finance.submit_claim(CLAIM, idempotency_key="k2")
    assert first.reference == again.reference == "FIN-2026-000001" and not first.duplicate
    assert again.duplicate and other.reference == "FIN-2026-000002"


async def test_fake_finance_demands_a_reason_for_a_rejection():
    finance = FakeFinance()
    with pytest.raises(ValueError, match="needs a comment"):
        await finance.decide_claim("FIN-1", approved=False, approver_id="R", comment="  ")
    assert (
        await finance.decide_claim("FIN-1", approved=False, approver_id="R", comment="no")
        == "rejected"
    )
    assert await finance.decide_claim("FIN-1", approved=True, approver_id="R") == "approved"
    assert [d[1] for d in finance.decisions] == [False, True]
