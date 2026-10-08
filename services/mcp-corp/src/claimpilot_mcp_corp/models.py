"""Wire models of the mock corporate systems: what the HR directory and calendar tools return."""

from __future__ import annotations

import datetime as dt
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

EventKind = Literal["client_meeting", "client_dinner", "offsite", "training", "travel"]


class EmployeeRecord(BaseModel):
    """One entry of the HR directory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(description="Directory id, e.g. 'P001'.")
    name: str
    employee_id: str = Field(description="HR employee number, e.g. 'EMP85968'.")
    grade: str = Field(description="Grade band L1 (junior) to L5 (senior).")
    base_city: str
    base_state_code: str | None = Field(
        default=None, description="GST state code of the base city, e.g. '36'."
    )
    manager_id: str | None = Field(
        default=None, description="Directory id of the employee's manager, who approves claims."
    )
    email: str | None = None


class CalendarEvent(BaseModel):
    """A calendar entry. Multi-day events carry ``end_date`` and usually no times."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    date: dt.date = Field(description="The day of the event (its first day when multi-day).")
    end_date: dt.date | None = Field(
        default=None, description="Last day of a multi-day event; null for a single-day event."
    )
    start_time: dt.time | None = Field(default=None, description="Local start time (24 h).")
    end_time: dt.time | None = Field(default=None, description="Local end time (24 h).")
    location: str | None = None
    attendees: list[str] = Field(
        default_factory=list,
        description="Other people at the event, e.g. 'Neha Rao (Kestrel Logistics)'.",
    )
    kind: EventKind

    @model_validator(mode="after")
    def _check_span(self) -> Self:
        if self.end_date is not None and self.end_date < self.date:
            raise ValueError("end_date must not be before date")
        same_day = self.end_date is None or self.end_date == self.date
        if (
            same_day
            and self.start_time is not None
            and self.end_time is not None
            and self.end_time <= self.start_time
        ):
            raise ValueError("end_time must be after start_time")
        return self

    @property
    def last_day(self) -> dt.date:
        return self.end_date or self.date


class SeedEvent(CalendarEvent):
    """A calendar entry as stored in the seed: the same fields plus whose calendar it is."""

    owner: str = Field(description="Directory id of the employee whose calendar holds the event.")
