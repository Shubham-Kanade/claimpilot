"""How the demo pile prints a restaurant bill, a cab receipt and a rail ticket.

The same arithmetic and printed layout as the generator's builders, but every value is passed in:
nothing is drawn at random, so the story's documents read exactly as written in ``story.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from claimpilot.domain import (
    DocType,
    ExpenseCategory,
    ExtractedReceipt,
    LineItem,
    PaymentMethod,
    generate_gstin,
)

from synthgen.assemble import assemble
from synthgen.builders.common import line
from synthgen.builders.travel import CAB_TARIFFS
from synthgen.demo.persona import DEMO_EMPLOYEE, DEMO_PERSONA_ID, STORY_SEED, TRIP_ID
from synthgen.geo import CITY_BY_NAME, City
from synthgen.gst import CAB_RATE, RESTAURANT_RATE, TRAIN_AC_RATE, money, tax_breakup, total_tax
from synthgen.rng import derive_rng
from synthgen.spec import DocSpec, Draft

PUNE, MUMBAI, NEW_DELHI = (CITY_BY_NAME[name] for name in ("Pune", "Mumbai", "New Delhi"))
PORTAL = "RailYatra e-Ticketing"  # the (fictional) rail booking portal, registered in Delhi


class DemoSpec(DocSpec):
    """A spec that can ask for a PDF although its type is normally a picture (an emailed folio)."""

    @property
    def is_pdf(self) -> bool:
        return super().is_pdf or bool(self.style.get("pdf"))


@dataclass(frozen=True)
class DemoDoc:
    """One document of the pile, with the sentences the README and the video need."""

    spec: DocSpec
    what: str  # what it is
    why: str  # why it is in the pile
    expect: str  # what ClaimPilot should do with it
    claim: str  # the claim it is expected to land in

    @property
    def id(self) -> str:
        return self.spec.id


@dataclass(frozen=True)
class Venue:
    """A fictional merchant's identity: name, city, street, PIN and landline."""

    name: str
    city: City
    street: str
    pin: str
    phone: str

    @property
    def address(self) -> list[str]:
        """Two printed lines; the second one names the city, like the generator's addresses."""
        return [self.street, f"{self.city.name} - {self.pin}"]


def gstin(merchant: str, state_code: str) -> str:
    """A valid-checksum GSTIN with a random (fictional) PAN part, fixed per merchant and state.

    A real merchant prints one GSTIN per state on every bill, so two bills of the same merchant in
    the pile must agree.
    """
    return generate_gstin(state_code, derive_rng(STORY_SEED, "gstin", merchant, state_code))


def spec_for(
    draft: Draft,
    slug: str,
    seed: int,
    *,
    degrade: str,
    trip: bool = False,
    tags: tuple[str, ...] = (),
) -> DemoSpec:
    """Turn a hand-built draft into a spec; ``seed`` only decides how its picture looks."""
    spec = assemble(
        draft,
        doc_id=slug,
        seed=seed,
        persona_id=DEMO_PERSONA_ID,
        trip_id=TRIP_ID if trip else None,
        extra_tags=tags,
        degrade=degrade,
    )
    return DemoSpec.model_validate(spec.model_dump())


def restaurant(
    slug: str,
    venue: Venue,
    *,
    day: date,
    time: str,
    invoice: str,
    fssai: str,
    covers: int,
    table: str,
    steward: str,
    food: list[LineItem],
    drinks: list[LineItem] | None = None,
    category: ExpenseCategory = ExpenseCategory.meals,
    payment: PaymentMethod = PaymentMethod.card,
    print_rate: bool = True,
    footer: str = "Thank you! Visit again",
    font: str = "Courier Prime",
    twelve_hour: bool = True,
) -> Draft:
    """A thermal dine-in bill. GST (5%, CGST + SGST) is charged on food only, never on liquor."""
    drinks = drinks or []
    food_total = money(sum(item.amount for item in food))
    taxes = tax_breakup(food_total, RESTAURANT_RATE, inter_state=False, print_rate=print_rate)
    subtotal = money(food_total + sum(item.amount for item in drinks))
    receipt = ExtractedReceipt(
        doc_type=DocType.restaurant_bill,
        merchant_name=venue.name,
        merchant_gstin=gstin(venue.name, venue.city.state_code),
        merchant_city=venue.city.name,
        invoice_number=invoice,
        date=day.isoformat(),
        time=time,
        line_items=[*food, *drinks],
        subtotal=subtotal,
        taxes=taxes,
        total=money(subtotal + total_tax(taxes)),
        payment_method=payment,
    )
    extras = {
        "address": venue.address,
        "phone": venue.phone,
        "fssai": fssai,
        "table": table,
        "covers": covers,
        "steward": steward,
        "bill_label": "TAX INVOICE",
        "alcohol_note": "Liquor prices are inclusive of VAT" if drinks else None,
        "footer": footer,
    }
    style = {"width_mm": 80, "font": font, "date_style": "d_mon_y", "twelve_hour": twelve_hour}
    return Draft(receipt=receipt, category=category, extras=extras, style=style)


def cab(
    slug: str,
    brand: str,
    city: City,
    *,
    day: date,
    time: str,
    ride_type: str,
    km: float,
    minutes: int,
    route: tuple[str, str],
    driver: str,
    vehicle: str,
    invoice: str,
    office: list[str],
    accent: str,
) -> Draft:
    """A ride-hailing e-receipt priced like the generator's: base + distance + time, 5% GST."""
    base, per_km = CAB_TARIFFS[ride_type]
    items = [
        line("Base Fare", base),
        line(f"Distance Fare ({km} km)", km * per_km),
        line(f"Ride Time Fare ({minutes} min)", minutes * 1.5),
    ]
    subtotal = money(sum(item.amount for item in items))
    taxes = tax_breakup(subtotal, CAB_RATE, inter_state=False)
    receipt = ExtractedReceipt(
        doc_type=DocType.cab_receipt,
        merchant_name=brand,
        merchant_gstin=gstin(brand, city.state_code),
        merchant_city=city.name,
        invoice_number=invoice,
        date=day.isoformat(),
        time=time,
        line_items=items,
        subtotal=subtotal,
        taxes=taxes,
        total=money(subtotal + total_tax(taxes)),
        payment_method=PaymentMethod.upi,
    )
    extras = {
        "ride_type": ride_type,
        "pickup": f"{route[0]}, {city.name}",
        "drop": f"{route[1]}, {city.name}",
        "driver": driver,
        "vehicle": vehicle,
        "distance": f"{km} km",
        "duration": f"{minutes} min",
        "rider": DEMO_EMPLOYEE["name"].split()[0],
        "promo": None,
        "office": office,
    }
    style = {"date_style": "d_month_y", "accent": accent, "twelve_hour": True}
    return Draft(receipt, ExpenseCategory.local_conveyance, extras, style)


def train(
    slug: str,
    *,
    origin: City,
    destination: City,
    day: date,
    time: str,
    arrival: str,
    pnr: str,
    train_name: str,
    fare: float,
    berth: str,
    transaction_id: str,
) -> Draft:
    """AC chair-car e-ticket: 5% GST on the fare only, charged as IGST (the portal is in Delhi)."""
    items = [line("Ticket Fare", fare), line("Convenience Fee", 17.70)]
    taxes = tax_breakup(fare, TRAIN_AC_RATE, inter_state=True)
    receipt = ExtractedReceipt(
        doc_type=DocType.train_ticket,
        merchant_name=PORTAL,
        merchant_gstin=gstin(PORTAL, NEW_DELHI.state_code),
        merchant_city=NEW_DELHI.name,
        invoice_number=pnr,
        date=day.isoformat(),
        time=time,
        line_items=items,
        taxes=taxes,
        total=money(sum(item.amount for item in items) + total_tax(taxes)),
        payment_method=PaymentMethod.upi,
        travel_from=origin.name,
        travel_to=destination.name,
    )
    extras = {
        "train": train_name,
        "travel_class": "CC",
        "from_station": origin.station,
        "to_station": destination.station,
        "arrival_time": arrival,
        "passenger": DEMO_EMPLOYEE["name"].upper(),
        "age": 34,
        "berth": berth,
        "transaction_id": transaction_id,
        "booked_on": date(2026, 10, 2).isoformat(),
        "quota": "GENERAL (GN)",
        "distance_km": 192,
        "office": ["Unit 7, Rail Bhawan Annexe, Connaught Place", "New Delhi - 110001"],
    }
    style = {"date_style": "d_mon_y"}
    return Draft(receipt, ExpenseCategory.travel_domestic, extras, style)
