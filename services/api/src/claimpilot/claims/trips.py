"""Finding business trips in a pile of documents.

A trip is anchored by documents that prove the employee was away from their base city: flight and
train tickets, and hotel folios. Anchors are clustered by date and city into trips; meals, cabs and
fuel are then pulled into a trip only when they are dated inside its window AND were bought in a
trip city (or are an airport or station transfer). Anything that cannot be tied to a trip with
that evidence stays out of it: a wrongly merged trip is worse than a separate claim.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from claimpilot.domain import DocType, ExpenseCategory, ProcessedDocument
from claimpilot.policy import hotel_stay, normalise_city

TICKET_TYPES = frozenset({DocType.flight_ticket, DocType.train_ticket})
TRAVEL_CATEGORIES = frozenset(
    {ExpenseCategory.travel_domestic, ExpenseCategory.travel_international}
)
# Documents that may belong to a trip when dated in its window in a trip city.
FOLLOWER_CATEGORIES = frozenset(
    {ExpenseCategory.meals, ExpenseCategory.local_conveyance, ExpenseCategory.fuel_vehicle}
)
# The window around the anchor dates in which followers are accepted (a cab to the airport the
# evening before, breakfast after checkout).
WINDOW_MARGIN_DAYS = 1
# A trip that has left the base city and not yet returned stays open this long for later anchors.
MAX_OPEN_TRIP_DAYS = 14

_TRANSFER = re.compile(
    r"\b(airport|aerodrome|railway station|rly\.? stn|station transfer|terminal)\b", re.IGNORECASE
)
_CLOCK = re.compile(r"\s*(\d{1,2}):(\d{2})")
_STAY_DATE = re.compile(r"\b(\d{1,2})[-/ ]([A-Za-z]{3})[a-z]*\b")
_MONTH_NUMBER = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}


@dataclass(frozen=True, slots=True)
class Anchor:
    """A document that proves the employee was away from base, with its dates and cities."""

    doc: ProcessedDocument
    start: date | None
    end: date | None
    cities: frozenset[str]  # normalised; never contains the base city
    leaves_base: bool = False  # a ticket departing from the base city
    returns_base: bool = False  # a ticket arriving at the base city
    minute: int = -1  # departure time as minutes after midnight; -1 when not printed


@dataclass(slots=True)
class Trip:
    anchors: list[Anchor] = field(default_factory=list)
    followers: list[ProcessedDocument] = field(default_factory=list)
    start: date | None = None
    end: date | None = None
    cities: set[str] = field(default_factory=set)
    departed: date | None = None  # when the outbound ticket leaves base
    returned: bool = False

    def add(self, anchor: Anchor) -> None:
        self.anchors.append(anchor)
        if anchor.start is not None and anchor.end is not None:
            self.start = anchor.start if self.start is None else min(self.start, anchor.start)
            self.end = anchor.end if self.end is None else max(self.end, anchor.end)
        self.cities |= anchor.cities
        if anchor.leaves_base and self.departed is None:
            self.departed = anchor.start
        self.returned = self.returned or anchor.returns_base

    @property
    def is_open(self) -> bool:
        """Left the base city and has not come back (as far as the documents show)."""
        return self.departed is not None and not self.returned

    def span(self) -> tuple[date, date] | None:
        """First and last day of the trip, or ``None`` when none of its anchors is dated."""
        if self.start is None or self.end is None:
            return None
        return self.start, self.end

    @property
    def documents(self) -> list[ProcessedDocument]:
        return [a.doc for a in self.anchors] + self.followers


def is_ticket(doc: ProcessedDocument) -> bool:
    r = doc.receipt
    return r.doc_type in TICKET_TYPES or (
        doc.category in TRAVEL_CATEGORIES and bool(r.travel_from or r.travel_to)
    )


def is_stay(doc: ProcessedDocument) -> bool:
    return (
        doc.receipt.doc_type is DocType.hotel_folio or doc.category is ExpenseCategory.accommodation
    )


def is_transfer(doc: ProcessedDocument) -> bool:
    """A cab or auto to or from an airport or station (the line items or merchant say so)."""
    if doc.category is not ExpenseCategory.local_conveyance:
        return False
    texts = [doc.receipt.merchant_name or "", *(i.description for i in doc.receipt.line_items)]
    return any(_TRANSFER.search(t) for t in texts)


def stay_start(doc: ProcessedDocument) -> date | None:
    """First night of a hotel folio.

    The folio's date is checkout; each room line is a night, usually labelled ``12-Jul``. Without
    readable labels the number of room lines gives the nights.
    """
    checkout = doc.expense_date
    if checkout is None:
        return None
    nights: list[date] = []
    for item in doc.receipt.line_items:
        match = _STAY_DATE.search(item.description)
        month = _MONTH_NUMBER.get(match.group(2).lower()) if match else None
        if match and month:
            try:
                night = date(checkout.year, month, int(match.group(1)))
            except ValueError:
                continue
            if night > checkout:  # a December night on a January checkout
                night = night.replace(year=night.year - 1)
            nights.append(night)
    if nights:
        return min(nights)
    stay = hotel_stay(doc.receipt)
    count = len(stay.rates) if stay and stay.nights_known else 1
    return checkout - timedelta(days=count)


def _minute_of(printed: str | None) -> int:
    """``"16:30"`` as minutes after midnight, or -1 when there is no readable time."""
    match = _CLOCK.match(printed or "")
    return int(match.group(1)) * 60 + int(match.group(2)) if match else -1


def as_anchor(doc: ProcessedDocument, base: str | None, names: dict[str, str]) -> Anchor | None:
    """The document as a trip anchor, or ``None`` when it does not show travel away from base.

    ``names`` collects a display spelling for each normalised city (first one seen wins).
    """
    receipt = doc.receipt
    day = doc.expense_date
    if is_ticket(doc):
        ends = [
            (receipt.travel_from, normalise_city(receipt.travel_from)),
            (receipt.travel_to, normalise_city(receipt.travel_to)),
        ]
        known = {key for _, key in ends if key}
        away = known - ({base} if base else set())
        if known and not away:  # both ends in the base city: not a trip
            return None
        for raw, key in ends:
            if key and key in away:
                names.setdefault(key, (raw or key).strip())
        return Anchor(
            doc=doc,
            start=day,
            end=day,
            cities=frozenset(away),
            leaves_base=bool(base) and ends[0][1] == base,
            returns_base=bool(base) and ends[1][1] == base,
            minute=_minute_of(receipt.time),
        )
    if is_stay(doc):
        city = normalise_city(receipt.merchant_city)
        if city is not None and city == base:
            return None
        if city is not None:
            names.setdefault(city, (receipt.merchant_city or city).strip())
        start = stay_start(doc) or day
        return Anchor(doc, start, day, frozenset({city} if city else ()))
    return None


def cluster_anchors(anchors: Sequence[Anchor]) -> list[Trip]:
    """Group dated anchors into trips.

    Sorted by date, an anchor joins the latest trip when it overlaps it in time (or the trip is
    still open) and shares a city with it. A second departure from base, or any departure after
    the traveller has returned, starts a new trip even on the same day.
    """
    # Within a day, tickets go in the order they depart, so a ticket home from one trip is never
    # swept into the next trip that leaves later the same day. Without printed times, an outbound
    # ticket goes first: a same-day round trip is far likelier than a same-day turnaround.
    keyed = [
        (a.start, a.end, a.minute, not a.leaves_base, a.doc.id, a)
        for a in anchors
        if a.start and a.end
    ]
    trips: list[Trip] = []
    for start, *_, anchor in sorted(keyed, key=lambda k: k[:5]):
        if trips and _joins(trips[-1], anchor, start):
            trips[-1].add(anchor)
        else:
            trip = Trip()
            trip.add(anchor)
            trips.append(trip)
    return trips


def _joins(trip: Trip, anchor: Anchor, start: date) -> bool:
    """Does ``anchor`` (starting ``start``) belong to the latest ``trip``?"""
    if trip.cities and anchor.cities and not trip.cities & anchor.cities:
        return False
    if anchor.leaves_base and (
        trip.returned or (trip.departed is not None and trip.departed != start)
    ):
        return False
    end = trip.end or start  # a clustered trip always has dates; this only satisfies the types
    if start <= end:
        return True
    return trip.is_open and (start - end).days <= MAX_OPEN_TRIP_DAYS


def attach_undated_anchors(trips: list[Trip], undated: Sequence[Anchor]) -> None:
    """A ticket or hotel with no readable date joins the one trip to its city, else stands alone."""
    for anchor in sorted(undated, key=lambda a: a.doc.id):
        matches = [t for t in trips if anchor.cities and t.cities & anchor.cities]
        if len(matches) == 1:
            matches[0].add(anchor)
        else:
            trip = Trip()
            trip.add(anchor)
            trips.append(trip)


def _nearest_trip(
    trips: Sequence[Trip], doc: ProcessedDocument, city: str | None, day: date
) -> Trip | None:
    """The closest trip whose window holds ``day`` and whose city (or transfer) ``doc`` matches."""
    margin = timedelta(days=WINDOW_MARGIN_DAYS)
    best: tuple[int, Trip] | None = None
    for trip in trips:
        span = trip.span()
        if span is None:  # a trip whose anchors are all undated has no window
            continue
        first, last = span
        if not first - margin <= day <= last + margin:
            continue
        if not ((city is not None and city in trip.cities) or is_transfer(doc)):
            continue
        gap = max((first - day).days, (day - last).days, 0)  # 0 when inside the trip itself
        if best is None or gap < best[0]:
            best = (gap, trip)
    return best[1] if best else None


def assign_followers(
    trips: Sequence[Trip], candidates: Sequence[ProcessedDocument]
) -> list[ProcessedDocument]:
    """Pull meals, cabs and fuel into trips; return the ones that belong to none.

    A dated document joins the nearest trip whose window contains its date and whose city it was
    bought in (or that it is an airport/station transfer for). An undated one joins only when
    exactly one trip visited the city it was bought in.
    """
    leftovers: list[ProcessedDocument] = []
    for doc in sorted(candidates, key=lambda d: (d.expense_date or date.max, d.id)):
        city = normalise_city(doc.receipt.merchant_city)
        day = doc.expense_date
        if day is None:
            matches = [t for t in trips if city and city in t.cities]
            target = matches[0] if len(matches) == 1 else None
        else:
            target = _nearest_trip(trips, doc, city, day)
        if target is None:
            leftovers.append(doc)
        else:
            target.followers.append(doc)
    return leftovers
