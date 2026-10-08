"""Ground-truth properties of the scenario documents (no browser needed)."""

from __future__ import annotations

import json
from collections import Counter

from claimpilot.domain import DocType, ReceiptTruth, is_valid_gstin

from synthgen.assemble import DEVANAGARI
from synthgen.builders import SUPPORTED_DOC_TYPES
from synthgen.geo import CITY_BY_NAME
from synthgen.gst import reconciles
from synthgen.scenarios import generate_base_specs
from synthgen.spec import DocSpec
from tests.conftest import BASE_COUNT, SEED

TRAVEL_TICKETS = {DocType.flight_ticket, DocType.train_ticket}
NO_GST_TYPES = {DocType.fuel_slip, DocType.handwritten_bill, DocType.upi_payment}


def _dump(specs: list[DocSpec]) -> list[str]:
    return [spec.model_dump_json() for spec in specs]


def test_same_seed_gives_identical_specs(base_specs: list[DocSpec]) -> None:
    assert _dump(generate_base_specs(SEED, BASE_COUNT)) == _dump(base_specs)


def test_different_seed_gives_different_truths(base_specs: list[DocSpec]) -> None:
    other = generate_base_specs(SEED + 1, BASE_COUNT)
    assert [s.truth.receipt for s in other] != [s.truth.receipt for s in base_specs]


def test_count_and_unique_ids(base_specs: list[DocSpec]) -> None:
    assert len(base_specs) == BASE_COUNT
    assert len({spec.id for spec in base_specs}) == BASE_COUNT


def test_all_supported_doc_types_are_generated(base_specs: list[DocSpec]) -> None:
    assert {spec.doc_type for spec in base_specs} == set(SUPPORTED_DOC_TYPES)


def test_only_filter_keeps_document_content(base_specs: list[DocSpec]) -> None:
    hotels = [s for s in base_specs if s.doc_type == DocType.hotel_folio]
    only = generate_base_specs(SEED, len(hotels), only={DocType.hotel_folio})
    assert {s.doc_type for s in only} == {DocType.hotel_folio}
    assert [s.truth.receipt for s in only] == [s.truth.receipt for s in hotels]


def test_every_truth_validates_as_receipt_truth(base_specs: list[DocSpec]) -> None:
    for spec in base_specs:
        payload = json.loads(spec.truth.model_dump_json())
        assert ReceiptTruth.model_validate(payload) == spec.truth


def test_arithmetic_reconciles(base_specs: list[DocSpec]) -> None:
    for spec in base_specs:
        receipt = spec.truth.receipt
        if receipt.line_items:
            assert reconciles(receipt), spec.id
        if receipt.subtotal is not None:
            assert receipt.subtotal == round(sum(i.amount for i in receipt.line_items), 2)


def test_priced_lines_multiply_out(base_specs: list[DocSpec]) -> None:
    for spec in base_specs:
        if spec.doc_type == DocType.fuel_slip:
            continue  # preset-amount fills: volume x rate only approximates the amount
        for item in spec.truth.receipt.line_items:
            if item.quantity is not None and item.unit_price is not None:
                assert round(item.quantity * item.unit_price, 2) == item.amount, spec.id


def test_gstins_are_valid_and_match_merchant_state(base_specs: list[DocSpec]) -> None:
    checked = 0
    for spec in base_specs:
        receipt = spec.truth.receipt
        if receipt.merchant_gstin is None:
            continue
        assert is_valid_gstin(receipt.merchant_gstin), spec.id
        assert receipt.merchant_city is not None
        assert receipt.merchant_gstin[:2] == CITY_BY_NAME[receipt.merchant_city].state_code
        checked += 1
    assert checked > BASE_COUNT // 2


def test_gst_rules(base_specs: list[DocSpec]) -> None:
    for spec in base_specs:
        receipt, taxes = spec.truth.receipt, spec.truth.receipt.taxes
        if spec.doc_type in NO_GST_TYPES:
            assert (taxes.cgst, taxes.sgst, taxes.igst) == (None, None, None), spec.id
        assert not (taxes.igst and (taxes.cgst or taxes.sgst)), "IGST excludes CGST/SGST"
        assert taxes.cgst == taxes.sgst, spec.id
        if spec.doc_type == DocType.restaurant_bill:
            assert taxes.igst is None
        if spec.doc_type == DocType.hotel_folio:
            tariff = spec.extras["tariff"]
            rate = 5 if tariff <= 7500 else 18  # GST 2.0
            assert taxes.cgst == round(receipt.subtotal * rate / 200, 2)


def test_categories_and_payment(base_specs: list[DocSpec]) -> None:
    categories = Counter(spec.truth.category.value for spec in base_specs)
    assert {"meals", "client_entertainment", "accommodation", "travel_domestic"} <= set(categories)
    upi = [s.truth.receipt for s in base_specs if s.doc_type == DocType.upi_payment]
    assert all(r.upi_reference and len(r.upi_reference) == 12 for r in upi)


def test_trip_documents_share_a_trip_and_travel_cities(base_specs: list[DocSpec]) -> None:
    trips = {spec.truth.trip_id for spec in base_specs if spec.truth.trip_id}
    assert trips
    for spec in base_specs:
        receipt = spec.truth.receipt
        if spec.doc_type in TRAVEL_TICKETS:
            assert spec.truth.trip_id
            assert receipt.travel_from in CITY_BY_NAME
            assert receipt.travel_to in CITY_BY_NAME
            assert receipt.travel_from != receipt.travel_to


def test_languages_follow_printed_script(base_specs: list[DocSpec]) -> None:
    hindi_docs = 0
    for spec in base_specs:
        printed = spec.truth.receipt.model_dump_json() + json.dumps(spec.extras, ensure_ascii=False)
        has_devanagari = bool(DEVANAGARI.search(printed))
        assert ("hi" in spec.truth.receipt.languages) == has_devanagari, spec.id
        assert ("hindi" in spec.truth.tags) == has_devanagari
        hindi_docs += has_devanagari
    assert hindi_docs >= 3


def test_handwritten_flag_and_tags(base_specs: list[DocSpec]) -> None:
    for spec in base_specs:
        handwritten = spec.doc_type == DocType.handwritten_bill
        assert spec.truth.receipt.handwritten is handwritten
        assert ("handwritten" in spec.truth.tags) is handwritten


def test_roughly_thirty_percent_clean(base_specs: list[DocSpec]) -> None:
    clean = sum("clean" in spec.truth.tags for spec in base_specs) / len(base_specs)
    assert 0.2 <= clean <= 0.45
    for spec in base_specs:
        if spec.is_pdf:
            assert spec.degrade == "clean"
