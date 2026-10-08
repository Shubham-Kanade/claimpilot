"""Arithmetic and GST consistency checks on an extracted receipt (India-native trust signals).

Extraction reports what is *printed*; these checks find printed documents that don't add up,
which is how edited totals and sloppy fakes usually give themselves away.
"""

from __future__ import annotations

from claimpilot.domain import ExtractedReceipt, validate_gstin
from claimpilot.domain.findings import Finding, Severity
from claimpilot.domain.text import plain_text

# Printed bills round the grand total to the rupee (and some print a separate round-off line the
# schema does not capture), so totals within ₹1 are consistent.
TOTAL_TOLERANCE = 1.0
LINE_TOLERANCE = 0.05
RATE_TOLERANCE_PP = 0.6  # percentage points; rates are printed rounded
STANDARD_GST_RATES = (0.0, 0.25, 3.0, 5.0, 12.0, 18.0, 28.0, 40.0)  # 40%: 2025 demerit slab


def _money(value: float) -> str:
    return f"₹{value:,.2f}"


HARD_TO_READ = " The image was hard to read at this point, so please compare it with the original."


def check_gst(receipt: ExtractedReceipt) -> list[Finding]:
    findings: list[Finding] = []
    findings += _check_line_items(receipt)
    findings += _check_total(receipt)
    findings += _check_tax_structure(receipt)
    findings += _check_rate(receipt)
    findings += _check_gstin(receipt)
    return _soften_unsure(findings, set(receipt.low_confidence_fields))


def _soften_unsure(findings: list[Finding], unsure: set[str]) -> list[Finding]:
    """A finding that rests on a figure the reader itself marked unsure asks for a check.

    A digit misread on a faded thermal bill breaks the arithmetic or the GSTIN checksum exactly
    like an edited figure does. When the extraction says it was guessing at that field, the honest
    reading is "please verify", not "this looks forged", so the finding drops from ``high`` to
    ``warn`` (and, being no longer conclusive, no longer blocks). Figures read with confidence keep
    their severity.
    """
    if not unsure:
        return findings
    return [
        f.model_copy(update={"severity": Severity.warn, "message": f.message + HARD_TO_READ})
        if f.severity is Severity.high and unsure.intersection(f.fields)
        else f
        for f in findings
    ]


def _check_line_items(r: ExtractedReceipt) -> list[Finding]:
    if r.subtotal is None or not r.line_items:
        return []
    items = round(sum(i.amount for i in r.line_items), 2)
    if abs(items - r.subtotal) <= LINE_TOLERANCE:
        return []
    if items < r.subtotal:
        # Fewer rupees in the rows than in the subtotal: a row (a ticket's convenience fee, a
        # packing charge) may simply not have been read. That is a reason to look, not evidence
        # of editing, so it must not block a genuine bill.
        return [
            Finding(
                code="items_incomplete",
                severity=Severity.warn,
                message=f"The items read add up to {_money(items)}, {_money(r.subtotal - items)} "
                f"less than the subtotal of {_money(r.subtotal)}; a line may be missing.",
                fields=("line_items", "subtotal"),
                expected=r.subtotal,
                actual=items,
            )
        ]
    return [  # more in the rows than the subtotal admits: a figure was changed
        Finding(
            code="items_subtotal_mismatch",
            severity=Severity.high,
            message=f"Line items add up to {_money(items)} but the subtotal says "
            f"{_money(r.subtotal)}.",
            fields=("line_items", "subtotal"),
            expected=items,
            actual=r.subtotal,
        )
    ]


def _expected_total(r: ExtractedReceipt) -> float | None:
    base = r.subtotal
    if base is None and r.line_items:
        base = sum(i.amount for i in r.line_items)
    if base is None:
        return None
    t = r.taxes
    taxes = sum(x or 0.0 for x in (t.cgst, t.sgst, t.igst, t.cess))
    return round(base + taxes + (r.service_charge or 0.0) - (r.discount or 0.0), 2)


def _check_total(r: ExtractedReceipt) -> list[Finding]:
    expected = _expected_total(r)
    if expected is None or r.total is None or abs(expected - r.total) <= TOTAL_TOLERANCE:
        return []
    return [
        Finding(
            code="total_mismatch",
            severity=Severity.high,
            message=f"The printed total {_money(r.total)} doesn't match the bill's own "
            f"items, taxes and charges ({_money(expected)}).",
            fields=("total",),
            expected=expected,
            actual=r.total,
        )
    ]


def _check_tax_structure(r: ExtractedReceipt) -> list[Finding]:
    t = r.taxes
    findings: list[Finding] = []
    if (t.cgst or t.sgst) and t.igst:
        findings.append(
            Finding(
                code="gst_mixed_intra_inter",
                severity=Severity.high,
                message="The bill charges both CGST/SGST (same-state) and IGST (inter-state), "
                "which a genuine GST invoice never does.",
                fields=("taxes",),
            )
        )
    if t.cgst is not None and t.sgst is not None and abs(t.cgst - t.sgst) > LINE_TOLERANCE:
        findings.append(
            Finding(
                code="cgst_sgst_unequal",
                severity=Severity.warn,
                message=f"CGST ({_money(t.cgst)}) and SGST ({_money(t.sgst)}) should be equal.",
                fields=("taxes",),
                expected=t.cgst,
                actual=t.sgst,
            )
        )
    if bool(t.cgst) != bool(t.sgst) and not t.igst:
        findings.append(
            Finding(
                code="gst_half_missing",
                severity=Severity.warn,
                message="Only one of CGST/SGST is printed; same-state bills charge both halves.",
                fields=("taxes",),
            )
        )
    return findings


def _aggregate_bases(r: ExtractedReceipt) -> list[float]:
    """Amounts GST can be charged on when the whole bill is taxed at one rate."""
    if not r.subtotal:
        return []
    discount, service = r.discount or 0.0, r.service_charge or 0.0
    bases = (
        r.subtotal,
        r.subtotal - discount,
        r.subtotal + service,
        r.subtotal - discount + service,
    )
    return sorted({round(b, 2) for b in bases if b > 0})


def _tax_bases(r: ExtractedReceipt) -> list[float]:
    """Everything the printed tax may have been charged on.

    Besides the whole bill (before or after the discount, with or without the service charge)
    that includes any single row: a flight ticket taxes the base fare but not the fees, so its tax
    is 5% of one row and only about 4% of the subtotal.
    """
    return [*_aggregate_bases(r), *(i.amount for i in r.line_items if i.amount > 0)]


def _check_rate(r: ExtractedReceipt) -> list[Finding]:
    t = r.taxes
    tax = sum(x or 0.0 for x in (t.cgst, t.sgst, t.igst))
    if not tax or not r.subtotal:
        return []
    effective = 100 * tax / r.subtotal
    findings: list[Finding] = []
    if t.gst_rate_percent is not None:
        stated = t.gst_rate_percent
        if all(abs(100 * tax / base - stated) > RATE_TOLERANCE_PP for base in _tax_bases(r)):
            findings.append(
                Finding(
                    code="gst_rate_mismatch",
                    severity=Severity.warn,
                    message=f"Tax charged is {effective:.1f}% of the subtotal, but the bill "
                    f"states {stated:g}% GST.",
                    fields=("taxes",),
                    expected=stated,
                    actual=round(effective, 2),
                )
            )
    else:
        rates = [100 * tax / base for base in _aggregate_bases(r)]
        if all(abs(rate - std) > RATE_TOLERANCE_PP for rate in rates for std in STANDARD_GST_RATES):
            findings.append(
                Finding(
                    code="gst_rate_nonstandard",
                    severity=Severity.info,
                    message=f"Tax works out to {effective:.1f}%, which is not a standard GST rate.",
                    fields=("taxes",),
                    actual=round(effective, 2),
                )
            )
    return findings


def _check_gstin(r: ExtractedReceipt) -> list[Finding]:
    if not r.merchant_gstin:
        has_gst = any((r.taxes.cgst, r.taxes.sgst, r.taxes.igst))
        if not has_gst:
            return []
        return [
            Finding(
                code="gstin_missing",
                severity=Severity.warn,
                message="GST is charged but no GSTIN is printed, so input credit can't be claimed.",
                fields=("merchant_gstin",),
            )
        ]
    valid, reason = validate_gstin(r.merchant_gstin)
    if valid:
        return []
    detail = {
        "format": "is not in the 15-character GSTIN format",
        "state_code": "starts with an unknown state code",
        "checksum": "fails the GSTIN checksum (likely mistyped or invented)",
    }[reason or "format"]
    return [
        Finding(
            code=f"gstin_invalid_{reason}",
            severity=Severity.high,
            message=f"The GSTIN {plain_text(r.merchant_gstin, 15)} {detail}.",
            fields=("merchant_gstin",),
            actual=plain_text(r.merchant_gstin, 15),
        )
    ]
