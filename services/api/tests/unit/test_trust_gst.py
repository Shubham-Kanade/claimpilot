from __future__ import annotations

import pytest

from claimpilot.domain import DocType, ExtractedReceipt, LineItem, TaxBreakup
from claimpilot.domain.findings import Severity
from claimpilot.trust import check_gst

VALID_GSTIN = "27AAPFU0939F1ZV"


def bill(**overrides) -> ExtractedReceipt:
    """A consistent intra-state 5% restaurant bill: 400 + 10 + 10 = 420."""
    base = {
        "doc_type": DocType.restaurant_bill,
        "merchant_gstin": VALID_GSTIN,
        "line_items": [
            LineItem(description="Thali", amount=250.0),
            LineItem(description="Lassi", amount=150.0),
        ],
        "subtotal": 400.0,
        "taxes": TaxBreakup(cgst=10.0, sgst=10.0, gst_rate_percent=5),
        "total": 420.0,
    }
    base.update(overrides)
    return ExtractedReceipt(**base)


def codes(receipt: ExtractedReceipt) -> list[str]:
    return [f.code for f in check_gst(receipt)]


def test_consistent_bill_has_no_findings():
    assert codes(bill()) == []


def test_rupee_round_off_is_tolerated():
    assert codes(bill(total=420.4)) == []


def test_tampered_total_is_high_severity():
    findings = check_gst(bill(total=520.0))
    assert [f.code for f in findings] == ["total_mismatch"]
    assert findings[0].severity is Severity.high
    assert findings[0].expected == 420.0 and findings[0].actual == 520.0


def test_items_not_matching_subtotal():
    assert "items_subtotal_mismatch" in codes(bill(subtotal=450.0, total=470.0))


def test_service_charge_and_discount_reconcile():
    assert codes(bill(service_charge=40.0, discount=20.0, total=440.0)) == []


def test_total_without_subtotal_uses_line_items():
    assert codes(bill(subtotal=None, total=420.0)) == []
    assert codes(bill(subtotal=None, total=900.0)) == ["total_mismatch"]


def test_mixed_cgst_and_igst():
    r = bill(taxes=TaxBreakup(cgst=10.0, sgst=10.0, igst=20.0), total=440.0)
    assert "gst_mixed_intra_inter" in codes(r)


def test_unequal_halves_and_missing_half():
    assert "cgst_sgst_unequal" in codes(bill(taxes=TaxBreakup(cgst=10.0, sgst=12.0), total=422.0))
    assert "gst_half_missing" in codes(bill(taxes=TaxBreakup(cgst=10.0), total=410.0))


def test_inter_state_igst_is_fine():
    r = bill(taxes=TaxBreakup(igst=20.0, gst_rate_percent=5), total=420.0)
    assert codes(r) == []


def test_stated_rate_mismatch():
    r = bill(taxes=TaxBreakup(cgst=10.0, sgst=10.0, gst_rate_percent=18), total=420.0)
    assert codes(r) == ["gst_rate_mismatch"]


def test_nonstandard_rate_without_stated_rate_is_info():
    r = bill(taxes=TaxBreakup(cgst=15.0, sgst=15.0), total=430.0)  # 7.5%
    findings = check_gst(r)
    assert [f.code for f in findings] == ["gst_rate_nonstandard"]
    assert findings[0].severity is Severity.info


@pytest.mark.parametrize(
    ("gstin", "code"),
    [
        ("27AAPFU0939F1ZW", "gstin_invalid_checksum"),
        ("99AAPFU0939F1ZV", "gstin_invalid_state_code"),
        ("NOT-A-GSTIN", "gstin_invalid_format"),
    ],
)
def test_invalid_gstins(gstin, code):
    assert codes(bill(merchant_gstin=gstin)) == [code]


def test_gst_charged_without_gstin():
    assert codes(bill(merchant_gstin=None)) == ["gstin_missing"]


def test_no_gst_no_gstin_is_fine():
    r = ExtractedReceipt(
        doc_type=DocType.handwritten_bill,
        total=80.0,
        line_items=[LineItem(description="Auto", amount=80.0)],
    )
    assert codes(r) == []
