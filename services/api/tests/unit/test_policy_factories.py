"""Builders shared by the policy and claims tests. Contains no tests itself.

(It is named ``test_policy_*`` so that it sits with the files this workstream owns; pytest finds
nothing to run in it.)
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from claimpilot.domain import (
    Claim,
    ClaimMode,
    Decisions,
    DocType,
    Employee,
    ExpenseCategory,
    ExtractedReceipt,
    Finding,
    LineItem,
    ProcessedDocument,
    TaxBreakup,
)
from claimpilot.policy import Policy

TODAY = date(2026, 9, 30)  # the calibration date: every golden claim is inside its window


def clause_as[C](policy: Policy, kind: type[C], clause_id: str) -> C:
    """``policy.clause(id)`` narrowed to its concrete class, so tests can read its parameters."""
    clause = policy.clause(clause_id)
    assert isinstance(clause, kind)
    return clause


def employee(
    grade: str = "L3", base_city: str = "Pune", *, emp_id: str = "P001", name: str = "Asha Rao"
) -> Employee:
    return Employee(id=emp_id, name=name, employee_id="EMP00001", grade=grade, base_city=base_city)


def doc(
    doc_id: str = "d1",
    category: ExpenseCategory = ExpenseCategory.meals,
    *,
    doc_type: DocType = DocType.restaurant_bill,
    day: str | None = "2026-08-12",
    total: float | None = 500.0,
    merchant: str | None = "Test Cafe",
    city: str | None = "Pune",
    items: Sequence[tuple[str, float]] = (),
    alcohol: float = 0.0,
    personal: float = 0.0,
    findings: Sequence[Finding] = (),
    **receipt_fields: Any,
) -> ProcessedDocument:
    """A processed document with sensible defaults; override only what the test is about."""
    receipt = ExtractedReceipt(
        doc_type=doc_type,
        merchant_name=merchant,
        merchant_city=city,
        date=day,
        total=total,
        line_items=[LineItem(description=text, amount=amount) for text, amount in items],
        **receipt_fields,
    )
    return ProcessedDocument(
        id=doc_id,
        filename=f"{doc_id}.png",
        sha256=f"sha-{doc_id}",
        receipt=receipt,
        decisions=Decisions(
            category=category,
            category_confidence=1.0,
            alcohol_present=alcohol,
            personal_expense=personal,
            engine="truth",
        ),
        findings=list(findings),
    )


def hotel(
    doc_id: str = "h1",
    *,
    rate: float = 5000.0,
    nights: int = 1,
    city: str | None = "Mumbai",
    checkout: date = date(2026, 8, 14),
    gst: float = 0.05,
) -> ProcessedDocument:
    """A hotel folio with one dated room line per night, plus GST on top."""
    start = checkout - timedelta(days=nights)
    lines = [
        (f"Room Charges {(start + timedelta(days=n)).strftime('%d-%b')}", rate)
        for n in range(nights)
    ]
    subtotal = rate * nights
    tax = round(subtotal * gst / 2, 2)
    return doc(
        doc_id,
        ExpenseCategory.accommodation,
        doc_type=DocType.hotel_folio,
        day=checkout.isoformat(),
        total=round(subtotal + 2 * tax, 2),
        merchant="Lotus Bay Residency",
        city=city,
        items=lines,
        subtotal=subtotal,
        taxes=TaxBreakup(cgst=tax, sgst=tax, gst_rate_percent=gst * 100),
    )


def ticket(
    doc_id: str,
    day: date | None,
    origin: str,
    destination: str,
    *,
    kind: DocType = DocType.flight_ticket,
    total: float = 4000.0,
    items: Sequence[tuple[str, float]] = (("Base Fare", 3800.0),),
    time: str | None = None,
) -> ProcessedDocument:
    return doc(
        doc_id,
        ExpenseCategory.travel_domestic,
        doc_type=kind,
        day=day.isoformat() if day else None,
        total=total,
        merchant="Megh Airways" if kind is DocType.flight_ticket else "RailYatra",
        city="New Delhi",
        items=items,
        travel_from=origin,
        travel_to=destination,
        invoice_number="PNR123",
        time=time,
    )


def meal(
    doc_id: str, day: date | None, city: str | None, total: float = 400.0
) -> ProcessedDocument:
    return doc(
        doc_id, ExpenseCategory.meals, day=day.isoformat() if day else None, city=city, total=total
    )


def cab(
    doc_id: str,
    day: date | None,
    city: str | None,
    total: float = 350.0,
    *,
    items: Sequence[tuple[str, float]] = (("Base Fare", 300.0),),
) -> ProcessedDocument:
    return doc(
        doc_id,
        ExpenseCategory.local_conveyance,
        doc_type=DocType.cab_receipt,
        day=day.isoformat() if day else None,
        city=city,
        total=total,
        merchant="Raahi Cabs",
        items=items,
        invoice_number=f"CRN{doc_id}",
    )


def claim_of(
    docs: Sequence[ProcessedDocument],
    *,
    mode: ClaimMode = ClaimMode.period,
    claim_id: str = "c1",
    city: str | None = None,
    start: date | None = None,
    end: date | None = None,
    title: str = "Test claim",
) -> Claim:
    return Claim(
        id=claim_id,
        employee_id="P001",
        title=title,
        mode=mode,
        document_ids=[d.id for d in docs],
        total=round(sum(d.amount for d in docs), 2),
        city=city,
        start_date=start,
        end_date=end,
    )
