"""Shared domain contract (source of truth for API schemas, LLM outputs and synthetic data)."""

from claimpilot.domain.gstin import generate_gstin, is_valid_gstin, state_of, validate_gstin
from claimpilot.domain.receipt import (
    DocType,
    ExpenseCategory,
    ExtractedReceipt,
    LineItem,
    PaymentMethod,
    ReceiptTruth,
    TaxBreakup,
)

__all__ = [
    "DocType",
    "ExpenseCategory",
    "ExtractedReceipt",
    "LineItem",
    "PaymentMethod",
    "ReceiptTruth",
    "TaxBreakup",
    "generate_gstin",
    "is_valid_gstin",
    "state_of",
    "validate_gstin",
]
