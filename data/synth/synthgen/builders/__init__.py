"""Per-document-type builders. Each returns a :class:`~synthgen.spec.Draft` whose receipt is
exactly what the matching template prints."""

from __future__ import annotations

from collections.abc import Callable

from claimpilot.domain import DocType

from synthgen.builders.bills import build_fuel_slip, build_gst_invoice, build_mobile_bill
from synthgen.builders.common import BuildContext
from synthgen.builders.food import build_restaurant_bill
from synthgen.builders.informal import build_handwritten_bill, build_upi_payment
from synthgen.builders.travel import (
    build_cab_receipt,
    build_flight_ticket,
    build_hotel_folio,
    build_train_ticket,
)
from synthgen.geo import CITIES, City
from synthgen.spec import Draft

__all__ = [
    "SUPPORTED_DOC_TYPES",
    "BuildContext",
    "build_cab_receipt",
    "build_default",
    "build_flight_ticket",
    "build_fuel_slip",
    "build_gst_invoice",
    "build_handwritten_bill",
    "build_hotel_folio",
    "build_mobile_bill",
    "build_restaurant_bill",
    "build_train_ticket",
    "build_upi_payment",
]


def _route(ctx: BuildContext) -> dict[str, City]:
    """Origin/destination for a stand-alone ticket: from the base city to ``ctx.city``."""
    origin = ctx.persona.base_city
    destination = ctx.city
    if destination == origin:
        destination = ctx.rng.choice([city for city in CITIES if city != origin])
    return {"origin": origin, "destination": destination}


def _flight(ctx: BuildContext) -> Draft:
    return build_flight_ticket(ctx, **_route(ctx))


def _train(ctx: BuildContext) -> Draft:
    return build_train_ticket(ctx, **_route(ctx))


def _gst_invoice(ctx: BuildContext) -> Draft:
    return build_gst_invoice(ctx, purpose=ctx.rng.choice(("course", "wfh")))


def _hotel(ctx: BuildContext) -> Draft:
    return build_hotel_folio(ctx, nights=ctx.rng.randint(1, 3))


def _upi(ctx: BuildContext) -> Draft:
    return build_upi_payment(ctx, auto_fare=ctx.rng.random() < 0.4)


_DEFAULT_BUILDERS: dict[DocType, Callable[[BuildContext], Draft]] = {
    DocType.restaurant_bill: build_restaurant_bill,
    DocType.gst_invoice: _gst_invoice,
    DocType.hotel_folio: _hotel,
    DocType.cab_receipt: build_cab_receipt,
    DocType.flight_ticket: _flight,
    DocType.train_ticket: _train,
    DocType.fuel_slip: build_fuel_slip,
    DocType.mobile_bill: build_mobile_bill,
    DocType.upi_payment: _upi,
    DocType.handwritten_bill: build_handwritten_bill,
}

SUPPORTED_DOC_TYPES: tuple[DocType, ...] = tuple(_DEFAULT_BUILDERS)


def build_default(doc_type: DocType, ctx: BuildContext) -> Draft:
    """Build a stand-alone document of ``doc_type`` with typical parameters."""
    return _DEFAULT_BUILDERS[doc_type](ctx)
