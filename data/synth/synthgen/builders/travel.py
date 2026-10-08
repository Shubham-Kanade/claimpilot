"""Travel documents: flight e-tickets, train e-tickets, hotel folios and cab e-receipts.

Labelling conventions for tickets: ``date``/``time`` are the journey's departure, the PNR is the
``invoice_number`` and ``travel_from``/``travel_to`` are city names. Local rides (cabs) leave
``travel_from``/``travel_to`` empty because only street localities are printed.
"""

from __future__ import annotations

from datetime import timedelta

from claimpilot.domain import (
    DocType,
    ExpenseCategory,
    ExtractedReceipt,
    LineItem,
    PaymentMethod,
    TaxBreakup,
    generate_gstin,
)

from synthgen.builders.common import (
    ACCENTS,
    BuildContext,
    add_minutes,
    clock,
    date_style,
    digits,
    letters,
    line,
    phone,
    pick_payment,
    rupees,
    street_address,
    vehicle_number,
)
from synthgen.catalog import (
    AIRLINE_CODES,
    AIRLINES,
    CAB_BRANDS,
    CAB_TYPES,
    HOTEL_PREFIXES,
    HOTEL_SUFFIXES,
    RAIL_PORTAL,
    ROOM_TYPES,
    TRAIN_CLASSES,
    TRAIN_NAMES,
)
from synthgen.geo import CITY_BY_NAME, City
from synthgen.gst import (
    CAB_RATE,
    FLIGHT_ECONOMY_RATE,
    TRAIN_AC_RATE,
    hotel_rate,
    is_inter_state,
    money,
    tax_breakup,
    total_tax,
)
from synthgen.personas import HOTEL_BUDGET
from synthgen.spec import Draft

ONLINE_PAYMENTS = {PaymentMethod.card: 5, PaymentMethod.upi: 3, PaymentMethod.netbanking: 2}
TRAIN_FARES = {"SL": (320, 720), "CC": (650, 1450), "3A": (950, 1950), "2A": (1400, 2750)}
CAB_TARIFFS = {  # ride type -> (base fare, per km)
    "Auto": (30.0, 11.0),
    "Mini": (50.0, 14.0),
    "Sedan": (65.0, 17.0),
    "Prime SUV": (90.0, 22.0),
}


def _sum(items: list[LineItem]) -> float:
    return money(sum(item.amount for item in items))


def build_flight_ticket(ctx: BuildContext, *, origin: City, destination: City) -> Draft:
    """Economy e-ticket. 5% GST on the base fare; IGST when the airline is registered in a
    state other than the departure state (the place of supply)."""
    rng = ctx.rng
    airline = rng.choice(AIRLINES)
    hq = CITY_BY_NAME[airline.hq_city]
    departure = clock(rng, 6, 22)
    duration = rng.randint(70, 170)
    base_fare = rupees(rng, 2800, 9500, step=1)
    items = [
        line("Base Fare", base_fare),
        line("User Development Fee", rupees(rng, 150, 650, step=1)),
        line("Passenger Service Fee", rupees(rng, 90, 240, step=1)),
        line("Aviation Security Fee", 236.0),
    ]
    if rng.random() < 0.6:
        items.append(line("Convenience Fee", rupees(rng, 199, 399, step=1)))
    taxes = tax_breakup(
        base_fare,
        FLIGHT_ECONOMY_RATE,
        inter_state=is_inter_state(hq.state_code, origin.state_code),
    )
    receipt = ExtractedReceipt(
        doc_type=DocType.flight_ticket,
        merchant_name=airline.name,
        merchant_gstin=generate_gstin(hq.state_code, rng),
        merchant_city=hq.name,
        invoice_number=letters(rng, 2) + digits(rng, 1) + letters(rng, 3),
        date=ctx.day.isoformat(),
        time=departure,
        line_items=items,
        subtotal=_sum(items),
        taxes=taxes,
        total=money(_sum(items) + total_tax(taxes)),
        payment_method=pick_payment(rng, ONLINE_PAYMENTS),
        travel_from=origin.name,
        travel_to=destination.name,
    )
    extras = {
        "flight_no": f"{AIRLINE_CODES[airline.name]} {rng.randint(101, 989)}",
        "origin_code": origin.airport,
        "destination_code": destination.airport,
        "arrival_time": add_minutes(departure, duration),
        "duration": f"{duration // 60}h {duration % 60:02d}m",
        "passenger": ctx.persona.name.upper(),
        "seat": f"{rng.randint(3, 32)}{rng.choice('ABCDEF')}",
        "booked_on": (ctx.day - timedelta(days=rng.randint(2, 20))).isoformat(),
        "fare_type": rng.choice(("Saver", "Regular", "Flexi")),
        "office": street_address(rng, ctx.fake, hq),
    }
    style = {"date_style": date_style(rng, wordy_share=0.8), "accent": rng.choice(ACCENTS)}
    return Draft(receipt, ExpenseCategory.travel_domestic, extras, style)


def build_train_ticket(ctx: BuildContext, *, origin: City, destination: City) -> Draft:
    """Rail e-ticket from a fictional booking portal; 5% GST on AC-class fares only."""
    rng = ctx.rng
    travel_class = rng.choice(list(TRAIN_CLASSES))
    fare = rupees(rng, *TRAIN_FARES[travel_class], step=1)
    items = [line("Ticket Fare", fare), line("Convenience Fee", rng.choice((11.8, 17.7)))]
    if rng.random() < 0.5:
        items.append(line("Travel Insurance Premium", 0.45))
    hq = CITY_BY_NAME[RAIL_PORTAL.hq_city]
    is_ac = TRAIN_CLASSES[travel_class]
    taxes = (
        tax_breakup(
            fare, TRAIN_AC_RATE, inter_state=is_inter_state(hq.state_code, origin.state_code)
        )
        if is_ac
        else TaxBreakup()
    )
    departure = clock(rng, 5, 23)
    receipt = ExtractedReceipt(
        doc_type=DocType.train_ticket,
        merchant_name=RAIL_PORTAL.name,
        merchant_gstin=generate_gstin(hq.state_code, rng),
        merchant_city=hq.name,
        invoice_number=digits(rng, 10),
        date=ctx.day.isoformat(),
        time=departure,
        line_items=items,
        taxes=taxes,
        total=money(_sum(items) + total_tax(taxes)),
        payment_method=pick_payment(rng, ONLINE_PAYMENTS),
        travel_from=origin.name,
        travel_to=destination.name,
    )
    extras = {
        "train": f"{rng.randint(11001, 22999)} / {rng.choice(TRAIN_NAMES).upper()}",
        "travel_class": travel_class,
        "from_station": origin.station,
        "to_station": destination.station,
        "arrival_time": add_minutes(departure, rng.randint(180, 900)),
        "passenger": ctx.persona.name.upper(),
        "age": rng.randint(24, 58),
        "berth": f"{rng.choice('ABSC')}{rng.randint(1, 9)}/{rng.randint(1, 72)}",
        "transaction_id": digits(rng, 11),
        "booked_on": (ctx.day - timedelta(days=rng.randint(3, 40))).isoformat(),
        "quota": rng.choice(("GENERAL (GN)", "TATKAL (TQ)")),
        "distance_km": rng.randint(150, 1200),
        "office": street_address(rng, ctx.fake, hq),
    }
    style = {"date_style": rng.choice(("dmy_dash", "d_mon_y"))}
    return Draft(receipt, ExpenseCategory.travel_domestic, extras, style)


def build_hotel_folio(ctx: BuildContext, *, nights: int = 2, tariff: float | None = None) -> Draft:
    """Guest folio dated on checkout (``ctx.day``). One room line per night; 5% GST up to
    Rs 7,500 per night, 18% above; always intra-state (place of supply = the property)."""
    rng, city = ctx.rng, ctx.city
    if tariff is None:
        tariff = rupees(rng, *HOTEL_BUDGET[ctx.persona.grade], step=50)
    arrival = ctx.day - timedelta(days=nights)
    items = [
        line(f"Room Charges {(arrival + timedelta(days=n)).strftime('%d-%b')}", tariff)
        for n in range(nights)
    ]
    taxes = tax_breakup(_sum(items), hotel_rate(tariff), inter_state=False)
    receipt = ExtractedReceipt(
        doc_type=DocType.hotel_folio,
        merchant_name=f"{rng.choice(HOTEL_PREFIXES)} {rng.choice(HOTEL_SUFFIXES)}",
        merchant_gstin=generate_gstin(city.state_code, rng),
        merchant_city=city.name,
        invoice_number=f"F{ctx.day:%y}{digits(rng, 5)}",
        date=ctx.day.isoformat(),
        line_items=items,
        subtotal=_sum(items),
        taxes=taxes,
        total=money(_sum(items) + total_tax(taxes)),
        payment_method=pick_payment(rng, {PaymentMethod.card: 7, PaymentMethod.upi: 2}),
    )
    extras = {
        "address": street_address(rng, ctx.fake, city),
        "phone": phone(rng, city),
        "guest": ctx.persona.name,
        "company": "ClaimPilot Demo Corp (Synthetic)",
        "room_no": str(rng.randint(101, 1520)),
        "room_type": rng.choice(ROOM_TYPES),
        "arrival": arrival.isoformat(),
        "departure": ctx.day.isoformat(),
        "nights": nights,
        "tariff": tariff,
        "plan": rng.choice(("EP (Room only)", "CP (with breakfast)")),
        "cashier": ctx.fake.first_name(),
    }
    style = {"date_style": date_style(rng, wordy_share=0.5), "accent": rng.choice(ACCENTS)}
    return Draft(receipt, ExpenseCategory.accommodation, extras, style)


def build_cab_receipt(ctx: BuildContext, *, to_airport: bool = False) -> Draft:
    """Ride-hailing e-receipt: base + distance + time fare, optional promo, 5% GST."""
    rng, city = ctx.rng, ctx.city
    ride_type = rng.choice(CAB_TYPES)
    base, per_km = CAB_TARIFFS[ride_type]
    km = round(rng.uniform(3, 30), 1)
    minutes = int(km * rng.uniform(2.2, 4.0))
    items = [
        line("Base Fare", base),
        line(f"Distance Fare ({km} km)", km * per_km),
        line(f"Ride Time Fare ({minutes} min)", minutes * 1.5),
    ]
    if to_airport:
        items.append(line("Airport Entry Charge", 150.0))
    discount = rupees(rng, 25, 75) if rng.random() < 0.25 else None
    taxable = money(_sum(items) - (discount or 0))
    taxes = tax_breakup(taxable, CAB_RATE, inter_state=False, print_rate=rng.random() < 0.7)
    pickup, drop = rng.sample(city.localities, 2)
    receipt = ExtractedReceipt(
        doc_type=DocType.cab_receipt,
        merchant_name=rng.choice(CAB_BRANDS),
        merchant_gstin=generate_gstin(city.state_code, rng),
        merchant_city=city.name,
        invoice_number=f"CRN{digits(rng, 10)}",
        date=ctx.day.isoformat(),
        time=clock(rng, 6, 23),
        line_items=items,
        subtotal=_sum(items),
        taxes=taxes,
        discount=discount,
        total=money(taxable + total_tax(taxes)),
        payment_method=pick_payment(
            rng,
            {
                PaymentMethod.upi: 4,
                PaymentMethod.cash: 3,
                PaymentMethod.card: 2,
                PaymentMethod.wallet: 2,
            },
        ),
    )
    extras = {
        "ride_type": ride_type,
        "pickup": f"{pickup}, {city.name}",
        "drop": f"{city.airport} Airport, {city.name}" if to_airport else f"{drop}, {city.name}",
        "driver": ctx.fake.first_name(),
        "vehicle": vehicle_number(rng, city),
        "distance": f"{km} km",
        "duration": f"{minutes} min",
        "rider": ctx.persona.name.split()[0],
        "promo": f"SAVE{int(discount)}" if discount else None,
        "office": street_address(rng, ctx.fake, city),
    }
    style = {
        "date_style": date_style(rng, wordy_share=0.7),
        "accent": rng.choice(ACCENTS),
        "twelve_hour": rng.random() < 0.7,
    }
    return Draft(receipt, ExpenseCategory.local_conveyance, extras, style)
