"""Policy-relevant facts that live in the *text* of an extracted receipt.

Alcohol lines, hotel nights, ticket class and attendee counts are not structured fields, so they
are recovered here with small fixed vocabularies. Receipt text is untrusted: it is only matched
against these vocabularies, never echoed into a finding message and never followed as an
instruction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from claimpilot.domain import ExtractedReceipt, LineItem

# --- alcohol -----------------------------------------------------------------------------------

# Production copy of the vocabulary used by the golden-set labels (evals.golden.has_alcohol),
# kept here because production code must not import from `evals`.
_ALCOHOL = re.compile(
    r"\b(beer|wine|whisk(?:e)?y|vodka|rum|gin|brandy|cocktail|lager|champagne"
    r"|tequila|scotch|liquor|prosecco)\b"
    r"|बीयर|वाइन|व्हिस्की|शराब",
    re.IGNORECASE,
)
# Soft drinks and zero-alcohol versions that share a word with the vocabulary.
_NOT_ALCOHOL = re.compile(
    r"non[- ]?alcoholic|alcohol[- ]?free|zero[- ]?alcohol|0\.0\s*%|mocktail"
    r"|ginger (?:beer|ale)|root beer",
    re.IGNORECASE,
)


def alcohol_items(receipt: ExtractedReceipt) -> list[LineItem]:
    """Line items that are alcoholic drinks."""
    return [
        item
        for item in receipt.line_items
        if _ALCOHOL.search(item.description) and not _NOT_ALCOHOL.search(item.description)
    ]


# --- hotel nights ------------------------------------------------------------------------------

_ROOM = re.compile(
    r"\b(?:room|rooms|tariff|accommodation|lodging|suite|stay|night|nights|rent)\b", re.IGNORECASE
)
# Charges on a folio that are not the room rate (the 4.1 cap is about the room rate only).
_EXTRA = re.compile(
    r"room service|service charge|laundry|mini[- ]?bar|restaurant|breakfast|lunch|dinner|food"
    r"|beverage|\bbar\b|\bspa\b|telephone|parking|wi-?fi|internet|\bgst\b|\btax|\bcess\b"
    r"|round[- ]?off|discount|deposit|advance|\bcab\b|transfer",
    re.IGNORECASE,
)
# Things a hotel often rolls into the room rate. A line that is the room plus one of these
# ("Deluxe Room incl. breakfast") is still the room, and counting the whole line is the cautious
# reading: it can only make a stay look dearer, never let one over the cap slip through.
_BUNDLED = re.compile("breakfast|lunch|dinner|food|beverage|wi-?fi|internet", re.IGNORECASE)


def _is_extra(description: str) -> bool:
    """A charge that is not the room rate (the 4.1 cap is about the room rate only)."""
    if not _EXTRA.search(description):
        return False
    only_bundled = not _EXTRA.search(_BUNDLED.sub(" ", description))
    return not (_ROOM.search(description) and only_bundled)


_NIGHTS = re.compile(r"(\d{1,2})\s*nights?\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class HotelStay:
    """Room rates of a folio, before GST.

    ``rates`` has one entry per night when ``nights_known``. Otherwise the bill gave no way to
    tell how many nights it covers and ``rates`` holds the whole pre-tax amount as one entry.
    """

    rates: tuple[float, ...]
    nights_known: bool


def _line_nights(item: LineItem) -> int:
    quantity = item.quantity
    if quantity is not None and quantity > 1 and float(quantity).is_integer():
        return int(quantity)
    match = _NIGHTS.search(item.description)
    return int(match.group(1)) if match and int(match.group(1)) > 0 else 1


def hotel_stay(receipt: ExtractedReceipt) -> HotelStay | None:
    """Per-night room rates of a hotel folio, or ``None`` when no room charge can be found.

    Folios print one room line per night, so each such line is one night. A line may also carry a
    quantity ("Deluxe room x 2 @ 6,100") or say "2 nights"; the per-night rate is then the unit
    price, or the line amount divided by the nights. With no line items at all, the pre-tax total
    stands for the whole stay and the nights are unknown.
    """
    candidates = [
        item for item in receipt.line_items if item.amount > 0 and not _is_extra(item.description)
    ]
    room_lines = [item for item in candidates if _ROOM.search(item.description)] or candidates
    rates: list[float] = []
    for item in room_lines:
        nights = _line_nights(item)
        if item.unit_price and item.quantity and item.quantity > 1:
            per_night = item.unit_price
        else:
            per_night = item.amount / nights
        rates.extend([round(per_night, 2)] * nights)
    if rates:
        return HotelStay(tuple(rates), nights_known=True)
    if receipt.line_items:  # only extras (room service, laundry ...): nothing to compare
        return None
    amount = receipt.subtotal
    if amount is None and receipt.total is not None:
        t = receipt.taxes
        taxes = sum(x or 0.0 for x in (t.cgst, t.sgst, t.igst, t.cess))
        amount = receipt.total - taxes - (receipt.service_charge or 0.0)
    if amount is None or amount <= 0:
        return None
    return HotelStay((round(amount, 2),), nights_known=False)


# --- ticket class ------------------------------------------------------------------------------

AirClass = Literal["economy", "premium_economy", "business", "first"]

# Order matters: "premium economy" contains "economy".
_AIR_CLASSES: tuple[tuple[AirClass, re.Pattern[str]], ...] = (
    ("first", re.compile(r"\bfirst[ -]?class\b|\bclass[ :]+first\b", re.IGNORECASE)),
    ("premium_economy", re.compile(r"\bpremium[ -]?economy\b", re.IGNORECASE)),
    ("business", re.compile(r"\bbusiness([ -]?class)?\b", re.IGNORECASE)),
    ("economy", re.compile(r"\beconomy\b|\bcoach\b", re.IGNORECASE)),
)


def _line_text(receipt: ExtractedReceipt) -> str:
    return " ".join(item.description for item in receipt.line_items)


def air_class(receipt: ExtractedReceipt) -> AirClass | None:
    """Cabin class printed on a flight ticket's fare lines, or ``None`` when none is printed."""
    text = _line_text(receipt)
    for name, pattern in _AIR_CLASSES:
        if pattern.search(text):
            return name
    return None


TRAIN_CLASS_NAMES: dict[str, str] = {
    "2S": "Second Sitting",
    "SL": "Sleeper",
    "CC": "AC Chair Car",
    "3E": "Third AC Economy",
    "3A": "Third AC",
    "EC": "Executive Chair Car",
    "2A": "Second AC",
    "1A": "First AC",
}
# Checked in order: the more specific names first ("executive chair car" before "chair car").
_TRAIN_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("1A", re.compile(r"\bfirst ac\b|\b1st ac\b|\bac first\b|\bfirst class ac\b", re.IGNORECASE)),
    ("2A", re.compile(r"\bsecond ac\b|\b2nd ac\b|\b2 tier\b|\b2ac\b", re.IGNORECASE)),
    ("3E", re.compile(r"\b3ac economy\b|\bthird ac economy\b", re.IGNORECASE)),
    ("3A", re.compile(r"\bthird ac\b|\b3rd ac\b|\b3 tier\b|\b3ac\b", re.IGNORECASE)),
    ("EC", re.compile(r"\bexecutive chair\b|\bexec chair\b", re.IGNORECASE)),
    ("CC", re.compile(r"\bchair car\b", re.IGNORECASE)),
    ("SL", re.compile(r"\bsleeper\b", re.IGNORECASE)),
    ("2S", re.compile(r"\bsecond sitting\b", re.IGNORECASE)),
)
# Two-letter railway codes are only trusted in capitals, so "cc" or "sl" in a word never match.
_TRAIN_CODE = re.compile(r"(?<![A-Za-z0-9])(1A|2A|3A|3E|EC|CC|SL|2S)(?![A-Za-z0-9])")


def train_class(receipt: ExtractedReceipt) -> str | None:
    """Railway class code (``3A``, ``SL`` ...) printed on a train ticket, or ``None``."""
    text = _line_text(receipt)
    for code, pattern in _TRAIN_WORDS:
        if pattern.search(text):
            return code
    match = _TRAIN_CODE.search(text)
    return match.group(1) if match else None


# --- attendees ---------------------------------------------------------------------------------

_CALENDAR_LIST = re.compile(r"attendees\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL)
_NUMBER_WORDS = {
    word: number
    for number, word in enumerate(
        [
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
            "twenty",
        ],
        1,
    )
}
_COUNT = r"(\d{1,3}|" + "|".join(_NUMBER_WORDS) + r")"
_BARE_COUNT = re.compile(
    rf"\s*{_COUNT}\s*(?:people|persons?|guests?|pax|attendees?|of us|in all|including me)?\s*\.?",
    re.IGNORECASE,
)
_PARTY_OF = re.compile(
    rf"\b(?:party|group|table) of {_COUNT}\b"
    rf"|\b{_COUNT} (?:people|persons|guests|pax|attendees|of us)\b",
    re.IGNORECASE,
)
_NAME_SPLIT = re.compile(r",|;|\band\b|&|\n", re.IGNORECASE)
_SELF = {"me", "myself", "i", "self"}
_NOT_A_NAME = {"n/a", "na", "none", "nil", "nobody", "no one", "unknown", "tbd"}


def attendee_headcount(answer: str, employee_name: str | None = None) -> int | None:
    """How many people were at an event, from the employee's (or the calendar's) attendee answer.

    "4", "four people", "party of 4" and "six of us" are total headcounts. A list of names counts
    the names, plus the employee unless they are already in the list (by name, or as "me").
    Returns ``None`` when nothing countable is there. The count is only as honest as the
    answer; this is a policy check, not a trust check.
    """
    text = answer.strip()
    if listed := _CALENDAR_LIST.search(text):
        text = listed.group(1)
    if bare := _BARE_COUNT.fullmatch(text):
        return _to_int(bare.group(1)) or None
    if party := _PARTY_OF.search(text):
        return _to_int(party.group(1) or party.group(2)) or None
    text = re.sub(r"\([^)]*\)", " ", text)  # "(Orion Retail)" company notes
    parts = [part.strip() for part in _NAME_SPLIT.split(text)]
    names = [p for p in parts if re.search(r"[^\W\d_]", p) and p.casefold() not in _NOT_A_NAME]
    if not names:
        return None
    others = [n for n in names if n.casefold() not in _SELF]
    named_self = employee_name is not None and any(
        _is_same_person(n, employee_name) for n in others
    )
    # The employee is one of the people unless they were already counted by name.
    return len(others) + (0 if named_self else 1)


def _to_int(token: str) -> int:
    return _NUMBER_WORDS.get(token.casefold()) or int(token)


def _is_same_person(listed: str, employee_name: str) -> bool:
    """A listed name that is (part of) the employee's name; short fragments do not count."""
    a, b = listed.casefold().strip(), employee_name.casefold().strip()
    return a == b or (len(a) >= 4 and (a in b or b in a))
