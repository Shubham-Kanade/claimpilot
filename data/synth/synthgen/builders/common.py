"""Shared helpers for document builders."""

from __future__ import annotations

import random
import string
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from claimpilot.domain import LineItem, PaymentMethod
from faker import Faker

from synthgen.geo import City
from synthgen.gst import money
from synthgen.personas import Persona

THERMAL_FONTS = ("Noto Sans Mono", "Courier Prime")
ACCENTS = ("#1f4e8c", "#0f766e", "#7c2d12", "#5b21b6", "#9d174d", "#1d4ed8")
NUMERIC_DATE_STYLES = ("dmy_slash", "dmy_dash", "dmy_short")
WORDY_DATE_STYLES = ("d_mon_y", "d_month_y")


@dataclass
class BuildContext:
    """Everything a builder needs: its own random stream, a Faker, who, where and when."""

    rng: random.Random
    fake: Faker
    persona: Persona
    city: City
    day: date


def rupees(rng: random.Random, low: float, high: float, step: int = 5) -> float:
    """A price in [low, high] rounded to a multiple of ``step`` rupees."""
    return float(step * round(rng.uniform(low, high) / step))


def line(
    description: str,
    amount: float,
    *,
    quantity: float | None = None,
    unit_price: float | None = None,
) -> LineItem:
    return LineItem(
        description=description, quantity=quantity, unit_price=unit_price, amount=money(amount)
    )


def priced_line(description: str, quantity: int, unit_price: float) -> LineItem:
    return line(description, quantity * unit_price, quantity=quantity, unit_price=unit_price)


def clock(rng: random.Random, start_hour: int, end_hour: int) -> str:
    """A random 24h 'HH:MM' between the two hours."""
    minutes = rng.randint(start_hour * 60, end_hour * 60 - 1)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def add_minutes(hhmm: str, minutes: int) -> str:
    start = datetime.combine(date(2000, 1, 1), time.fromisoformat(hhmm))
    return (start + timedelta(minutes=minutes)).strftime("%H:%M")


def pick_payment(rng: random.Random, weights: dict[PaymentMethod, float]) -> PaymentMethod:
    methods = list(weights)
    return rng.choices(methods, weights=[weights[m] for m in methods])[0]


def digits(rng: random.Random, count: int) -> str:
    return "".join(rng.choices(string.digits, k=count))


def letters(rng: random.Random, count: int) -> str:
    return "".join(rng.choices(string.ascii_uppercase, k=count))


def street_address(rng: random.Random, fake: Faker, city: City) -> list[str]:
    """Two printed address lines; the second always names the city (``merchant_city``)."""
    locality = rng.choice(city.localities)
    building = rng.choice(("Shop No. ", "Unit ", "Plot ", "G-")) + str(rng.randint(1, 240))
    street = fake.street_name()
    pin = f"{city.pin_prefix}{rng.randint(1, 99):03d}"
    return [f"{building}, {street}, {locality}", f"{city.name} - {pin}"]


def phone(rng: random.Random, city: City) -> str:
    return f"{city.std_code}-{rng.randint(2, 6)}{digits(rng, 3)} {digits(rng, 4)}"


def vehicle_number(rng: random.Random, city: City) -> str:
    """Registration plate in the city's state series, e.g. 'MH 12 AB 1234'."""
    return f"{city.rto} {rng.randint(1, 50):02d} {letters(rng, 2)} {digits(rng, 4)}"


def date_style(rng: random.Random, wordy_share: float = 0.4) -> str:
    pool = WORDY_DATE_STYLES if rng.random() < wordy_share else NUMERIC_DATE_STYLES
    return rng.choice(pool)


def financial_year(day: date) -> str:
    """Indian financial year (April-March) as printed on invoices, e.g. '26-27'."""
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"
