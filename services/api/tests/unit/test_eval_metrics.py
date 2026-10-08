from __future__ import annotations

import pytest

from claimpilot.domain import DocType, ExtractedReceipt, LineItem, PaymentMethod, TaxBreakup
from claimpilot.evals.metrics import (
    aggregate,
    amounts_match,
    line_item_f1,
    score_receipt,
    text_similarity,
)


def _truth(**overrides) -> ExtractedReceipt:
    base = {
        "doc_type": DocType.restaurant_bill,
        "merchant_name": "Chai Point Express",
        "merchant_gstin": "27AAPFU0939F1ZV",
        "date": "2026-10-03",
        "line_items": [
            LineItem(description="Masala Dosa", amount=180.0),
            LineItem(description="Filter Coffee", amount=60.0),
        ],
        "subtotal": 240.0,
        "taxes": TaxBreakup(cgst=6.0, sgst=6.0),
        "total": 252.0,
        "payment_method": PaymentMethod.upi,
    }
    base.update(overrides)
    return ExtractedReceipt(**base)


def test_perfect_prediction_scores_one():
    t = _truth()
    s = score_receipt("r1", t, t.model_copy())
    assert s.accuracy() == 1.0
    assert s.line_item_f1 == 1.0
    assert not s.errors


def test_tolerant_normalisation():
    p = _truth(
        merchant_name="CHAI POINT  EXPRESS.",
        merchant_gstin=" 27aapfu0939f1zv ",
        total=252.004,
    )
    s = score_receipt("r1", _truth(), p)
    assert s.accuracy() == 1.0


def test_wrong_total_is_a_critical_error():
    s = score_receipt("r1", _truth(), _truth(total=262.0))
    assert [e.name for e in s.errors] == ["total"]
    assert s.accuracy(["total", "date"]) == 0.5


def test_hallucinated_and_missed_fields_are_errors():
    s = score_receipt("r1", _truth(), _truth(invoice_number="INV-9", date=None))
    errors = {e.name for e in s.errors}
    assert errors == {"invoice_number", "date"}


def test_unknown_payment_method_treated_as_empty():
    t = _truth(payment_method=PaymentMethod.unknown)
    s = score_receipt("r1", t, t.model_copy())
    assert "payment_method" not in {f.name for f in s.fields}


@pytest.mark.parametrize(
    ("truth", "pred", "expected"),
    [
        ([], [], 1.0),
        ([10.0], [], 0.0),
        ([10.0, 20.0], [10.0, 20.0], 1.0),
        ([10.0, 20.0], [10.0], pytest.approx(2 / 3)),
        ([10.0, 10.0], [10.0, 10.0, 10.0], pytest.approx(0.8)),
    ],
)
def test_line_item_f1(truth, pred, expected):
    assert line_item_f1(truth, pred) == expected


def test_helpers():
    assert amounts_match(10.0, 10.005)
    assert not amounts_match(10.0, 10.02)
    assert text_similarity("Hotel Sea View", "hotel  sea view") == 1.0


def test_aggregate_with_parse_failures_and_injection():
    clean = score_receipt("r1", _truth(), _truth())
    inj_truth = _truth(contains_instructions=True)
    caught = score_receipt("r2", inj_truth, _truth(contains_instructions=True))
    missed = score_receipt("r3", inj_truth, _truth(total=1.0))
    agg = aggregate([clean, caught, missed], parse_failures=1)

    assert agg.receipts == 3
    assert agg.json_validity == pytest.approx(0.75)
    assert agg.injection_recall == pytest.approx(0.5)
    assert agg.injection_false_positive_rate == 0.0
    assert agg.errors_by_field == {"total": 1}
    # 12 critical fields scored, 1 wrong, plus 4 lost to the parse failure -> 11/16
    assert agg.critical_field_accuracy == pytest.approx(11 / 16)


def test_aggregate_empty():
    agg = aggregate([])
    assert agg.field_accuracy == 0.0
    assert agg.injection_recall is None
    assert agg.json_validity == 0.0


# --- false alarms: a misread figure that would make the trust checks accuse a genuine receipt ----


def test_a_misread_total_is_a_false_alarm_when_the_document_itself_adds_up():
    genuine = _truth()  # 240 + 12 = 252
    s = score_receipt("r1", genuine, _truth(total=2520.0))
    assert s.false_alarm


def test_a_genuine_mismatch_the_reader_reproduced_is_not_a_false_alarm():
    edited = _truth(total=752.0)  # printed that way: the document's own figures contradict
    assert not score_receipt("r1", edited, edited.model_copy()).false_alarm


def test_an_exact_read_is_not_a_false_alarm():
    assert not score_receipt("r1", _truth(), _truth()).false_alarm


def test_aggregate_counts_false_alarms():
    scores = [
        score_receipt("r1", _truth(), _truth()),
        score_receipt("r2", _truth(), _truth(total=2520.0)),
        score_receipt("r3", _truth(), _truth(subtotal=2400.0)),
    ]
    assert aggregate(scores).false_alarms == 2
