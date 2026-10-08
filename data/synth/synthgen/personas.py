"""Fictional employee personas (Faker ``en_IN``), one per simulated month of expenses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from faker import Faker

from synthgen.geo import CITIES, City
from synthgen.rng import derive_rng, derive_seed

GRADES = ("L1", "L2", "L3", "L4", "L5")
# Typical nightly hotel tariff band by grade (rupees); the policy cap tested is Rs 10,000.
HOTEL_BUDGET: dict[str, tuple[int, int]] = {
    "L1": (2200, 4200),
    "L2": (3000, 5500),
    "L3": (4200, 7500),
    "L4": (5500, 8800),
    "L5": (7000, 9800),
}
ACTIVITY_MONTHS = (date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1))


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    employee_id: str
    grade: str
    base_city: City
    month: date  # first day of the simulated month
    mobile_masked: str
    email: str


def make_faker(seed: int, *scope: object) -> Faker:
    fake = Faker("en_IN")
    fake.seed_instance(derive_seed(seed, "faker", *scope))
    return fake


def make_persona(seed: int, index: int) -> Persona:
    rng = derive_rng(seed, "persona", index)
    fake = make_faker(seed, "persona", index)
    first, last = fake.first_name(), fake.last_name()
    return Persona(
        id=f"P{index + 1:03d}",
        name=f"{first} {last}",
        employee_id=f"EMP{rng.randint(10000, 99999)}",
        grade=rng.choice(GRADES),
        base_city=rng.choice(CITIES),
        month=ACTIVITY_MONTHS[index % len(ACTIVITY_MONTHS)],
        mobile_masked=f"9{rng.randint(0, 9)}XXXXXX{rng.randint(10, 99)}",
        # example.com is reserved for documentation, so these can never reach a real inbox
        email=f"{first.lower()}.{last.lower()}@example.com",
    )
