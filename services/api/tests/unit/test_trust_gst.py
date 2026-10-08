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


def test_items_adding_up_to_more_than_the_subtotal_is_high_severity():
    r = bill(
        subtotal=350.0,
        taxes=TaxBreakup(cgst=8.75, sgst=8.75, gst_rate_percent=5),
        total=367.5,
    )
    findings = check_gst(r)
    assert [f.code for f in findings] == ["items_subtotal_mismatch"]
    assert findings[0].severity is Severity.high
    assert findings[0].expected == 400.0 and findings[0].actual == 350.0


def test_items_adding_up_to_less_than_the_subtotal_only_asks_for_a_look():
    # a row the extraction skipped looks exactly like this; it must not block a genuine bill
    findings = check_gst(bill(subtotal=450.0, total=470.0))
    assert [f.code for f in findings] == ["items_incomplete"]
    assert findings[0].severity is Severity.warn
    assert "50.00 less than the subtotal" in findings[0].message


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


# --- which amount the tax was charged on -------------------------------------------------------


def ticket(items: list[tuple[str, float]], **overrides) -> ExtractedReceipt:
    """A flight e-ticket: 5% GST on the base fare only, none on the fees."""
    base = {
        "doc_type": DocType.flight_ticket,
        "merchant_gstin": VALID_GSTIN,
        "line_items": [LineItem(description=name, amount=amount) for name, amount in items],
        "subtotal": 8517.0,
        "taxes": TaxBreakup(igst=358.95, gst_rate_percent=5),
        "total": 8875.95,
    }
    base.update(overrides)
    return ExtractedReceipt(**base)


FARE_AND_FEES = [
    ("Base Fare", 7179.0),
    ("User Development Fee", 481.0),
    ("Passenger Service Fee", 226.0),
    ("Aviation Security Fee", 236.0),
    ("Convenience Fee", 395.0),
]


def test_a_ticket_taxed_on_the_base_fare_only_is_consistent():
    assert codes(ticket(FARE_AND_FEES)) == []


def test_a_ticket_whose_fee_rows_were_not_read_is_incomplete_not_tampered():
    assert codes(ticket(FARE_AND_FEES[:1])) == ["items_incomplete"]


def test_tax_on_the_discounted_bill_is_consistent():
    # 5% of (400 - 40) = 18, not 5% of 400
    r = bill(discount=40.0, taxes=TaxBreakup(cgst=9.0, sgst=9.0, gst_rate_percent=5), total=378.0)
    assert codes(r) == []


def test_tax_on_the_bill_with_the_service_charge_is_consistent():
    # 5% of (400 + 40) = 22
    r = bill(
        service_charge=40.0,
        taxes=TaxBreakup(cgst=11.0, sgst=11.0, gst_rate_percent=5),
        total=462.0,
    )
    assert codes(r) == []


def test_inflated_tax_is_flagged_even_when_a_single_row_is_a_candidate_base():
    # 60 of tax on a 400 bill is 15%, 24% of the 250 row and 40% of the 150 row, never 5%
    r = bill(taxes=TaxBreakup(cgst=30.0, sgst=30.0, gst_rate_percent=5), total=460.0)
    assert codes(r) == ["gst_rate_mismatch"]


def test_a_nonstandard_rate_is_not_excused_by_matching_one_row():
    # 7.5% of the bill, but 12% of the 250 row: the bill as a whole is what is nonstandard
    r = bill(taxes=TaxBreakup(cgst=15.0, sgst=15.0), total=430.0)
    assert codes(r) == ["gst_rate_nonstandard"]


# --- figures the reader was unsure of ----------------------------------------------------------


def test_a_mismatch_on_a_figure_the_reader_was_unsure_of_asks_for_a_check():
    findings = check_gst(bill(total=520.0, low_confidence_fields=["total"]))
    assert [f.code for f in findings] == ["total_mismatch"]
    assert findings[0].severity is Severity.warn
    assert findings[0].message.endswith("compare it with the original.")


def test_a_mismatch_on_a_confidently_read_figure_stays_high_even_if_other_fields_were_unsure():
    findings = check_gst(bill(total=520.0, low_confidence_fields=["date", "merchant_city"]))
    assert [(f.code, f.severity) for f in findings] == [("total_mismatch", Severity.high)]


def test_an_unreadable_gstin_that_fails_its_checksum_is_a_warning():
    findings = check_gst(
        bill(merchant_gstin="27AAPFU0939F1ZW", low_confidence_fields=["merchant_gstin"])
    )
    assert [(f.code, f.severity) for f in findings] == [("gstin_invalid_checksum", Severity.warn)]


def test_line_items_the_reader_doubted_soften_the_subtotal_check():
    r = bill(subtotal=350.0, low_confidence_fields=["line_items"],
             taxes=TaxBreakup(cgst=8.75, sgst=8.75, gst_rate_percent=5), total=367.5)  # fmt: skip
    assert [(f.code, f.severity) for f in check_gst(r)] == [
        ("items_subtotal_mismatch", Severity.warn)
    ]
