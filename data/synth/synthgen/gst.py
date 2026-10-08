"""Money rounding and Indian GST rules used to build (and check) receipt ground truth.

Rates follow GST 2.0 (effective 22 Sep 2025): restaurants 5%, hotel rooms 5% up to Rs 7,500 per
night and 18% above, cabs 5%, economy flights 5%, AC train fares 5%, telecom and professional
services 18%.
Fuel and informal (auto, kirana, handwritten) bills carry no GST. An inter-state supply (supplier
state differs from the place of supply) is taxed as IGST; an intra-state one as CGST + SGST.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from claimpilot.domain import ExtractedReceipt, TaxBreakup

CENT = Decimal("0.01")

RESTAURANT_RATE = 5.0
CAB_RATE = 5.0
FLIGHT_ECONOMY_RATE = 5.0
TRAIN_AC_RATE = 5.0
TELECOM_RATE = 18.0
SERVICES_RATE = 18.0
GOODS_RATE = 18.0
HOTEL_STANDARD_RATE = 5.0  # GST 2.0 (22 Sep 2025); was 12% before
HOTEL_PREMIUM_RATE = 18.0
HOTEL_STANDARD_TARIFF_CAP = 7500.0


def money(value: float | int | str | Decimal) -> float:
    """Round half-up to 2 decimals (the way Indian billing software rounds)."""
    return float(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP))


def hotel_rate(tariff_per_night: float) -> float:
    """GST rate on a hotel room for a given per-night tariff."""
    return (
        HOTEL_STANDARD_RATE if tariff_per_night <= HOTEL_STANDARD_TARIFF_CAP else HOTEL_PREMIUM_RATE
    )


def is_inter_state(supplier_state_code: str, place_of_supply_state_code: str) -> bool:
    return supplier_state_code != place_of_supply_state_code


def tax_breakup(
    taxable: float, rate_percent: float, *, inter_state: bool, print_rate: bool = True
) -> TaxBreakup:
    """Split GST on ``taxable`` into IGST (inter-state) or equal CGST + SGST halves."""
    rate_shown = rate_percent if print_rate else None
    if inter_state:
        return TaxBreakup(igst=money(taxable * rate_percent / 100), gst_rate_percent=rate_shown)
    half = money(taxable * rate_percent / 200)
    return TaxBreakup(cgst=half, sgst=half, gst_rate_percent=rate_shown)


def total_tax(taxes: TaxBreakup) -> float:
    parts = (taxes.cgst, taxes.sgst, taxes.igst, taxes.cess)
    return money(sum(part for part in parts if part is not None))


def items_total(receipt: ExtractedReceipt) -> float:
    return money(sum(Decimal(str(item.amount)) for item in receipt.line_items))


def expected_total(receipt: ExtractedReceipt) -> float:
    """Line items + taxes + service charge - discount, rounded to 2 decimals."""
    return money(
        Decimal(str(items_total(receipt)))
        + Decimal(str(total_tax(receipt.taxes)))
        + Decimal(str(receipt.service_charge or 0))
        - Decimal(str(receipt.discount or 0))
    )


def reconciles(receipt: ExtractedReceipt) -> bool:
    """True when the printed total equals the arithmetic of the printed parts."""
    return receipt.total is not None and expected_total(receipt) == money(receipt.total)
