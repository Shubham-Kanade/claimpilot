"""Shared domain contract (source of truth for API schemas, LLM outputs and synthetic data)."""

from claimpilot.domain.claims import (
    Claim,
    ClaimMode,
    ClaimStatus,
    Decisions,
    Employee,
    OpenQuestion,
    ProcessedDocument,
    QuestionKind,
)
from claimpilot.domain.findings import Finding, FindingSource, Severity
from claimpilot.domain.gstin import generate_gstin, is_valid_gstin, state_of, validate_gstin
from claimpilot.domain.receipt import (
    CRITICAL_FIELDS,
    DocType,
    ExpenseCategory,
    ExtractedReceipt,
    LineItem,
    PaymentMethod,
    ReceiptTruth,
    TaxBreakup,
)

__all__ = [
    "CRITICAL_FIELDS",
    "Claim",
    "ClaimMode",
    "ClaimStatus",
    "Decisions",
    "DocType",
    "Employee",
    "ExpenseCategory",
    "ExtractedReceipt",
    "Finding",
    "FindingSource",
    "LineItem",
    "OpenQuestion",
    "PaymentMethod",
    "ProcessedDocument",
    "QuestionKind",
    "ReceiptTruth",
    "Severity",
    "TaxBreakup",
    "generate_gstin",
    "is_valid_gstin",
    "state_of",
    "validate_gstin",
]
