from __future__ import annotations

from collections import Counter

import pytest
from claimpilot.domain import DocType

from synthgen.adversarial import KINDS, generate_adversarial_specs
from synthgen.catalog import ALCOHOL
from synthgen.gst import reconciles
from synthgen.spec import DocSpec
from tests.conftest import ADVERSARIAL_COUNT, BASE_COUNT, SEED


def _with(specs: list[DocSpec], tag: str) -> list[DocSpec]:
    return [spec for spec in specs if tag in spec.truth.tags]


def test_every_kind_is_present_in_equal_share(adversarial_specs: list[DocSpec]) -> None:
    counts = Counter(tag for spec in adversarial_specs for tag in spec.truth.tags if tag in KINDS)
    assert counts == dict.fromkeys(KINDS, ADVERSARIAL_COUNT // len(KINDS))


def test_adversarial_is_deterministic(
    base_specs: list[DocSpec], adversarial_specs: list[DocSpec]
) -> None:
    again = generate_adversarial_specs(
        base_specs, seed=SEED, count=ADVERSARIAL_COUNT, first_index=BASE_COUNT + 1
    )
    assert [s.model_dump_json() for s in again] == [s.model_dump_json() for s in adversarial_specs]


def test_ids_continue_after_base(adversarial_specs: list[DocSpec]) -> None:
    assert adversarial_specs[0].id == f"s{SEED}-{BASE_COUNT + 1:04d}"


def test_tampered_truths_do_not_reconcile(adversarial_specs: list[DocSpec]) -> None:
    tampered = _with(adversarial_specs, "tampered")
    assert tampered
    for spec in tampered:
        assert spec.truth.receipt.line_items
        assert not reconciles(spec.truth.receipt), spec.id


def test_non_tampered_adversarial_truths_reconcile(adversarial_specs: list[DocSpec]) -> None:
    for spec in adversarial_specs:
        if "tampered" not in spec.truth.tags and spec.truth.receipt.line_items:
            assert reconciles(spec.truth.receipt), spec.id


def test_injection_docs_are_flagged_and_print_the_instruction(
    all_specs: list[DocSpec],
) -> None:
    for spec in all_specs:
        injected = "injection" in spec.truth.tags
        assert spec.truth.receipt.contains_instructions is injected
        assert bool(spec.extras["injection_text"]) is injected


def test_over_policy_has_alcohol_or_expensive_hotel(adversarial_specs: list[DocSpec]) -> None:
    alcohol = {drink.en for drink in ALCOHOL}
    for spec in _with(adversarial_specs, "over_policy"):
        receipt = spec.truth.receipt
        if spec.doc_type == DocType.hotel_folio:
            assert spec.extras["tariff"] > 10_000
            assert receipt.taxes.gst_rate_percent == 18
        else:
            assert alcohol & {item.description for item in receipt.line_items}


def test_missing_date_truths_have_no_date(adversarial_specs: list[DocSpec]) -> None:
    for spec in _with(adversarial_specs, "missing_date"):
        assert spec.truth.receipt.date is None
        assert spec.truth.receipt.time is None


def test_duplicates_point_to_an_identical_original(all_specs: list[DocSpec]) -> None:
    by_id = {spec.id: spec for spec in all_specs}
    duplicates = _with(all_specs, "duplicate")
    assert duplicates
    for spec in duplicates:
        original = by_id[spec.truth.duplicate_of]
        assert spec.truth.receipt == original.truth.receipt
        assert spec.extras == original.extras
        assert spec.degrade != "clean"
        assert spec.render_seed != original.render_seed


def test_no_compatible_kind_is_an_error() -> None:
    with pytest.raises(ValueError, match="no adversarial kind"):
        generate_adversarial_specs([], seed=SEED, count=1, first_index=1, only={DocType.other})
