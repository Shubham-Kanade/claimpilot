"""Wording shared by claim titles and question text: dates, labels and safe quoting."""

from __future__ import annotations

from datetime import date

from claimpilot.domain import DocType, ExpenseCategory, ProcessedDocument
from claimpilot.policy import format_inr

EN_DASH = chr(0x2013)  # shown between the dates of a range, e.g. 12-14 Aug 2026
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

PERIOD_LABELS: dict[ExpenseCategory, str] = {
    ExpenseCategory.mobile_internet: "Mobile & internet",
    ExpenseCategory.local_conveyance: "Local conveyance",
    ExpenseCategory.fuel_vehicle: "Fuel",
    ExpenseCategory.meals: "Meals",
    ExpenseCategory.misc: "Miscellaneous",
    ExpenseCategory.wfh_supplies: "WFH supplies",
    ExpenseCategory.medical: "Medical",
    ExpenseCategory.relocation: "Relocation",
    ExpenseCategory.accommodation: "Accommodation",
    ExpenseCategory.travel_domestic: "Travel",
    ExpenseCategory.travel_international: "International travel",
    ExpenseCategory.client_entertainment: "Client entertainment",
    ExpenseCategory.learning: "Learning",
    ExpenseCategory.conference: "Conference",
}
EVENT_LABELS: dict[ExpenseCategory, str] = {
    ExpenseCategory.client_entertainment: "Client dinner",
    ExpenseCategory.learning: "Learning",
    ExpenseCategory.conference: "Conference",
}
DOC_LABELS: dict[DocType, str] = {
    DocType.restaurant_bill: "restaurant bill",
    DocType.gst_invoice: "invoice",
    DocType.hotel_folio: "hotel bill",
    DocType.cab_receipt: "cab receipt",
    DocType.flight_ticket: "flight ticket",
    DocType.train_ticket: "train ticket",
    DocType.fuel_slip: "fuel slip",
    DocType.mobile_bill: "mobile bill",
    DocType.upi_payment: "UPI payment",
    DocType.handwritten_bill: "handwritten bill",
    DocType.other: "document",
}


def rupees(amount: float) -> str:
    """``₹3,919``: whole rupees, for wording a person reads (findings keep the exact amount)."""
    return format_inr(round(amount))


def month_label(day: date) -> str:
    """``Aug 2026``."""
    return f"{_MONTHS[day.month - 1]} {day.year}"


def day_label(day: date) -> str:
    """``14 Aug 2026``."""
    return f"{day.day} {month_label(day)}"


def short_day_label(day: date) -> str:
    """``14 Aug``."""
    return f"{day.day} {_MONTHS[day.month - 1]}"


def date_range(start: date, end: date) -> str:
    """A date range with an en dash: ``12-14 Aug 2026``, ``30 Jul-2 Aug 2026``, ``12 Aug 2026``."""
    if start == end:
        return day_label(start)
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}{EN_DASH}{day_label(end)}"
    if start.year == end.year:
        return f"{short_day_label(start)}{EN_DASH}{day_label(end)}"
    return f"{day_label(start)}{EN_DASH}{day_label(end)}"


def safe_text(value: str | None, limit: int = 40) -> str:
    """Receipt text made safe to quote in a question: single line, no control characters, short.

    Receipts are untrusted input. Anything quoted back to the employee (and shown to the chat
    agent) goes through here so a crafted merchant name cannot smuggle in line breaks or a wall of
    text.
    """
    if not value:
        return ""
    printable = "".join(ch if ch.isprintable() else " " for ch in value)
    return " ".join(printable.replace('"', "'").split())[:limit].rstrip()


LEFT_QUOTE, RIGHT_QUOTE = chr(0x201C), chr(0x201D)


def quote(value: str) -> str:
    """Typographic quotes around receipt text so it reads as quoted, not as part of the sentence."""
    return f"{LEFT_QUOTE}{value}{RIGHT_QUOTE}"


def sentence(text: str) -> str:
    """Capitalise the first letter only (``str.capitalize`` would lower-case a merchant name)."""
    return text[:1].upper() + text[1:]


def describe(doc: ProcessedDocument) -> str:
    """``the ₹651 restaurant bill from "Chulha Cafe"``; the merchant only when it is printed."""
    label = DOC_LABELS.get(doc.receipt.doc_type, "document")
    text = f"the {label}"
    if doc.receipt.total is not None:
        text = f"the {rupees(doc.amount)} {label}"
    merchant = safe_text(doc.receipt.merchant_name)
    if not merchant:
        return text
    preposition = "to" if doc.receipt.doc_type is DocType.upi_payment else "from"
    return f"{text} {preposition} {quote(merchant)}"
