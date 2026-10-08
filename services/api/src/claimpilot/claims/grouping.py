"""Pile-to-claim grouping: turn one employee's processed documents into claims.

Three of the four claim modes are derived from documents (``allowance`` is for per-diem and
mileage, which have no documents, so it is never generated here):

* **trip**: tickets and hotel folios away from the base city anchor a trip; meals, cabs and fuel
  bought in the trip city inside its window (anchor dates +/- 1 day), and airport or station
  transfers, join it. See :mod:`claimpilot.claims.trips`.
* **event**: one claim per client dinner, course or conference document.
* **period**: everything else, one claim per category per calendar month (mobile bills, local
  conveyance, fuel, base-city meals ...).

Grouping is pure and policy-free: no findings, no questions. The pipeline adds those with
:func:`claimpilot.claims.service.finalize_claim`. A document without a readable date goes to the
best-guess claim for its category (the month it most likely belongs to) and the claims module
then asks the employee for the date.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from datetime import date

from claimpilot.claims import labels
from claimpilot.claims.trips import (
    FOLLOWER_CATEGORIES,
    Anchor,
    Trip,
    as_anchor,
    assign_followers,
    attach_undated_anchors,
    cluster_anchors,
    is_stay,
    stay_start,
)
from claimpilot.domain import Claim, ClaimMode, Employee, ExpenseCategory, ProcessedDocument
from claimpilot.policy import normalise_city

EVENT_CATEGORIES = frozenset(
    {
        ExpenseCategory.client_entertainment,
        ExpenseCategory.learning,
        ExpenseCategory.conference,
    }
)
MAX_TRIP_CITIES_IN_TITLE = 2

Month = tuple[int, int]  # (year, month)


def group_documents(
    employee: Employee,
    docs: Sequence[ProcessedDocument],
    *,
    today: date | None = None,
    id_prefix: str = "clm",
) -> list[Claim]:
    """Group ``docs`` (all of one employee's) into claims, in a deterministic order.

    ``today`` is only used as the last-resort month for undated documents when there is no other
    dated document to guess from. A claim's id is ``<id_prefix>-<employee.id>-<hash of its sorted
    document ids>``, so the same documents always give the same claim id.
    """
    today = today or date.today()
    base = normalise_city(employee.base_city)
    unique = list({doc.id: doc for doc in docs}.values())  # a document passed twice counts once

    names: dict[str, str] = {}
    events: list[ProcessedDocument] = []
    anchors: list[Anchor] = []
    followers: list[ProcessedDocument] = []
    rest: list[ProcessedDocument] = []
    for doc in unique:
        if doc.category in EVENT_CATEGORIES:
            events.append(doc)
        elif (anchor := as_anchor(doc, base, names)) is not None:
            anchors.append(anchor)
        elif doc.category in FOLLOWER_CATEGORIES:
            followers.append(doc)
        else:
            rest.append(doc)

    trips = cluster_anchors(anchors)
    attach_undated_anchors(trips, [a for a in anchors if a.start is None or a.end is None])
    rest += assign_followers(trips, followers)

    claims = [_trip_claim(employee, trip, names, id_prefix) for trip in trips]
    claims += [_event_claim(employee, doc, id_prefix) for doc in events]
    claims += _period_claims(employee, unique, rest, today, id_prefix)
    return sorted(claims, key=lambda c: (c.start_date or date.max, c.mode.value, c.title, c.id))


# --- building claims -----------------------------------------------------------------------------


def _span(doc: ProcessedDocument) -> tuple[date, date] | None:
    """First and last day a document covers (a hotel folio covers its nights)."""
    day = doc.expense_date
    if day is None:
        return None
    return ((stay_start(doc) if is_stay(doc) else None) or day, day)


def _claim_id(employee: Employee, docs: Sequence[ProcessedDocument], id_prefix: str) -> str:
    digest = hashlib.sha256("|".join(sorted(d.id for d in docs)).encode()).hexdigest()[:10]
    return f"{id_prefix}-{employee.id}-{digest}"


def _dates(docs: Sequence[ProcessedDocument]) -> tuple[date | None, date | None]:
    spans = [s for s in (_span(d) for d in docs) if s is not None]
    return min((s[0] for s in spans), default=None), max((s[1] for s in spans), default=None)


def _make_claim(
    employee: Employee,
    mode: ClaimMode,
    docs: Sequence[ProcessedDocument],
    title: str,
    city: str | None,
    id_prefix: str,
) -> Claim:
    ordered = sorted(docs, key=lambda d: (d.expense_date or date.max, d.id))
    start, end = _dates(ordered)
    currencies = {d.receipt.currency.strip().upper() for d in ordered}
    return Claim(
        id=_claim_id(employee, ordered, id_prefix),
        employee_id=employee.id,
        title=title,
        mode=mode,
        document_ids=[d.id for d in ordered],
        total=round(sum(d.amount for d in ordered), 2),
        currency=currencies.pop() if len(currencies) == 1 else "INR",
        start_date=start,
        end_date=end,
        city=city,
    )


def _trip_claim(employee: Employee, trip: Trip, names: dict[str, str], id_prefix: str) -> Claim:
    docs = trip.documents
    # Cities in the order the traveller reached them.
    visited: list[str] = []
    for anchor in sorted(trip.anchors, key=lambda a: (a.start or date.max, a.doc.id)):
        visited += [c for c in sorted(anchor.cities) if c not in visited]
    visited += [c for c in sorted(trip.cities) if c not in visited]
    shown = [names.get(c, c.title()) for c in visited]
    if not shown:
        place = "Business"
    elif len(shown) <= MAX_TRIP_CITIES_IN_TITLE:
        place = " & ".join(shown)
    else:
        place = f"{shown[0]} +{len(shown) - 1} more"
    start, end = _dates(docs)
    when = labels.date_range(start, end) if start and end else "(dates needed)"
    return _make_claim(
        employee,
        ClaimMode.trip,
        docs,
        f"{place} trip {when}",
        shown[0] if shown else None,
        id_prefix,
    )


def _event_claim(employee: Employee, doc: ProcessedDocument, id_prefix: str) -> Claim:
    label = labels.EVENT_LABELS[doc.category]
    day = doc.expense_date
    title = f"{label} {labels.day_label(day)}" if day else f"{label} (date needed)"
    # A client dinner or conference happens where the bill was issued; a course is bought online
    # from a supplier whose city says nothing about where the employee was.
    printed = (doc.receipt.merchant_city or "").strip()
    city = printed if printed and doc.category is not ExpenseCategory.learning else None
    return _make_claim(
        employee, ClaimMode.event, [doc], title, city or employee.base_city, id_prefix
    )


def _period_claims(
    employee: Employee,
    everything: Sequence[ProcessedDocument],
    docs: Sequence[ProcessedDocument],
    today: date,
    id_prefix: str,
) -> list[Claim]:
    guesser = _MonthGuesser(everything, today)
    buckets: dict[tuple[ExpenseCategory, Month], list[ProcessedDocument]] = {}
    for doc in docs:
        buckets.setdefault((doc.category, guesser.month_of(doc)), []).append(doc)
    claims = []
    for (category, (year, month)), members in buckets.items():
        label = labels.PERIOD_LABELS[category]
        if any(d.expense_date for d in members):
            title = f"{label} {labels.month_label(date(year, month, 1))}"
        else:
            title = f"{label} (date needed)"
        city = _modal_city(members, employee)
        claims.append(_make_claim(employee, ClaimMode.period, members, title, city, id_prefix))
    return claims


def _modal_city(docs: Sequence[ProcessedDocument], employee: Employee) -> str:
    """The city most of the documents were bought in, else the employee's base city."""
    seen = Counter((d.receipt.merchant_city or "").strip() for d in docs)
    seen.pop("", None)
    if not seen:
        return employee.base_city
    return min(seen.items(), key=lambda kv: (-kv[1], kv[0]))[0]


class _MonthGuesser:
    """Which calendar month a document belongs to, guessing for undated ones.

    An undated document most likely belongs with the newest documents of its own category, then
    with the newest document of any category, and failing both with the current month.
    """

    def __init__(self, docs: Sequence[ProcessedDocument], today: date) -> None:
        self._by_category: dict[ExpenseCategory, Month] = {}
        newest: Month | None = None
        for doc in docs:
            day = doc.expense_date
            if day is None:
                continue
            month = (day.year, day.month)
            self._by_category[doc.category] = max(month, self._by_category.get(doc.category, month))
            newest = max(month, newest) if newest else month
        self._fallback: Month = newest or (today.year, today.month)

    def month_of(self, doc: ProcessedDocument) -> Month:
        day = doc.expense_date
        if day is not None:
            return (day.year, day.month)
        return self._by_category.get(doc.category, self._fallback)
