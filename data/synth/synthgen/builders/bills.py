"""Bills and invoices: postpaid mobile bill (PDF), A4 GST tax invoice (PDF) and fuel slips."""

from __future__ import annotations

from datetime import timedelta

from claimpilot.domain import (
    DocType,
    ExpenseCategory,
    ExtractedReceipt,
    LineItem,
    PaymentMethod,
    generate_gstin,
)

from synthgen.builders.common import (
    ACCENTS,
    THERMAL_FONTS,
    BuildContext,
    clock,
    date_style,
    digits,
    financial_year,
    letters,
    line,
    phone,
    pick_payment,
    priced_line,
    rupees,
    street_address,
    vehicle_number,
)
from synthgen.catalog import (
    COURSES,
    EDTECH_BRANDS,
    ELECTRONICS_STORES,
    FUEL_BRANDS,
    FUEL_PRODUCTS,
    MOBILE_ADDONS,
    MOBILE_PLANS,
    TELECOM_BRANDS,
    WFH_PRODUCTS,
)
from synthgen.geo import CITY_BY_NAME, City
from synthgen.gst import (
    GOODS_RATE,
    SERVICES_RATE,
    TELECOM_RATE,
    is_inter_state,
    money,
    tax_breakup,
    total_tax,
)
from synthgen.spec import Draft


def _sum(items: list[LineItem]) -> float:
    return money(sum(item.amount for item in items))


def build_mobile_bill(ctx: BuildContext) -> Draft:
    """Postpaid bill dated ``ctx.day`` for the previous cycle; 18% GST in the subscriber's
    circle (intra-state)."""
    rng, city = ctx.rng, ctx.persona.base_city
    plan_name, rental = rng.choice(MOBILE_PLANS)
    items = [line(f"Monthly Rental - {plan_name}", rental)]
    for name, price in rng.sample(MOBILE_ADDONS, rng.randint(0, 2)):
        items.append(line(name, price))
    if rng.random() < 0.5:
        items.append(line("Usage Charges (calls/SMS beyond plan)", rupees(rng, 8, 140, step=1)))
    taxes = tax_breakup(_sum(items), TELECOM_RATE, inter_state=False)
    total = money(_sum(items) + total_tax(taxes))
    cycle_start, cycle_end = ctx.day - timedelta(days=30), ctx.day - timedelta(days=1)
    receipt = ExtractedReceipt(
        doc_type=DocType.mobile_bill,
        merchant_name=rng.choice(TELECOM_BRANDS),
        merchant_gstin=generate_gstin(city.state_code, rng),
        merchant_city=city.name,
        invoice_number=f"MB{ctx.day:%y%m}{digits(rng, 8)}",
        date=ctx.day.isoformat(),
        line_items=items,
        subtotal=_sum(items),
        taxes=taxes,
        total=total,
    )
    previous = rupees(rng, 499, 1400, step=1)
    extras = {
        "office": street_address(rng, ctx.fake, city),
        "customer": ctx.persona.name,
        "customer_address": street_address(rng, ctx.fake, city),
        "mobile": ctx.persona.mobile_masked,
        "account_no": digits(rng, 10),
        "cycle_start": cycle_start.isoformat(),
        "cycle_end": cycle_end.isoformat(),
        "due_date": (ctx.day + timedelta(days=15)).isoformat(),
        "previous_balance": previous,
        "payment_received": previous,
        "data_used_gb": round(rng.uniform(8, 70), 2),
        "voice_minutes": rng.randint(150, 2400),
    }
    style = {"date_style": date_style(rng, wordy_share=0.6), "accent": rng.choice(ACCENTS)}
    return Draft(receipt, ExpenseCategory.mobile_internet, extras, style)


def build_gst_invoice(ctx: BuildContext, *, purpose: str = "course") -> Draft:
    """A4 tax invoice to the employee: an online course (supplier often in another state, so
    IGST) or work-from-home equipment from a local store (CGST + SGST). 18% GST."""
    rng = ctx.rng
    buyer_city = ctx.persona.base_city
    if purpose == "course":
        brand = rng.choice(EDTECH_BRANDS)
        supplier_city: City = CITY_BY_NAME[brand.hq_city]
        supplier, category, rate = brand.name, ExpenseCategory.learning, SERVICES_RATE
        products = [rng.choice(COURSES)]
    else:
        supplier_city = buyer_city
        supplier, category, rate = (
            rng.choice(ELECTRONICS_STORES),
            ExpenseCategory.wfh_supplies,
            GOODS_RATE,
        )
        products = rng.sample(WFH_PRODUCTS, rng.randint(1, 3))
    if purpose == "course":
        items = [
            priced_line(p.description, 1, rupees(rng, p.low, p.high, step=500)) for p in products
        ]
    else:
        items = [
            priced_line(p.description, rng.randint(1, 2), rupees(rng, p.low, p.high, step=1))
            for p in products
        ]
    inter = is_inter_state(supplier_city.state_code, buyer_city.state_code)
    taxes = tax_breakup(_sum(items), rate, inter_state=inter)
    receipt = ExtractedReceipt(
        doc_type=DocType.gst_invoice,
        merchant_name=supplier,
        merchant_gstin=generate_gstin(supplier_city.state_code, rng),
        merchant_city=supplier_city.name,
        invoice_number=f"{letters(rng, 3)}/{financial_year(ctx.day)}/{digits(rng, 4)}",
        date=ctx.day.isoformat(),
        line_items=items,
        subtotal=_sum(items),
        taxes=taxes,
        total=money(_sum(items) + total_tax(taxes)),
        payment_method=pick_payment(
            rng, {PaymentMethod.card: 4, PaymentMethod.upi: 3, PaymentMethod.netbanking: 2}
        ),
    )
    extras = {
        "address": street_address(rng, ctx.fake, supplier_city),
        "phone": phone(rng, supplier_city),
        "email": f"billing@{supplier.split()[0].lower()}.example",
        "buyer": ctx.persona.name,
        "buyer_address": street_address(rng, ctx.fake, buyer_city),
        "place_of_supply": f"{buyer_city.state_code}-{buyer_city.name}",
        "hsn": [p.hsn_sac for p in products],
        "reverse_charge": "No",
        "signatory": ctx.fake.name(),
    }
    style = {"date_style": date_style(rng, wordy_share=0.3), "accent": rng.choice(ACCENTS)}
    return Draft(receipt, category, extras, style)


def build_fuel_slip(ctx: BuildContext) -> Draft:
    """Pump slip. Fuel is outside GST, so no tax lines; a GSTIN is printed on some slips.
    Most fills are preset amounts, so volume x rate is only approximately the amount."""
    rng, city = ctx.rng, ctx.city
    product = rng.choice(list(FUEL_PRODUCTS))
    rate = round(rng.uniform(*FUEL_PRODUCTS[product]), 2)
    if rng.random() < 0.7:
        amount = float(rng.choice((300, 500, 1000, 1500, 2000, 2500, 3000)))
        volume = round(amount / rate, 2)
    else:
        volume = round(rng.uniform(8, 40), 2)
        amount = money(volume * rate)
    brand = rng.choice(FUEL_BRANDS)
    receipt = ExtractedReceipt(
        doc_type=DocType.fuel_slip,
        merchant_name=f"{brand} - {rng.choice(city.localities)}",
        merchant_gstin=generate_gstin(city.state_code, rng) if rng.random() < 0.5 else None,
        merchant_city=city.name,
        invoice_number=digits(rng, 6),
        date=ctx.day.isoformat(),
        time=clock(rng, 7, 22),
        line_items=[line(product, amount, quantity=volume, unit_price=rate)],
        total=amount,
        payment_method=pick_payment(
            rng, {PaymentMethod.cash: 3, PaymentMethod.card: 3, PaymentMethod.upi: 4}
        ),
    )
    extras = {
        "address": street_address(rng, ctx.fake, city),
        "pump_id": f"{rng.randint(1, 8)}",
        "nozzle": f"{rng.randint(1, 4)}",
        "vehicle": vehicle_number(rng, city),
        "density": f"{rng.uniform(730, 760):.1f} kg/m3",
        "attendant": ctx.fake.first_name(),
    }
    style = {
        "width_mm": 58,
        "font": rng.choice(THERMAL_FONTS),
        "date_style": rng.choice(("dmy_slash", "dmy_dash")),
        "twelve_hour": False,
    }
    return Draft(receipt, ExpenseCategory.fuel_vehicle, extras, style)
