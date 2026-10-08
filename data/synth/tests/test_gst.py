from __future__ import annotations

import pytest
from claimpilot.domain import DocType, ExtractedReceipt, LineItem, TaxBreakup

from synthgen.gst import expected_total, hotel_rate, money, reconciles, tax_breakup
from synthgen.textfmt import format_date, format_time, indian_grouping, rupees_in_words


@pytest.mark.parametrize(
    ("value", "expected"),
    [(2.675, 2.68), (2.665, 2.67), (10, 10.0), ("0.005", 0.01), (-1.005, -1.01)],
)
def test_money_rounds_half_up(value: float, expected: float) -> None:
    assert money(value) == expected


def test_intra_state_splits_cgst_and_sgst() -> None:
    taxes = tax_breakup(1000, 5, inter_state=False)
    assert (taxes.cgst, taxes.sgst, taxes.igst, taxes.gst_rate_percent) == (25.0, 25.0, None, 5)


def test_inter_state_uses_igst() -> None:
    taxes = tax_breakup(1000, 18, inter_state=True, print_rate=False)
    assert (taxes.cgst, taxes.sgst, taxes.igst, taxes.gst_rate_percent) == (None, None, 180.0, None)


@pytest.mark.parametrize(("tariff", "rate"), [(4500, 5), (7500, 5), (7500.5, 18), (12000, 18)])
def test_hotel_rate_threshold(tariff: float, rate: float) -> None:
    assert hotel_rate(tariff) == rate


def _receipt(**fields: object) -> ExtractedReceipt:
    items = [LineItem(description="Thali", quantity=2, unit_price=250, amount=500)]
    return ExtractedReceipt(doc_type=DocType.restaurant_bill, line_items=items, **fields)


def test_reconcile_counts_service_charge_and_discount() -> None:
    receipt = _receipt(
        taxes=TaxBreakup(cgst=12.5, sgst=12.5),
        service_charge=50,
        discount=50,
        total=525,
    )
    assert expected_total(receipt) == 525
    assert reconciles(receipt)
    assert not reconciles(receipt.model_copy(update={"total": 526}))
    assert not reconciles(receipt.model_copy(update={"total": None}))


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (0, "0.00"),
        (999.5, "999.50"),
        (1234.5, "1,234.50"),
        (123456.78, "1,23,456.78"),
        (12345678, "1,23,45,678.00"),
    ],
)
def test_indian_grouping(amount: float, expected: str) -> None:
    assert indian_grouping(amount) == expected


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (9440, "Rupees Nine Thousand Four Hundred Forty Only"),
        (1250000, "Rupees Twelve Lakh Fifty Thousand Only"),
        (714.76, "Rupees Seven Hundred Fourteen and Paise Seventy Six Only"),
        (10_000_000, "Rupees One Crore Only"),
    ],
)
def test_rupees_in_words(amount: float, expected: str) -> None:
    assert rupees_in_words(amount) == expected


def test_date_and_time_formats() -> None:
    assert format_date("2026-09-07", "dmy_slash") == "07/09/2026"
    assert format_date("2026-09-07", "d_mon_y") == "07-Sep-2026"
    assert format_date(None, "iso") == ""
    assert format_time("21:05", twelve_hour=True) == "09:05 PM"
    assert format_time("21:05", twelve_hour=False) == "21:05"
