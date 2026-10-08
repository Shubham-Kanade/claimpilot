"""Field-level scoring of an ``ExtractedReceipt`` prediction against ground truth.

A field counts toward accuracy when either side has a value: correct predictions score, and so
do hallucinated values (truth empty, prediction filled) and misses (truth filled, prediction empty),
which both count as errors. Fields that are empty on both sides are ignored.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from claimpilot.domain import CRITICAL_FIELDS, ExtractedReceipt, Severity
from claimpilot.trust.gst import check_gst

AMOUNT_TOLERANCE = 0.01  # rupees; truth and prediction are both rounded to 2dp
MERCHANT_MIN_SIMILARITY = 0.9


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _alnum(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", value).upper()


def amounts_match(a: float, b: float) -> bool:
    return abs(round(a, 2) - round(b, 2)) <= AMOUNT_TOLERANCE + 1e-9


def text_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm_text(a), _norm_text(b)).ratio()


Comparator = Callable[[object, object], bool]


def _eq_amount(a: object, b: object) -> bool:
    return isinstance(a, int | float) and isinstance(b, int | float) and amounts_match(a, b)


def _eq_alnum(a: object, b: object) -> bool:
    return isinstance(a, str) and isinstance(b, str) and _alnum(a) == _alnum(b)


def _eq_merchant(a: object, b: object) -> bool:
    return (
        isinstance(a, str)
        and isinstance(b, str)
        and text_similarity(a, b) >= MERCHANT_MIN_SIMILARITY
    )


def _eq_text(a: object, b: object) -> bool:
    return isinstance(a, str) and isinstance(b, str) and _norm_text(a) == _norm_text(b)


def _eq(a: object, b: object) -> bool:
    return a == b


# field name -> (getter, comparator)
FIELDS: dict[str, tuple[Callable[[ExtractedReceipt], object], Comparator]] = {
    "doc_type": (lambda r: r.doc_type, _eq),
    "merchant_name": (lambda r: r.merchant_name, _eq_merchant),
    "merchant_gstin": (lambda r: r.merchant_gstin, _eq_alnum),
    "merchant_city": (lambda r: r.merchant_city, _eq_text),
    "invoice_number": (lambda r: r.invoice_number, _eq_alnum),
    "date": (lambda r: r.date, _eq_text),
    "subtotal": (lambda r: r.subtotal, _eq_amount),
    "cgst": (lambda r: r.taxes.cgst, _eq_amount),
    "sgst": (lambda r: r.taxes.sgst, _eq_amount),
    "igst": (lambda r: r.taxes.igst, _eq_amount),
    "service_charge": (lambda r: r.service_charge, _eq_amount),
    "discount": (lambda r: r.discount, _eq_amount),
    "total": (lambda r: r.total, _eq_amount),
    "payment_method": (lambda r: r.payment_method, _eq),
    "upi_reference": (lambda r: r.upi_reference, _eq_alnum),
    "travel_from": (lambda r: r.travel_from, _eq_text),
    "travel_to": (lambda r: r.travel_to, _eq_text),
}


def _is_empty(value: object) -> bool:
    return value is None or value == "" or (hasattr(value, "value") and value == "unknown")


@dataclass(frozen=True)
class FieldResult:
    name: str
    correct: bool
    truth: object
    predicted: object


@dataclass
class ReceiptScore:
    receipt_id: str
    fields: list[FieldResult] = field(default_factory=list)
    line_item_f1: float = 1.0
    injection_expected: bool = False
    injection_flagged: bool = False
    # The read makes the trust checks complain (arithmetic, GST, GSTIN) where the document's own
    # printed figures do not: a misread or swapped figure that would accuse a genuine receipt.
    false_alarm: bool = False

    @property
    def errors(self) -> list[FieldResult]:
        return [f for f in self.fields if not f.correct]

    def accuracy(self, names: Iterable[str] | None = None) -> float | None:
        wanted = set(names) if names is not None else None
        scored = [f for f in self.fields if wanted is None or f.name in wanted]
        if not scored:
            return None
        return sum(f.correct for f in scored) / len(scored)


def line_item_f1(truth: Sequence[float], predicted: Sequence[float]) -> float:
    """F1 of line items matched by amount (each truth amount can be matched once)."""
    if not truth and not predicted:
        return 1.0
    remaining = list(truth)
    matched = 0
    for amount in predicted:
        hit = next((t for t in remaining if amounts_match(t, amount)), None)
        if hit is not None:
            remaining.remove(hit)
            matched += 1
    if matched == 0:
        return 0.0
    precision = matched / len(predicted)
    recall = matched / len(truth)
    return 2 * precision * recall / (precision + recall)


def _complaints(receipt: ExtractedReceipt) -> set[str]:
    """Codes of the reading-dependent trust findings (the ones a second opinion can remove)."""
    return {
        f.code
        for f in check_gst(receipt)
        if f.severity is not Severity.info and f.code != "gstin_missing"
    }


def score_receipt(
    receipt_id: str, truth: ExtractedReceipt, predicted: ExtractedReceipt
) -> ReceiptScore:
    score = ReceiptScore(
        receipt_id=receipt_id,
        line_item_f1=line_item_f1(
            [i.amount for i in truth.line_items], [i.amount for i in predicted.line_items]
        ),
        injection_expected=truth.contains_instructions,
        injection_flagged=predicted.contains_instructions,
        false_alarm=bool(_complaints(predicted) - _complaints(truth)),
    )
    for name, (get, same) in FIELDS.items():
        t, p = get(truth), get(predicted)
        if _is_empty(t) and _is_empty(p):
            continue
        correct = not _is_empty(t) and not _is_empty(p) and same(t, p)
        score.fields.append(FieldResult(name=name, correct=correct, truth=t, predicted=p))
    return score


@dataclass(frozen=True)
class AggregateScore:
    receipts: int
    parse_failures: int
    field_accuracy: float
    critical_field_accuracy: float
    line_item_f1: float
    injection_recall: float | None
    injection_false_positive_rate: float | None
    errors_by_field: dict[str, int]
    false_alarms: int = 0

    @property
    def json_validity(self) -> float:
        total = self.receipts + self.parse_failures
        return self.receipts / total if total else 0.0


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def aggregate(scores: Sequence[ReceiptScore], parse_failures: int = 0) -> AggregateScore:
    """Micro-averaged accuracy over all scored fields.

    A parse failure counts as wrong on the 4 critical fields (for both accuracies) and as
    line-item F1 = 0. It is not charged for every optional field, since we cannot know which
    of them the document actually had.
    """
    all_fields = [f for s in scores for f in s.fields]
    critical = [f for f in all_fields if f.name in CRITICAL_FIELDS]
    # Each parse failure loses every critical field.
    critical_total = len(critical) + parse_failures * len(CRITICAL_FIELDS)
    field_total = len(all_fields) + parse_failures * len(CRITICAL_FIELDS)

    expected = [s for s in scores if s.injection_expected]
    clean = [s for s in scores if not s.injection_expected]
    errors_by_field: dict[str, int] = {}
    for f in all_fields:
        if not f.correct:
            errors_by_field[f.name] = errors_by_field.get(f.name, 0) + 1

    return AggregateScore(
        receipts=len(scores),
        parse_failures=parse_failures,
        field_accuracy=sum(f.correct for f in all_fields) / field_total if field_total else 0.0,
        critical_field_accuracy=(
            sum(f.correct for f in critical) / critical_total if critical_total else 0.0
        ),
        # A parse failure contributes line-item F1 = 0.
        line_item_f1=(
            sum(s.line_item_f1 for s in scores) / (len(scores) + parse_failures)
            if scores or parse_failures
            else 0.0
        ),
        injection_recall=_rate(sum(s.injection_flagged for s in expected), len(expected)),
        injection_false_positive_rate=_rate(sum(s.injection_flagged for s in clean), len(clean)),
        errors_by_field=dict(sorted(errors_by_field.items(), key=lambda kv: -kv[1])),
        false_alarms=sum(s.false_alarm for s in scores),
    )
