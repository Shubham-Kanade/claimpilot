from __future__ import annotations

import pytest
from pydantic import ValidationError

from claimpilot.domain import DocType, ExpenseCategory, ExtractedReceipt, ReceiptTruth


def _walk(schema: dict):
    yield schema
    for value in schema.values():
        if isinstance(value, dict):
            yield from _walk(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield from _walk(item)


def test_minimal_receipt_defaults():
    r = ExtractedReceipt(doc_type=DocType.cab_receipt)
    assert r.currency == "INR"
    assert r.line_items == []
    assert r.taxes.cgst is None
    assert not r.contains_instructions


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        ExtractedReceipt.model_validate({"doc_type": "other", "unexpected": 1})


def test_schema_is_structured_output_friendly():
    """Claude structured outputs need closed objects and no free-form maps."""
    schema = ExtractedReceipt.model_json_schema()
    for node in _walk(schema):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, node.get("title")
        assert not isinstance(node.get("additionalProperties"), dict), "no dict-valued fields"


def test_truth_round_trip():
    truth = ReceiptTruth(
        id="r-001",
        receipt=ExtractedReceipt(doc_type=DocType.restaurant_bill, total=472.5),
        category=ExpenseCategory.meals,
        tags=["clean"],
    )
    assert ReceiptTruth.model_validate_json(truth.model_dump_json()) == truth
