from __future__ import annotations

import pytest
from anthropic import transform_schema

from claimpilot.domain import DocType, ExtractedReceipt, LineItem, PaymentMethod, TaxBreakup
from claimpilot.extraction.schema import WireReceipt, from_domain, to_domain

# Documented Claude structured-output limits (per request, across all strict schemas).
MAX_OPTIONAL = 24
MAX_UNION = 16


def _objects(schema: dict):
    defs = schema.get("$defs", {})
    yield schema
    yield from defs.values()


def test_wire_schema_has_no_optional_and_no_union_parameters():
    schema = transform_schema(WireReceipt.model_json_schema())
    optional = unions = 0
    for obj in _objects(schema):
        props = obj.get("properties", {})
        optional += len(set(props) - set(obj.get("required", [])))
        unions += sum("anyOf" in p or isinstance(p.get("type"), list) for p in props.values())
    assert optional == 0 <= MAX_OPTIONAL
    assert unions == 0 <= MAX_UNION


def test_domain_schema_would_exceed_limits():
    """Documents why the wire schema exists: the domain model is over the union limit."""
    schema = ExtractedReceipt.model_json_schema()
    unions = sum(
        "anyOf" in p for obj in _objects(schema) for p in obj.get("properties", {}).values()
    )
    assert unions > MAX_UNION


FULL = ExtractedReceipt(
    doc_type=DocType.restaurant_bill,
    merchant_name="Chai Point Express",
    merchant_gstin="27AAPFU0939F1ZV",
    date="2026-10-03",
    line_items=[LineItem(description="मसाला डोसा", quantity=2, unit_price=90.0, amount=180.0)],
    subtotal=180.0,
    taxes=TaxBreakup(cgst=4.5, sgst=4.5, gst_rate_percent=5),
    total=189.0,
    payment_method=PaymentMethod.upi,
    upi_reference="UTR123",
    languages=["en", "hi"],
    contains_instructions=True,
)


def test_round_trip_preserves_receipt():
    assert to_domain(from_domain(FULL)) == FULL


def test_empty_strings_become_none():
    r = to_domain(from_domain(ExtractedReceipt(doc_type=DocType.other)))
    assert r.merchant_name is None and r.total is None and r.taxes.cgst is None
    assert r.currency == "INR"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1,23,456.50", 123456.5), ("Rs. 120", 120.0), ("₹ 99/-", 99.0), (" 42 ", 42.0)],
)
def test_amount_formats_are_normalised(raw, expected):
    wire = from_domain(FULL).model_copy(update={"total": raw})
    assert to_domain(wire).total == expected


def test_unparseable_amount_is_flagged_low_confidence():
    wire = from_domain(FULL).model_copy(update={"total": "one eighty nine"})
    r = to_domain(wire)
    assert r.total is None
    assert "total" in r.low_confidence_fields


def test_line_without_amount_is_dropped():
    wire = from_domain(FULL)
    bad = wire.line_items[0].model_copy(update={"amount": ""})
    r = to_domain(wire.model_copy(update={"line_items": [bad]}))
    assert r.line_items == []
