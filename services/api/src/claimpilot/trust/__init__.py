"""Trust checks: does this document look genuine and internally consistent?

The pipeline calls :func:`assess_document` once per uploaded document. The individual checks are
exported too, for tests, evals and one-off use.
"""

from claimpilot.trust.assess import (
    BLOCKING_CODES,
    DuplicateIndex,
    DuplicateMatch,
    InMemoryDuplicateIndex,
    SeenDocument,
    TrustReport,
    assess_document,
    score_findings,
    verdict_for,
)
from claimpilot.trust.c2pa import C2paResult, inspect_c2pa, native_backend_active, native_enabled
from claimpilot.trust.duplicates import receipt_fingerprint
from claimpilot.trust.forensics import metadata_findings
from claimpilot.trust.gst import check_gst
from claimpilot.trust.injection import scan_for_instructions
from claimpilot.trust.phash import PHASH_MAX_DISTANCE, dhash, hamming, page_phash

__all__ = [
    "BLOCKING_CODES",
    "PHASH_MAX_DISTANCE",
    "C2paResult",
    "DuplicateIndex",
    "DuplicateMatch",
    "InMemoryDuplicateIndex",
    "SeenDocument",
    "TrustReport",
    "assess_document",
    "check_gst",
    "dhash",
    "hamming",
    "inspect_c2pa",
    "metadata_findings",
    "native_backend_active",
    "native_enabled",
    "page_phash",
    "receipt_fingerprint",
    "scan_for_instructions",
    "score_findings",
    "verdict_for",
]
