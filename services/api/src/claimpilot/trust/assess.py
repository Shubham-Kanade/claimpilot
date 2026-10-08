"""One call per uploaded document: "does this look genuine?", as a score, a verdict and findings.

``assess_document`` runs every trust check, merges their findings and judges the result. Checks
(each returns ``Finding`` objects with ``source=trust``):

====================================  =====================================================
check                                 codes
====================================  =====================================================
arithmetic, GST, GSTIN (``gst.py``)   ``items_subtotal_mismatch``, ``total_mismatch``,
                                      ``gst_*``, ``gstin_*``
duplicates (``duplicates.py``)        ``duplicate_exact``, ``duplicate_image``,
                                      ``duplicate_fields``
metadata forensics (``forensics.py``) ``edited_software``, ``pdf_editor_producer``,
                                      ``ai_generated_metadata``, ``exif_date_mismatch``,
                                      ``file_type_mismatch``
C2PA credentials (``c2pa.py``)        ``ai_generated_c2pa``, ``c2pa_invalid``, ``c2pa_present``
prompt injection (``injection.py``)   ``prompt_injection``
a check that crashed                  ``trust_check_failed`` (warn; the other checks still run)
====================================  =====================================================

Score and verdict (kept simple so an approver can read why)
-----------------------------------------------------------
* The score starts at 100 and loses **40** per ``high`` finding, **15** per ``warn`` and **3** per
  ``info``, never below 0. ``c2pa_present`` (the file carries content credentials) is not
  suspicion, so it costs nothing.
* The verdict is ``block`` when any finding is in ``BLOCKING_CODES`` or the score is below 30;
  otherwise ``review`` when the score is below 80 or any finding is ``high``; otherwise ``clean``.
  One warning alone (85) stays ``clean``; two warnings (70) need a look; one ``high`` always does.
* ``BLOCKING_CODES`` are the findings that are conclusive on their own: text that talks to the AI
  reviewer, a file that declares itself AI-generated (C2PA or metadata), and a bill whose own
  arithmetic contradicts itself (printed total, or line items against the subtotal). Everything
  else, including duplicates and GSTIN problems, needs a human to weigh it (``review``).
* If a C2PA manifest already declares AI generation, the weaker ``ai_generated_metadata`` finding
  (which usually repeats it) is dropped so the same fact is not counted twice.

C2PA is read by the dependency-free minimal reader unless ``C2PA_NATIVE=1`` opts in to the
native library (see ``c2pa.py``): by default credentials are reported as present but
unverified, and only an AI declaration raises a finding.

The checks never call the network or an LLM. Byte-level checks run in a worker thread
(``asyncio.to_thread``) so an upload does not stall the event loop. A check that raises does not
fail the document: it becomes a ``trust_check_failed`` warning and is logged (no file content).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Final, Literal

import structlog
from pydantic import BaseModel, Field

from claimpilot.domain import ExtractedReceipt
from claimpilot.domain.findings import Finding, Severity
from claimpilot.trust.c2pa import c2pa_findings, inspect_c2pa
from claimpilot.trust.duplicates import (
    DuplicateIndex,
    DuplicateMatch,
    ExactLookup,
    InMemoryDuplicateIndex,
    MatchKind,
    SeenDocument,
    classify_match,
    duplicate_findings,
    receipt_fingerprint,
)
from claimpilot.trust.forensics import check_file_type, metadata_findings
from claimpilot.trust.gst import check_gst
from claimpilot.trust.injection import scan_for_instructions
from claimpilot.trust.phash import PHASH_MAX_DISTANCE, page_phash

if TYPE_CHECKING:  # annotations only: importing extraction would load pdfium for nothing
    from claimpilot.extraction.preprocess import PreparedDocument

Verdict = Literal["clean", "review", "block"]

PENALTY: Final = {Severity.high: 40, Severity.warn: 15, Severity.info: 3}
FREE_CODES: Final = frozenset({"c2pa_present"})  # informational and positive: no penalty
BLOCK_BELOW: Final = 30
REVIEW_BELOW: Final = 80
BLOCKING_CODES: Final = frozenset(
    {
        "prompt_injection",
        "ai_generated_c2pa",
        "ai_generated_metadata",
        "total_mismatch",
        "items_subtotal_mismatch",
    }
)
_SEVERITY_ORDER: Final = {Severity.high: 0, Severity.warn: 1, Severity.info: 2}
_KIND_RANK: Final = {"exact": 0, "image": 1, "fields": 2}
_NO_HASH: Final = "0" * 16  # placeholder for find_similar when the page cannot be hashed

__all__ = [
    "BLOCKING_CODES",
    "DuplicateIndex",
    "DuplicateMatch",
    "InMemoryDuplicateIndex",
    "SeenDocument",
    "TrustReport",
    "Verdict",
    "assess_document",
    "score_findings",
    "verdict_for",
]

log = structlog.get_logger(__name__)


class TrustReport(BaseModel):
    """The trust layer's answer for one document."""

    score: int = Field(ge=0, le=100, description="100 = fully trustworthy, 0 = not at all")
    verdict: Verdict = Field(description="clean: proceed; review: a human looks; block: do not use")
    findings: list[Finding] = Field(default_factory=list)
    phash: str | None = Field(default=None, description="64-bit dHash of the first page (hex)")
    fingerprint: str | None = Field(default=None, description="Merchant/date/total key (or None)")


def score_findings(findings: list[Finding]) -> int:
    """100 minus 40 per high, 15 per warn, 3 per info (``c2pa_present`` is free); floor 0."""
    penalty = sum(PENALTY[f.severity] for f in findings if f.code not in FREE_CODES)
    return max(0, 100 - penalty)


def verdict_for(findings: list[Finding], score: int) -> Verdict:
    if score < BLOCK_BELOW or any(f.code in BLOCKING_CODES for f in findings):
        return "block"
    if score < REVIEW_BELOW or any(f.severity is Severity.high for f in findings):
        return "review"
    return "clean"


async def assess_document(
    *,
    document_id: str,
    filename: str,
    raw: bytes,
    prepared: PreparedDocument,
    receipt: ExtractedReceipt,
    employee_id: str | None,
    index: DuplicateIndex,
    register: bool = True,
) -> TrustReport:
    """Run every trust check on one uploaded document.

    ``raw`` are the original upload bytes (metadata and C2PA live there, not in the re-encoded
    page image), ``prepared`` is ``prepare_document(raw)``, ``receipt`` is what extraction read.
    With ``register`` the document is added to ``index`` after it has been searched, so it never
    matches itself but a later upload of the same bill does. Re-assessing a ``document_id`` that
    is already in the index (a retried job) ignores its own earlier entry.
    """
    findings, phash = await asyncio.to_thread(
        _inspect, document_id, filename, raw, prepared, receipt
    )
    fingerprint = receipt_fingerprint(receipt)

    matches = await _find_duplicates(index, document_id, prepared.sha256, phash, fingerprint)
    findings += duplicate_findings(matches, employee_id=employee_id)
    if register:
        await index.add(
            SeenDocument(
                document_id=document_id,
                sha256=prepared.sha256,
                phash=phash,
                fingerprint=fingerprint,
                employee_id=employee_id,
            )
        )

    findings = _tidy(findings)
    score = score_findings(findings)
    return TrustReport(
        score=score,
        verdict=verdict_for(findings, score),
        findings=findings,
        phash=phash,
        fingerprint=fingerprint,
    )


# --- internals ------------------------------------------------------------------------------


def _inspect(
    document_id: str,
    filename: str,
    raw: bytes,
    prepared: PreparedDocument,
    receipt: ExtractedReceipt,
) -> tuple[list[Finding], str | None]:
    """Every check that needs no index: pure functions of the bytes and the receipt."""
    findings: list[Finding] = []
    checks: tuple[tuple[str, Callable[[], list[Finding]]], ...] = (
        ("gst", lambda: check_gst(receipt)),
        ("injection", lambda: scan_for_instructions(receipt)),
        ("metadata", lambda: metadata_findings(raw, receipt)),
        ("c2pa", lambda: c2pa_findings(inspect_c2pa(raw))),
        ("file_type", lambda: check_file_type(filename, prepared.media_type)),
    )
    for name, check in checks:
        try:
            findings += check()
        except Exception as exc:  # one broken check must not sink the document
            log.warning("trust_check_failed", check=name, error=type(exc).__name__, doc=document_id)
            findings.append(_failed(name))
    try:
        phash = page_phash(prepared, raw)
    except Exception as exc:
        log.warning("trust_check_failed", check="phash", error=type(exc).__name__, doc=document_id)
        findings.append(_failed("phash"))
        phash = None
    return findings, phash


def _failed(check: str) -> Finding:
    return Finding(
        code="trust_check_failed",
        severity=Severity.warn,
        message=f"The '{check}' check could not run on this file, so it was not applied.",
        actual=check,
    )


async def _find_duplicates(
    index: DuplicateIndex,
    document_id: str,
    sha256: str,
    phash: str | None,
    fingerprint: str | None,
) -> list[DuplicateMatch]:
    exact = await index.find_exact(sha256=sha256) if isinstance(index, ExactLookup) else []
    similar = await index.find_similar(
        phash=phash or _NO_HASH,
        fingerprint=fingerprint,
        # no page hash: a negative bound leaves only the field-fingerprint match
        max_distance=PHASH_MAX_DISTANCE if phash else -1,
    )
    best: dict[str, DuplicateMatch] = {}
    for match in [*exact, *similar]:
        if match.document_id == document_id:
            continue  # this very document (a retried job), never a duplicate of itself
        checked = _revalidate(match, fingerprint)
        current = best.get(match.document_id)
        if checked is not None and (current is None or _rank(checked) < _rank(current)):
            best[match.document_id] = checked
    return list(best.values())


def _revalidate(match: DuplicateMatch, fingerprint: str | None) -> DuplicateMatch | None:
    """Apply the matching policy to a picture match, whatever index produced it.

    Exact (byte-identical) and fields (equal fingerprints) matches stand as they are. An "image"
    match is re-checked against the stored fingerprint when the index supplied it, and against
    the stricter "unverified" distance when it did not: an index that cannot say what the earlier
    bill contained cannot rule out a look-alike template.
    """
    if match.kind != "image":
        return match
    kind: MatchKind | None = classify_match(
        distance=match.distance, fingerprint=fingerprint, stored_fingerprint=match.fingerprint
    )
    if kind is None:
        return None
    if kind == match.kind:
        return match
    return DuplicateMatch(
        match.document_id, match.employee_id, kind, match.distance, match.fingerprint
    )


def _rank(match: DuplicateMatch) -> tuple[int, int]:
    return (_KIND_RANK[match.kind], match.distance if match.distance is not None else 99)


def _tidy(findings: list[Finding]) -> list[Finding]:
    """Drop a finding that only repeats a stronger one, then order by severity (stable)."""
    if any(f.code == "ai_generated_c2pa" for f in findings):
        findings = [f for f in findings if f.code != "ai_generated_metadata"]
    return sorted(findings, key=lambda f: _SEVERITY_ORDER[f.severity])
