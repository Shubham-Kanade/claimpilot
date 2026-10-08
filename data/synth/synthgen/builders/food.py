"""Thermal restaurant bills (58/80 mm), including client dinners and alcohol lines."""

from __future__ import annotations

from claimpilot.domain import (
    DocType,
    ExpenseCategory,
    ExtractedReceipt,
    LineItem,
    PaymentMethod,
    generate_gstin,
)

from synthgen.builders.common import (
    THERMAL_FONTS,
    BuildContext,
    clock,
    date_style,
    digits,
    phone,
    pick_payment,
    priced_line,
    rupees,
    street_address,
)
from synthgen.catalog import ALCOHOL, MENU, RESTAURANT_PREFIXES, RESTAURANT_SUFFIXES
from synthgen.gst import RESTAURANT_RATE, money, tax_breakup, total_tax
from synthgen.spec import Draft


def build_restaurant_bill(
    ctx: BuildContext,
    *,
    covers: int = 1,
    upscale: bool = False,
    alcohol: bool = False,
    category: ExpenseCategory = ExpenseCategory.meals,
) -> Draft:
    """A dine-in bill. GST (5%, CGST + SGST) applies to food and service charge, not alcohol."""
    rng, city = ctx.rng, ctx.city
    hindi = rng.random() < (0.55 if city.hindi_belt else 0.2)
    markup = 1.4 if upscale else 1.0

    dishes = rng.sample(MENU, min(len(MENU), rng.randint(2, 3) + covers))
    food: list[LineItem] = []
    for dish in dishes:
        name = dish.hi if hindi and rng.random() < 0.6 else dish.en
        quantity = rng.randint(1, max(1, (covers + 1) // 2))
        food.append(priced_line(name, quantity, rupees(rng, dish.low * markup, dish.high * markup)))
    drinks: list[LineItem] = []
    if alcohol:
        for drink in rng.sample(ALCOHOL, rng.randint(1, 2)):
            drinks.append(
                priced_line(
                    drink.en, rng.randint(1, max(1, covers)), rupees(rng, drink.low, drink.high)
                )
            )

    food_total = money(sum(item.amount for item in food))
    discount = money(food_total * rng.choice((5, 10, 15)) / 100) if rng.random() < 0.15 else None
    service = (
        money(food_total * rng.choice((5, 10)) / 100) if upscale and rng.random() < 0.4 else None
    )
    taxable = money(food_total - (discount or 0) + (service or 0))
    taxes = tax_breakup(taxable, RESTAURANT_RATE, inter_state=False, print_rate=rng.random() < 0.8)
    subtotal = money(food_total + sum(item.amount for item in drinks))
    total = money(subtotal - (discount or 0) + (service or 0) + total_tax(taxes))

    payment = pick_payment(
        rng,
        {
            PaymentMethod.card: 4,
            PaymentMethod.upi: 4,
            PaymentMethod.cash: 2,
            PaymentMethod.unknown: 1,
        },
    )
    receipt = ExtractedReceipt(
        doc_type=DocType.restaurant_bill,
        merchant_name=f"{rng.choice(RESTAURANT_PREFIXES)} {rng.choice(RESTAURANT_SUFFIXES)}",
        merchant_gstin=generate_gstin(city.state_code, rng),
        merchant_city=city.name,
        invoice_number=rng.choice(("B", "INV", "R", "")) + digits(rng, rng.randint(3, 5)),
        date=ctx.day.isoformat(),
        time=clock(rng, 19, 23) if covers > 1 else clock(rng, 12, 22),
        line_items=[*food, *drinks],
        subtotal=subtotal,
        taxes=taxes,
        service_charge=service,
        discount=discount,
        total=total,
        payment_method=payment,
    )
    extras = {
        "address": street_address(rng, ctx.fake, city),
        "phone": phone(rng, city),
        "fssai": digits(rng, 14),
        "table": f"T{rng.randint(1, 32)}",
        "covers": covers,
        "steward": ctx.fake.first_name(),
        "bill_label": rng.choice(("TAX INVOICE", "BILL", "RETAIL INVOICE")),
        "alcohol_note": "Liquor prices are inclusive of VAT" if alcohol else None,
        "footer": "धन्यवाद! फिर पधारें"
        if hindi
        else rng.choice(
            ("Thank you! Visit again", "Thank You. Please visit again!", "** Have a nice day **")
        ),
    }
    style = {
        "width_mm": rng.choice((58, 80, 80)),
        "font": rng.choice(THERMAL_FONTS),
        "date_style": date_style(rng, wordy_share=0.2),
        "twelve_hour": rng.random() < 0.5,
    }
    return Draft(receipt=receipt, category=category, extras=extras, style=style)
