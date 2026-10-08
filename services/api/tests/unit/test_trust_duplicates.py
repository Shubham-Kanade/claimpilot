"""Duplicate detection: fingerprint, matching policy, the in-memory index, findings."""

from __future__ import annotations

import pytest
from test_trust_support import bill

from claimpilot.domain import ExtractedReceipt
from claimpilot.domain.findings import Severity
from claimpilot.trust.duplicates import (
    FINGERPRINT_MAX_CHARS,
    SPLIT_BILL_NOTE,
    DuplicateIndex,
    DuplicateMatch,
    ExactLookup,
    InMemoryDuplicateIndex,
    MatchKind,
    SeenDocument,
    classify_match,
    duplicate_findings,
    fingerprints_conflict,
    receipt_fingerprint,
)
from claimpilot.trust.phash import PHASH_MAX_DISTANCE, PHASH_UNVERIFIED_MAX_DISTANCE

HASH_A = "0000000000000000"
HASH_NEAR = "0000000000000003"  # 2 bits from HASH_A
HASH_FAR = "ffffffffffffffff"


def fp(**overrides) -> str:
    value = receipt_fingerprint(bill(**overrides))
    assert value is not None
    return value


# --- fingerprint -----------------------------------------------------------------------------


def test_fingerprint_is_stable_across_spelling_noise():
    base = fp()
    assert fp(merchant_name="RASOI GHAR EATERY") == base
    assert fp(merchant_name="Rasoi-Ghar  Eatery Pvt. Ltd.") == base
    assert fp(invoice_number="r-01061") == base
    assert fp(merchant_name="Rasoi Ghar Eatery", total=420.0004) == base


def test_fingerprint_changes_with_each_identifying_field():
    base = fp()
    assert fp(merchant_name="Other Eatery") != base
    assert fp(date="2026-08-08") != base
    assert fp(total=421.0) != base
    assert fp(invoice_number="R01062") != base


def test_fingerprint_uses_the_upi_reference_when_there_is_no_invoice_number():
    with_utr = fp(invoice_number=None, upi_reference="587470820293")
    assert with_utr != fp(invoice_number=None)
    assert with_utr != fp(invoice_number=None, upi_reference="111111111111")
    assert fp(invoice_number="R1", upi_reference="587470820293") == fp(invoice_number="R1")


def test_fingerprint_format_and_width():
    value = fp()
    assert value.startswith("v1:") and value.count("|") == 3
    assert len(value) <= FINGERPRINT_MAX_CHARS == 64


def test_fingerprint_always_fits_the_column():
    nasty = bill(
        merchant_name="Ünïcode Ṃerchant " * 40,
        invoice_number="INV/" + "9" * 300,
        total=999_999_999_999.99,
    )
    value = receipt_fingerprint(nasty)
    assert value is not None and len(value) <= 64


@pytest.mark.parametrize(
    "overrides",
    [
        {"merchant_name": None},
        {"merchant_name": "   "},
        {"date": None},
        {"date": "07/08/2026"},  # not ISO: no usable date
        {"total": None},
        {"total": 0.0},
        {"total": -5.0},
        {"total": 1e12},
        {"total": float("nan")},
    ],
)
def test_fingerprint_is_none_when_merchant_date_or_total_is_unusable(overrides):
    assert receipt_fingerprint(bill(**overrides)) is None


def test_fingerprint_of_a_bill_without_reference_has_an_empty_last_part():
    assert fp(invoice_number=None, upi_reference=None).endswith("|")


# --- conflicts and the matching policy ---------------------------------------------------------


def test_conflict_looks_at_date_total_and_reference_but_not_the_merchant():
    base = fp()
    assert not fingerprints_conflict(base, fp(merchant_name="Totally Different Name"))
    assert fingerprints_conflict(base, fp(date="2026-08-09"))
    assert fingerprints_conflict(base, fp(total=500.0))
    assert fingerprints_conflict(base, fp(invoice_number="R99999"))
    assert not fingerprints_conflict(base, fp(total=420.9))  # within the round-off tolerance
    assert not fingerprints_conflict(base, fp(invoice_number=None))  # one side has no reference


def test_conflict_with_an_unknown_format_means_only_equality_counts():
    assert not fingerprints_conflict("legacy-key", "legacy-key")
    assert fingerprints_conflict("legacy-key", fp())
    assert fingerprints_conflict("v1:broken", "v1:broken|x")
    assert fingerprints_conflict("v1:a|2026-01-01|notanumber|", fp())


@pytest.mark.parametrize(
    ("distance", "mine", "stored", "expected"),
    [
        # equal fingerprints: always a duplicate; "image" when the pictures are close too
        (0, "k", "k", "image"),
        (PHASH_MAX_DISTANCE, "k", "k", "image"),
        (PHASH_MAX_DISTANCE + 1, "k", "k", "fields"),
        (None, "k", "k", "fields"),
        # close pictures with fingerprints that agree on the bill's details
        (3, fp(), fp(merchant_name="Spelled Differently"), "image"),
        # close pictures but a different bill of the same template: not a duplicate
        (0, fp(), fp(total=999.0), None),
        (0, fp(), fp(date="2026-01-01"), None),
        # far pictures and different fingerprints
        (PHASH_MAX_DISTANCE + 1, fp(), fp(total=999.0), None),
        (None, fp(), fp(total=999.0), None),
        # no fingerprint on one side: only an identical hash counts
        (0, None, fp(), "image"),
        (0, fp(), None, "image"),
        (PHASH_UNVERIFIED_MAX_DISTANCE + 1, None, fp(), None),
        (PHASH_UNVERIFIED_MAX_DISTANCE + 1, fp(), None, None),
        (0, None, None, "image"),
        (None, None, None, None),
    ],
)
def test_classify_match_policy(distance, mine, stored, expected):
    kind = classify_match(distance=distance, fingerprint=mine, stored_fingerprint=stored)
    assert kind == expected


def test_a_negative_bound_leaves_only_field_matches():
    assert (
        classify_match(distance=0, fingerprint=None, stored_fingerprint=None, max_distance=-1)
        is None
    )
    assert (
        classify_match(distance=0, fingerprint="k", stored_fingerprint="k", max_distance=-1)
        == "fields"
    )


# --- the in-memory index -----------------------------------------------------------------------


def seen(
    doc_id: str,
    *,
    phash: str | None = HASH_A,
    fingerprint: str | None = None,
    employee: str | None = "E1",
    sha: str = "a" * 64,
) -> SeenDocument:
    return SeenDocument(doc_id, sha, phash, fingerprint, employee)


async def test_index_satisfies_the_protocols():
    index = InMemoryDuplicateIndex()
    assert isinstance(index, ExactLookup)
    index_as_protocol: DuplicateIndex = index  # static check: the shape the pipeline relies on
    assert len(index_as_protocol.__class__.__name__) > 0 and len(index) == 0


async def test_find_similar_by_picture_and_by_fields():
    index = InMemoryDuplicateIndex()
    key = fp()
    await index.add(seen("near", phash=HASH_NEAR, fingerprint=key))
    await index.add(seen("far-same-bill", phash=HASH_FAR, fingerprint=key))
    await index.add(seen("near-other-bill", phash=HASH_NEAR, fingerprint=fp(total=77.0)))
    await index.add(seen("far-other-bill", phash=HASH_FAR, fingerprint=fp(total=78.0)))
    await index.add(seen("near-no-fingerprint", phash=HASH_NEAR, fingerprint=None))

    found = await index.find_similar(phash=HASH_A, fingerprint=key, max_distance=PHASH_MAX_DISTANCE)

    assert [(m.document_id, m.kind) for m in found] == [
        ("near", "image"),
        ("far-same-bill", "fields"),
    ]
    assert found[0].distance == 2 and found[0].fingerprint == key


async def test_find_similar_without_a_fingerprint_needs_an_identical_hash():
    index = InMemoryDuplicateIndex()
    await index.add(seen("same", phash=HASH_A, fingerprint=fp()))
    await index.add(seen("close", phash=HASH_NEAR, fingerprint=fp(total=77.0)))
    found = await index.find_similar(phash=HASH_A, fingerprint=None, max_distance=8)
    assert [m.document_id for m in found] == ["same"]


async def test_find_similar_skips_documents_without_a_usable_hash():
    index = InMemoryDuplicateIndex()
    await index.add(seen("no-hash", phash=None, fingerprint=fp()))
    await index.add(seen("bad-hash", phash="xyz", fingerprint=None))
    found = await index.find_similar(phash=HASH_A, fingerprint=fp(), max_distance=8)
    assert [(m.document_id, m.kind, m.distance) for m in found] == [("no-hash", "fields", None)]


async def test_add_replaces_an_earlier_record_of_the_same_document():
    index = InMemoryDuplicateIndex()
    await index.add(seen("d1", phash=HASH_FAR))
    await index.add(seen("d1", phash=HASH_A))
    assert len(index) == 1
    assert [
        m.document_id
        for m in await index.find_similar(phash=HASH_A, fingerprint=None, max_distance=8)
    ] == ["d1"]


async def test_find_exact_matches_on_sha256_only():
    index = InMemoryDuplicateIndex()
    await index.add(seen("d1", sha="1" * 64, phash=HASH_FAR))
    await index.add(seen("d2", sha="2" * 64))
    found = await index.find_exact(sha256="1" * 64)
    assert [(m.document_id, m.kind, m.distance) for m in found] == [("d1", "exact", 0)]
    assert await index.find_exact(sha256="3" * 64) == []


async def test_matches_are_ordered_strongest_first_then_nearest():
    index = InMemoryDuplicateIndex()
    key = fp()
    await index.add(seen("b-far", phash=HASH_FAR, fingerprint=key))
    await index.add(seen("a-near", phash="0000000000000007", fingerprint=key))
    await index.add(seen("c-nearest", phash=HASH_NEAR, fingerprint=key))
    found = await index.find_similar(phash=HASH_A, fingerprint=key, max_distance=8)
    assert [m.document_id for m in found] == ["c-nearest", "a-near", "b-far"]


# --- findings ----------------------------------------------------------------------------------


def match(
    doc_id: str = "d1",
    kind: MatchKind = "image",
    employee: str | None = "E1",
    distance: int | None = 2,
    fingerprint: str | None = None,
) -> DuplicateMatch:
    return DuplicateMatch(doc_id, employee, kind, distance, fingerprint)


def test_each_kind_gets_its_own_high_finding():
    findings = duplicate_findings(
        [match("e", "exact", distance=0), match("i", "image"), match("f", "fields", distance=20)],
        employee_id="E1",
    )
    assert [f.code for f in findings] == ["duplicate_exact", "duplicate_image", "duplicate_fields"]
    assert {f.severity for f in findings} == {Severity.high}
    assert [f.actual for f in findings] == ["e", "i", "f"]
    assert "document e" in findings[0].message and "document i" in findings[1].message


def test_fields_finding_names_the_fields_and_the_reference_when_one_was_printed():
    with_ref = duplicate_findings([match("f", "fields", fingerprint=fp())], employee_id="E1")[0]
    assert with_ref.fields == ("merchant_name", "date", "total", "invoice_number")
    assert "merchant, date, total and reference" in with_ref.message
    without = duplicate_findings(
        [match("f", "fields", fingerprint=fp(invoice_number=None))], employee_id="E1"
    )[0]
    assert without.fields == ("merchant_name", "date", "total")
    assert "merchant, date and total" in without.message
    unknown = duplicate_findings([match("f", "fields")], employee_id="E1")[0]
    assert "merchant, date and total" in unknown.message


def test_other_employee_adds_the_split_bill_note_and_keeps_severity_high():
    same = duplicate_findings([match(employee="E1")], employee_id="E1")[0]
    other = duplicate_findings([match(employee="E2")], employee_id="E1")[0]
    assert SPLIT_BILL_NOTE not in same.message
    assert other.message.endswith(SPLIT_BILL_NOTE)
    assert other.severity is Severity.high


def test_unknown_employees_never_trigger_the_split_bill_note():
    assert (
        SPLIT_BILL_NOTE
        not in duplicate_findings([match(employee=None)], employee_id="E1")[0].message
    )
    assert (
        SPLIT_BILL_NOTE
        not in duplicate_findings([match(employee="E2")], employee_id=None)[0].message
    )


def test_several_matches_are_counted_in_one_finding():
    findings = duplicate_findings(
        [match("d1", distance=1), match("d2", distance=3, employee="E2"), match("d3", distance=5)],
        employee_id="E1",
    )
    assert len(findings) == 1
    assert findings[0].actual == "d1" and "and 2 more" in findings[0].message
    assert SPLIT_BILL_NOTE in findings[0].message  # one of the group belongs to someone else


def test_no_matches_no_findings():
    assert duplicate_findings([], employee_id="E1") == []


def test_fingerprint_helper_accepts_any_extracted_receipt():
    assert isinstance(bill(), ExtractedReceipt)
