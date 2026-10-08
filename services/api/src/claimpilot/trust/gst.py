"""Arithmetic and GST consistency checks on an extracted receipt (India-native trust signals).

Extraction reports what is *printed*; these checks find printed documents that don't add up,
which is how edited totals and sloppy fakes usually give themselves away.
"""

from __future__ import annotations

from claimpilot.domain import ExtractedReceipt, validate_gstin
from claimpilot.domain.findings import Finding, Severity

# Printed bills round the grand total to the rupee (and some print a separate round-off line the
# schema does not capture), so totals within ₹1 are consistent.
TOTAL_TOLERANCE = 1.0
LINE_TOLERANCE = 0.05
RATE_TOLERANCE_PP = 0.6  # percentage points; rates are printed rounded
STANDARD_GST_RATES = (0.0, 0.25, 3.0, 5.0, 12.0, 18.0, 28.0, 40.0)  # 40%: 2025 demerit slab


def _money(value: float) -> str:
    return f"₹{value:,.2f}"


def check_gst(receipt: ExtractedReceipt) -> list[Finding]:
    findings: list[Finding] = []
    findings += _check_line_items(receipt)
    findings += _check_total(receipt)
    findings += _check_tax_structure(receipt)
    findings += _check_rate(receipt)
    findings += _check_gstin(receipt)
    return findings


def _check_line_items(r: ExtractedReceipt) -> list[Finding]:
    if r.subtotal is None or not r.line_items:
        return []
    items = round(sum(i.amount for i in r.line_items), 2)
    if abs(items - r.subtotal) <= LINE_TOLERANCE:
        return []
    return [
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


def _check_rate(r: ExtractedReceipt) -> list[Finding]:
    t = r.taxes
    tax = sum(x or 0.0 for x in (t.cgst, t.sgst, t.igst))
    if not tax or not r.subtotal:
        return []
    effective = 100 * tax / r.subtotal
    findings: list[Finding] = []
    if t.gst_rate_percent is not None and abs(effective - t.gst_rate_percent) > RATE_TOLERANCE_PP:
        findings.append(
            Finding(
                code="gst_rate_mismatch",
                severity=Severity.warn,
                message=f"Tax charged is {effective:.1f}% of the subtotal, but the bill states "
                f"{t.gst_rate_percent:g}% GST.",
                fields=("taxes",),
                expected=t.gst_rate_percent,
                actual=round(effective, 2),
            )
        )
    elif all(abs(effective - rate) > RATE_TOLERANCE_PP for rate in STANDARD_GST_RATES):
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
            message=f"The GSTIN {r.merchant_gstin} {detail}.",
            fields=("merchant_gstin",),
            actual=r.merchant_gstin,
        )
    ]
