"""System One sees what the employee's calendar shows on the receipt's date (names left out)."""

# ruff: noqa: F811, F401
# The `fixtures` and `sessions` pytest fixtures come from test_process_batch.

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from test_process_batch import (
    FIXTURES,
    Fixture,
    build_harness,
    fixtures,
    sessions,
    unique_png,
)

from claimpilot.domain import DocType, ExpenseCategory, ExtractedReceipt, LineItem, ReceiptTruth
from claimpilot.extraction.schema import from_domain
from claimpilot.ports import CalendarEvent, StaticCalendar

pytestmark = pytest.mark.skipif(not FIXTURES.exists(), reason="synthetic fixtures not present")

DAY = date(2026, 9, 14)


def dinner(day: str | None) -> ExtractedReceipt:
    return ExtractedReceipt(
        doc_type=DocType.restaurant_bill,
        merchant_name="Spice Route Dining",
        merchant_city="Hyderabad",
        date=day,
        line_items=[LineItem(description="Thali", amount=400.0)],
        total=400.0,
    )


class Recorder:
    """Wraps the harness's System One stand-in and keeps every state it was asked about."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.states = inner, []
        self.name = inner.name

    async def decide(self, state: Any, questions: Any) -> Any:
        self.states.append(state)
        return await self.inner.decide(state, questions)


async def run_dinner(sessions, models_registry, fixtures, receipt, calendar) -> list[dict]:
    harness = build_harness(
        sessions,
        models_registry,
        fixtures,
        calendar=calendar,
        responder=lambda _request: from_domain(receipt),
    )
    harness.deps.decisions.__dict__["_by_key"][("Spice Route Dining", 400.0)] = Fixture(  # type: ignore[attr-defined]
        "synthetic",
        b"",
        ReceiptTruth(id="synthetic", receipt=receipt, category=ExpenseCategory.meals),
    )
    recorder = Recorder(harness.deps.decisions)
    object.__setattr__(harness.deps, "decisions", recorder)  # PipelineDeps is frozen
    view = await harness.run(await harness.upload([("dinner.png", unique_png(3))]))
    assert view.status == "done" and view.failed == 0
    return recorder.states


async def test_a_client_dinner_in_the_calendar_reaches_the_decision(
    sessions, models_registry, fixtures
):
    event = CalendarEvent(
        id="e1",
        title="Dinner with Kestrel Logistics",
        date=DAY,
        kind="client_dinner",
        attendees=["Neha Rao (Kestrel)", "Rohan Kapoor (Kestrel)"],
    )
    [state] = await run_dinner(
        sessions,
        models_registry,
        fixtures,
        dinner("2026-09-14"),
        StaticCalendar({"P001": [event]}),
    )
    assert state["calendar_that_day"] == ["client dinner with 2 guests"]
    assert "Kestrel" not in str(state) and "Neha" not in str(state)  # counts, never names


async def test_an_empty_calendar_day_is_said_so(sessions, models_registry, fixtures):
    elsewhere = CalendarEvent(
        id="e1", title="Offsite", date=DAY + timedelta(days=3), kind="offsite", attendees=[]
    )
    [state] = await run_dinner(
        sessions,
        models_registry,
        fixtures,
        dinner("2026-09-14"),
        StaticCalendar({"P001": [elsewhere]}),
    )
    assert state["calendar_that_day"] == ["nothing relevant"]


async def test_a_receipt_without_a_date_is_asked_without_calendar_context(
    sessions, models_registry, fixtures
):
    [state] = await run_dinner(
        sessions, models_registry, fixtures, dinner(None), StaticCalendar({"P001": []})
    )
    assert "calendar_that_day" not in state


async def test_an_unreachable_calendar_never_fails_the_document(
    sessions, models_registry, fixtures
):
    class Down:
        async def events(self, employee_id: str, start: date, end: date) -> list:
            raise ConnectionError("calendar service is down")

    [state] = await run_dinner(sessions, models_registry, fixtures, dinner("2026-09-14"), Down())
    assert "calendar_that_day" not in state
