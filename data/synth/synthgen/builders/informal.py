"""Informal proofs of payment: UPI success screenshots and handwritten auto / kirana bills.

None of these carry GST. A UPI screenshot prints no line items, merchant city or GSTIN, so those
truth fields stay empty.
"""

from __future__ import annotations

from claimpilot.domain import DocType, ExpenseCategory, ExtractedReceipt, PaymentMethod

from synthgen.builders.common import (
    BuildContext,
    clock,
    digits,
    line,
    priced_line,
    rupees,
    vehicle_number,
)
from synthgen.catalog import (
    BANKS,
    KIRANA_PANTRY,
    KIRANA_SHOPS,
    KIRANA_STATIONERY,
    UPI_APPS,
    UPI_PAYEES,
)
from synthgen.gst import money
from synthgen.spec import Draft

HANDWRITING_FONTS = ("Kalam", "Caveat")  # Caveat has no Devanagari; Hindi bills use Kalam
INK_COLOURS = ("#1d2a6b", "#14213d", "#222222", "#0b3d91")


def build_upi_payment(ctx: BuildContext, *, auto_fare: bool = False) -> Draft:
    """'Payment successful' phone screenshot from a fictional UPI app."""
    rng = ctx.rng
    if auto_fare:
        payee = ctx.fake.first_name().upper() + " " + ctx.fake.last_name().upper()
        category, amount = ExpenseCategory.local_conveyance, rupees(rng, 60, 380, step=10)
        note = f"Auto {rng.choice(ctx.city.localities)}"
        handle = (
            f"{payee.split()[0].lower()}{digits(rng, 3)}@{rng.choice(('okvasudha', 'trishul'))}"
        )
    else:
        payee, category_name, (low, high) = rng.choice(UPI_PAYEES)
        category, amount = ExpenseCategory(category_name), rupees(rng, low, high, step=1)
        note = rng.choice((None, "Team snacks", "Office expense", None))
        handle = f"{payee.split()[0].lower()}.{digits(rng, 4)}@meghdoot"
    receipt = ExtractedReceipt(
        doc_type=DocType.upi_payment,
        merchant_name=payee,
        date=ctx.day.isoformat(),
        time=clock(rng, 8, 23),
        total=money(amount),
        payment_method=PaymentMethod.upi,
        upi_reference=f"{rng.randint(4, 6)}{digits(rng, 11)}",
    )
    extras = {
        "app": rng.choice(UPI_APPS),
        "payee_handle": handle,
        "note": note,
        "bank": rng.choice(BANKS),
        "account_tail": digits(rng, 4),
        "status_time": clock(rng, 8, 23),
        "battery": rng.randint(18, 96),
    }
    style = {
        "date_style": rng.choice(("d_month_y", "d_mon_y")),
        "twelve_hour": rng.random() < 0.8,
        "dark": rng.random() < 0.25,
    }
    return Draft(receipt, category, extras, style)


def build_handwritten_bill(ctx: BuildContext, *, kind: str | None = None) -> Draft:
    """A hand-filled cash memo from a kirana / stationery shop, or an auto-rickshaw fare slip."""
    rng = ctx.rng
    kind = kind or rng.choice(("kirana", "auto"))
    hindi = rng.random() < (0.75 if ctx.city.hindi_belt else 0.35)
    style = {
        "font": "Kalam" if hindi else rng.choice(HANDWRITING_FONTS),
        "ink": rng.choice(INK_COLOURS),
        "date_style": rng.choice(("dmy_slash", "dmy_dash", "dmy_short")),
        "tilt": round(rng.uniform(-1.5, 1.5), 2),
        "kind": kind,
    }
    if kind == "auto":
        return _auto_slip(ctx, hindi, style)
    return _kirana_memo(ctx, hindi, style)


def _kirana_memo(ctx: BuildContext, hindi: bool, style: dict[str, object]) -> Draft:
    rng, city = ctx.rng, ctx.city
    stationery = rng.random() < 0.6
    catalogue = KIRANA_STATIONERY if stationery else KIRANA_PANTRY
    items = []
    for product in rng.sample(catalogue, rng.randint(2, 4)):
        name = product.hi if hindi else product.en
        quantity = rng.randint(1, 3)
        unit = rupees(rng, product.low, product.high)
        items.append(priced_line(name, quantity, unit) if quantity > 1 else line(name, unit))
    receipt = ExtractedReceipt(
        doc_type=DocType.handwritten_bill,
        merchant_name=rng.choice(KIRANA_SHOPS),
        merchant_city=city.name,
        invoice_number=digits(rng, 3),
        date=ctx.day.isoformat(),
        line_items=items,
        total=money(sum(item.amount for item in items)),
        payment_method=PaymentMethod.cash,
        handwritten=True,
    )
    extras = {
        "locality": rng.choice(city.localities),
        "phone_masked": f"98{digits(rng, 3)}XXXXX",
        "memo_label": "कैश मेमो / CASH MEMO" if hindi else "CASH MEMO",
        "customer": ctx.persona.name.split()[0] if rng.random() < 0.5 else None,
    }
    category = ExpenseCategory.wfh_supplies if stationery else ExpenseCategory.misc
    return Draft(receipt, category, extras, style)


def _auto_slip(ctx: BuildContext, hindi: bool, style: dict[str, object]) -> Draft:
    rng, city = ctx.rng, ctx.city
    origin, destination = rng.sample(city.localities, 2)
    driver = f"{ctx.fake.first_name()} {ctx.fake.last_name()}"
    receipt = ExtractedReceipt(
        doc_type=DocType.handwritten_bill,
        merchant_name=driver,
        date=ctx.day.isoformat(),
        total=rupees(rng, 60, 450, step=10),
        payment_method=PaymentMethod.cash,
        handwritten=True,
    )
    extras = {
        "title": "ऑटो रिक्शा किराया रसीद" if hindi else "AUTO RICKSHAW FARE RECEIPT",
        "from_place": origin,
        "to_place": destination,
        "received_label": "प्राप्त किया" if hindi else "Received with thanks",
        "passenger": ctx.persona.name,
        "auto_no": vehicle_number(rng, city),
    }
    return Draft(receipt, ExpenseCategory.local_conveyance, extras, style)
