from __future__ import annotations

from collections import Counter

import pytest

from claimpilot.domain import Claim, ClaimMode, ClaimStatus, Finding, FindingSource, Severity
from claimpilot.domain.claims import OpenQuestion, QuestionKind
from claimpilot.evals.golden import GOLDEN_DIR, has_alcohol, load_golden, to_processed

pytestmark = pytest.mark.skipif(not GOLDEN_DIR.exists(), reason="golden dataset not generated")


def test_golden_set_loads_with_employees():
    cases = load_golden()
    assert len(cases) == 100
    assert {c.split for c in cases} == {"dev", "test"}
    assert all(c.employee.grade in {"L1", "L2", "L3", "L4", "L5"} for c in cases)


def test_trip_ids_belong_to_one_persona_each():
    cases = load_golden()
    trips = {}
    for c in cases:
        if c.truth.trip_id:
            trips.setdefault(c.truth.trip_id, set()).add(c.truth.persona_id)
    assert trips and all(len(p) == 1 for p in trips.values())


def test_to_processed_marks_alcohol_from_line_items():
    flagged = [c for c in load_golden() if has_alcohol(c.truth)]
    assert flagged, "the dataset has over_policy dinners with alcohol"
    assert Counter(to_processed(c.truth).decisions.alcohol_present for c in flagged) == {
        1.0: len(flagged)
    }


def test_to_processed_carries_truth_category_and_date():
    case = load_golden()[0]
    doc = to_processed(case.truth)
    assert doc.category == case.truth.category
    assert doc.expense_date is None or doc.expense_date.isoformat() == case.truth.receipt.date


def test_claim_helpers():
    q_open = OpenQuestion(id="q1", kind=QuestionKind.attendees, text="Who attended?")
    q_done = OpenQuestion(id="q2", kind=QuestionKind.business_purpose, text="Why?", answer="QBR")
    claim = Claim(
        id="c1",
        employee_id="P001",
        title="t",
        mode=ClaimMode.event,
        document_ids=["d1"],
        total=10.0,
        open_questions=[q_open, q_done],
        findings=[Finding(code="x", severity=Severity.high, message="m")],
    )
    assert [q.id for q in claim.unanswered] == ["q1"]
    assert claim.has_high_findings and claim.status is ClaimStatus.draft
    assert q_done.answered and not q_open.answered


def test_finding_for_document_and_defaults():
    f = Finding(code="x", severity=Severity.warn, message="m")
    assert f.source is FindingSource.trust and f.document_id is None
    assert f.for_document("d9").document_id == "d9"
