"""'A month of life' for each persona, turned into ground-truth document specs.

Each persona gets one out-of-town trip (sometimes two): travel out and back, a hotel, cabs and
meals sharing a ``trip_id``. Around it: client dinners, local cabs and autos, UPI payments, a
mobile bill, a course or work-from-home purchase, and fuel.

Every planned document is built from its own random stream (persona id + position in the plan),
so ``--only`` filtering never changes the content of the documents that remain.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import partial
from itertools import count as counter

from claimpilot.domain import DocType, ExpenseCategory

from synthgen.assemble import assemble, doc_id
from synthgen.builders import (
    BuildContext,
    build_cab_receipt,
    build_flight_ticket,
    build_fuel_slip,
    build_gst_invoice,
    build_handwritten_bill,
    build_hotel_folio,
    build_mobile_bill,
    build_restaurant_bill,
    build_train_ticket,
    build_upi_payment,
)
from synthgen.geo import CITIES, CITY_BY_NAME, RAIL_NEIGHBOURS, City
from synthgen.personas import Persona, make_faker, make_persona
from synthgen.rng import derive_rng, derive_seed
from synthgen.spec import DocSpec, Draft

MAX_PERSONAS = 5000  # guard against an --only filter that can never reach --count


@dataclass(frozen=True)
class PlannedDoc:
    doc_type: DocType
    day: date
    city: City
    build: Callable[[BuildContext], Draft]
    trip_id: str | None = field(default=None)


def _pick_destination(base: City, rng: random.Random) -> City:
    neighbours = RAIL_NEIGHBOURS.get(base.name, ())
    if neighbours and rng.random() < 0.65:
        return CITY_BY_NAME[rng.choice(neighbours)]
    return rng.choice([city for city in CITIES if city != base])


def _travel_leg(
    origin: City, destination: City, day: date, rng: random.Random, trip_id: str
) -> PlannedDoc:
    is_neighbour = destination.name in RAIL_NEIGHBOURS.get(origin.name, ())
    if rng.random() < (0.8 if is_neighbour else 0.2):  # long-distance trains are rarer
        build = partial(build_train_ticket, origin=origin, destination=destination)
        return PlannedDoc(DocType.train_ticket, day, origin, build, trip_id)
    build = partial(build_flight_ticket, origin=origin, destination=destination)
    return PlannedDoc(DocType.flight_ticket, day, origin, build, trip_id)


def _plan_trip(
    persona: Persona, rng: random.Random, number: int, start_day: int
) -> list[PlannedDoc]:
    trip_id = f"{persona.id}-T{number}"
    base = persona.base_city
    destination = _pick_destination(base, rng)
    nights = rng.randint(1, 3)
    start = persona.month.replace(day=start_day)
    checkout = start + timedelta(days=nights)

    outbound = _travel_leg(base, destination, start, rng, trip_id)
    inbound = _travel_leg(destination, base, checkout, rng, trip_id)
    plan = [
        outbound,
        PlannedDoc(DocType.cab_receipt, start, destination, build_cab_receipt, trip_id),
        PlannedDoc(
            DocType.hotel_folio,
            checkout,
            destination,
            partial(build_hotel_folio, nights=nights),
            trip_id,
        ),
        inbound,
    ]
    for night in range(min(nights, 2)):
        plan.append(
            PlannedDoc(
                DocType.restaurant_bill,
                start + timedelta(days=night),
                destination,
                build_restaurant_bill,
                trip_id,
            )
        )
    if inbound.doc_type == DocType.flight_ticket and rng.random() < 0.6:
        to_airport = partial(build_cab_receipt, to_airport=True)
        plan.append(PlannedDoc(DocType.cab_receipt, checkout, destination, to_airport, trip_id))
    return plan


def plan_month(persona: Persona, seed: int) -> list[PlannedDoc]:
    """All expense documents of one persona's month, in date order."""
    rng = derive_rng(seed, "plan", persona.id)
    base = persona.base_city

    def on(day: int) -> date:
        return persona.month.replace(day=day)

    def anyday() -> date:
        return on(rng.randint(1, 28))

    plan = _plan_trip(persona, rng, 1, start_day=rng.randint(3, 12))
    if rng.random() < 0.4:
        plan += _plan_trip(persona, rng, 2, start_day=rng.randint(16, 24))

    client_dinner = partial(
        build_restaurant_bill,
        upscale=True,
        covers=rng.randint(3, 6),
        category=ExpenseCategory.client_entertainment,
    )
    plan += [
        PlannedDoc(DocType.restaurant_bill, anyday(), base, client_dinner)
        for _ in range(rng.randint(1, 2))
    ]
    plan += [
        PlannedDoc(DocType.cab_receipt, anyday(), base, build_cab_receipt)
        for _ in range(rng.randint(1, 2))
    ]
    plan += [
        PlannedDoc(DocType.handwritten_bill, anyday(), base, build_handwritten_bill)
        for _ in range(rng.choice((1, 1, 2)))
    ]
    plan += [
        PlannedDoc(
            DocType.upi_payment,
            anyday(),
            base,
            partial(build_upi_payment, auto_fare=rng.random() < 0.5),
        )
        for _ in range(rng.randint(1, 2))
    ]
    plan.append(PlannedDoc(DocType.mobile_bill, on(rng.randint(3, 6)), base, build_mobile_bill))
    purpose = "course" if rng.random() < 0.6 else "wfh"
    plan.append(
        PlannedDoc(DocType.gst_invoice, anyday(), base, partial(build_gst_invoice, purpose=purpose))
    )
    plan += [
        PlannedDoc(DocType.fuel_slip, anyday(), base, build_fuel_slip)
        for _ in range(rng.randint(1, 2))
    ]
    return sorted(plan, key=lambda doc: doc.day)


def generate_base_specs(
    seed: int,
    count: int,
    only: Collection[DocType] | None = None,
    first_index: int = 1,
) -> list[DocSpec]:
    """``count`` scenario documents, walking personas in order until enough are produced."""
    specs: list[DocSpec] = []
    for persona_index in counter():
        if len(specs) >= count:
            break
        if persona_index >= MAX_PERSONAS:
            raise RuntimeError(f"could not produce {count} documents for doc types {only}")
        persona = make_persona(seed, persona_index)
        fake = make_faker(seed, "docs", persona.id)
        for position, planned in enumerate(plan_month(persona, seed)):
            if only and planned.doc_type not in only:
                continue
            fake.seed_instance(derive_seed(seed, "doc-faker", persona.id, position))
            ctx = BuildContext(
                rng=derive_rng(seed, "doc", persona.id, position),
                fake=fake,
                persona=persona,
                city=planned.city,
                day=planned.day,
            )
            specs.append(
                assemble(
                    planned.build(ctx),
                    doc_id=doc_id(seed, first_index + len(specs)),
                    seed=seed,
                    persona_id=persona.id,
                    trip_id=planned.trip_id,
                )
            )
            if len(specs) >= count:
                break
    return specs
