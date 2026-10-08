"""Duplicate detection: the same file, the same picture, or the same bill in a different picture.

Three signals, from strongest to weakest:

``duplicate_exact``
    The upload's sha256 equals an earlier one: the very same file.
``duplicate_image``
    The first page's 64-bit dHash is close to an earlier one (re-saved, resized, lightly rotated
    or cropped copy, a screenshot of a screenshot).
``duplicate_fields``
    The earlier bill has the same *fingerprint*: normalised merchant, date, total and (when
    printed) invoice or UPI reference. This catches a re-issued bill or a fresh photo of the
    same paper, which no picture hash can match across desks and lighting.

All three are ``high`` severity. When the earlier upload belongs to a different employee the
message adds "(possible split bill or shared receipt)": still blocking auto-approval, but a
reviewer will read it differently from a double claim.

How the image hash and the fingerprint are combined (``classify_match``)
------------------------------------------------------------------------
A 64-bit layout hash cannot tell apart two different bills that share a template: on the 100
synthetic documents, mobile bills from one operator, UPI screenshots from one app and cab
receipts from one company sit at Hamming distance 0 to 4 although every amount differs. Trusting
the picture alone would flag every recurring monthly bill as a duplicate. So:

* equal fingerprints  -> duplicate, labelled "image" when the pictures are also close
  (``<= PHASH_MAX_DISTANCE``) and "fields" otherwise;
* close pictures but fingerprints that disagree on date, total or reference -> different bills
  that look alike; never a duplicate (merchant spelling is *not* compared: the extractor may
  spell a name differently on a second read);
* close pictures and a receipt with no fingerprint (merchant, date or total unreadable) ->
  only when the hashes are identical (``<= PHASH_UNVERIFIED_MAX_DISTANCE``, which is 0): there
  is nothing to cross-check, so the bar is higher. At distance 4 the dataset sweep already
  paired two documents with a missing date to different bills of the same template.

Trade-off, accepted on purpose: a re-uploaded picture whose extracted date or total changed
between two reads is not flagged by image. A false accusation against an employee costs more
than a missed re-encode, and the exact-bytes and fingerprint checks still apply.

Index contract (the pipeline backs ``DuplicateIndex`` with Postgres; ``InMemoryDuplicateIndex``
is the reference implementation and the test double)
-----------------------------------------------------------------------------------------
``find_similar`` returns candidates, one per earlier document: every row whose stored hash is
within ``max_distance`` of ``phash`` or whose stored fingerprint equals ``fingerprint``, each with
the stored fingerprint, the Hamming distance (None when either hash is missing) and the owner.
``assess_document`` re-applies ``classify_match`` to what comes back, so an adapter that returns
raw candidates is safe; one that already filters is just faster. An index may also implement
``find_exact(sha256=...)`` (see ``ExactLookup``); without it, a byte-identical re-upload is
still caught as ``duplicate_image`` at distance 0 when the fields agree.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, Protocol, runtime_checkable

from claimpilot.domain import ExtractedReceipt
from claimpilot.domain.findings import Finding, Severity
from claimpilot.trust.gst import TOTAL_TOLERANCE
from claimpilot.trust.phash import (
    PHASH_MAX_DISTANCE,
    PHASH_UNVERIFIED_MAX_DISTANCE,
    hamming,
    is_phash,
)

MatchKind = Literal["exact", "image", "fields"]

FINGERPRINT_VERSION: Final = "v1"
FINGERPRINT_MAX_CHARS: Final = 64  # width of the documents.fingerprint column (String(64))
DIGEST_CHARS: Final = 10  # hex digits kept of the merchant and reference digests
MAX_TOTAL: Final = 1e12  # larger "totals" are not plausible receipts (and would not fit the key)
_LEGAL_SUFFIXES: Final = frozenset(
    {"pvt", "private", "ltd", "limited", "llp", "inc", "co", "corp", "company"}
)
SPLIT_BILL_NOTE: Final = "(possible split bill or shared receipt)"


@dataclass(frozen=True, slots=True)
class SeenDocument:
    """What the index remembers about an uploaded document."""

    document_id: str
    sha256: str
    phash: str | None
    fingerprint: str | None
    employee_id: str | None


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    """An earlier document that ``find_similar`` / ``find_exact`` considers a duplicate."""

    document_id: str
    employee_id: str | None
    kind: MatchKind
    distance: int | None  # Hamming distance of the page hashes; None when a hash is missing
    fingerprint: str | None = None  # the earlier document's stored fingerprint, when known


class DuplicateIndex(Protocol):
    """Everything a duplicate check needs from storage (Postgres in the pipeline)."""

    async def find_similar(
        self, *, phash: str, fingerprint: str | None, max_distance: int
    ) -> list[DuplicateMatch]: ...

    async def add(self, record: SeenDocument) -> None: ...


@runtime_checkable
class ExactLookup(Protocol):
    """Optional index capability: documents with the same sha256 (a unique-index lookup)."""

    async def find_exact(self, *, sha256: str) -> list[DuplicateMatch]: ...


# --- fingerprint ----------------------------------------------------------------------------


def _alnum(text: str | None) -> str:
    """Case-folded letters and digits only (any script), so 'Chai-Point' == 'chai point'."""
    if not text:
        return ""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).casefold() if ch.isalnum())


def _merchant_key(name: str | None) -> str:
    if not name:
        return ""
    words = [
        "".join(ch for ch in word if ch.isalnum())
        for word in unicodedata.normalize("NFKC", name).casefold().split()
    ]
    words = [word for word in words if word]
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return "".join(words)


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def _digest(text: str) -> str:
    """A short stable digest (10 hex digits) of normalised text; empty text stays empty."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:DIGEST_CHARS] if text else ""


def receipt_fingerprint(receipt: ExtractedReceipt) -> str | None:
    """A canonical key for "this bill": ``v1:<merchant>|<date>|<total>|<reference>``.

    The merchant and the reference (invoice number, or the UPI reference when there is none) are
    stored as 10-digit digests, the date as ISO ``YYYY-MM-DD`` and the total with two decimals, so
    the key is at most 51 characters and always fits ``String(64)``. Two otherwise identical
    small bills (two ₹20 teas on one day) stay distinct because their references differ.

    None when the merchant, a valid ISO date or a total in ``(0, MAX_TOTAL)`` is missing: such a
    receipt cannot be matched by its fields.
    """
    merchant = _merchant_key(receipt.merchant_name)
    day = _iso_date(receipt.date)
    total = receipt.total
    if not merchant or day is None or total is None or not 0 < total < MAX_TOTAL:
        return None
    reference = _alnum(receipt.invoice_number) or _alnum(receipt.upi_reference)
    return f"{FINGERPRINT_VERSION}:{_digest(merchant)}|{day}|{total:.2f}|{_digest(reference)}"


def _fingerprint_parts(fingerprint: str) -> tuple[str, float, str] | None:
    """(date, total, reference) of a ``v1`` fingerprint, else None for an unknown format."""
    if not fingerprint.startswith(f"{FINGERPRINT_VERSION}:"):
        return None
    parts = fingerprint.split("|")
    if len(parts) != 4:
        return None
    try:
        return parts[1], float(parts[2]), parts[3]
    except ValueError:
        return None


def fingerprints_conflict(a: str, b: str) -> bool:
    """True when two fingerprints describe different bills: date, total or reference disagree.

    The merchant is deliberately not compared (an extractor may spell it differently on a
    second read), and a missing reference on one side does not conflict with a printed one.
    """
    parts_a, parts_b = _fingerprint_parts(a), _fingerprint_parts(b)
    if parts_a is None or parts_b is None:
        return a != b  # unknown format: only equality is meaningful
    (date_a, total_a, ref_a), (date_b, total_b, ref_b) = parts_a, parts_b
    if date_a != date_b or abs(total_a - total_b) > TOTAL_TOLERANCE:
        return True
    return bool(ref_a and ref_b and ref_a != ref_b)


# --- matching policy ------------------------------------------------------------------------


def classify_match(
    *,
    distance: int | None,
    fingerprint: str | None,
    stored_fingerprint: str | None,
    max_distance: int = PHASH_MAX_DISTANCE,
) -> Literal["image", "fields"] | None:
    """Decide whether an earlier document is a duplicate, and how it was recognised.

    See the module docstring for the reasoning. ``distance`` is the Hamming distance between
    the page hashes (None when either is missing).
    """
    near = distance is not None and distance <= max_distance
    if fingerprint is not None and fingerprint == stored_fingerprint:
        return "image" if near else "fields"
    if distance is None or not near:
        return None
    if fingerprint is not None and stored_fingerprint is not None:
        return None if fingerprints_conflict(fingerprint, stored_fingerprint) else "image"
    return "image" if distance <= min(max_distance, PHASH_UNVERIFIED_MAX_DISTANCE) else None


# --- in-memory index ------------------------------------------------------------------------


class InMemoryDuplicateIndex:
    """Reference ``DuplicateIndex`` (and ``ExactLookup``) for tests, evals and local runs."""

    def __init__(self) -> None:
        self._records: dict[str, SeenDocument] = {}

    def __len__(self) -> int:
        return len(self._records)

    async def add(self, record: SeenDocument) -> None:
        self._records[record.document_id] = record  # re-adding a document replaces it

    async def find_similar(
        self, *, phash: str, fingerprint: str | None, max_distance: int
    ) -> list[DuplicateMatch]:
        matches: list[DuplicateMatch] = []
        for record in self._records.values():
            distance = _distance(phash, record.phash)
            kind = classify_match(
                distance=distance,
                fingerprint=fingerprint,
                stored_fingerprint=record.fingerprint,
                max_distance=max_distance,
            )
            if kind is not None:
                matches.append(
                    DuplicateMatch(
                        document_id=record.document_id,
                        employee_id=record.employee_id,
                        kind=kind,
                        distance=distance,
                        fingerprint=record.fingerprint,
                    )
                )
        return sorted(matches, key=_match_order)

    async def find_exact(self, *, sha256: str) -> list[DuplicateMatch]:
        return [
            DuplicateMatch(r.document_id, r.employee_id, "exact", 0, r.fingerprint)
            for r in self._records.values()
            if r.sha256 == sha256
        ]


def _distance(a: str | None, b: str | None) -> int | None:
    return (
        hamming(a, b) if a is not None and b is not None and is_phash(a) and is_phash(b) else None
    )


_KIND_RANK: Final = {"exact": 0, "image": 1, "fields": 2}


def _match_order(match: DuplicateMatch) -> tuple[int, int, str]:
    return (
        _KIND_RANK[match.kind],
        match.distance if match.distance is not None else 99,
        match.document_id,
    )


# --- findings -------------------------------------------------------------------------------


def duplicate_findings(matches: list[DuplicateMatch], *, employee_id: str | None) -> list[Finding]:
    """One ``high`` finding per kind of match found (exact, image, fields), strongest first.

    ``matches`` must already be de-duplicated per earlier document (one entry each, strongest
    kind). The finding names the best match in ``actual`` and counts the others in the message.
    """
    findings: list[Finding] = []
    for kind in ("exact", "image", "fields"):
        group = sorted((m for m in matches if m.kind == kind), key=_match_order)
        if not group:
            continue
        best = group[0]
        suffix = f" {SPLIT_BILL_NOTE}" if _other_employee(group, employee_id) else ""
        more = f" and {len(group) - 1} more" if len(group) > 1 else ""
        parts = _fingerprint_parts(best.fingerprint) if best.fingerprint else None
        uses_reference = bool(parts and parts[2])
        if kind == "exact":
            message = f"This exact file was already uploaded as document {best.document_id}{more}."
            fields: tuple[str, ...] = ()
        elif kind == "image":
            message = (
                f"This picture is almost identical to document {best.document_id}{more}, "
                "uploaded earlier (a re-saved, resized or re-photographed copy)."
            )
            fields = ()
        else:
            what = (
                "merchant, date, total and reference"
                if uses_reference
                else "merchant, date and total"
            )
            message = (
                f"This bill has the same {what} as document {best.document_id}{more}, "
                "but it is a different picture (a re-issued or re-photographed copy)."
            )
            fields = ("merchant_name", "date", "total") + (
                ("invoice_number",) if uses_reference else ()
            )
        findings.append(
            Finding(
                code=f"duplicate_{kind}",
                severity=Severity.high,
                message=message + suffix,
                fields=fields,
                actual=best.document_id,
            )
        )
    return findings


def _other_employee(group: list[DuplicateMatch], employee_id: str | None) -> bool:
    """True when any earlier upload in the group provably belongs to someone else."""
    return employee_id is not None and any(
        m.employee_id is not None and m.employee_id != employee_id for m in group
    )
