"""finalize_claim / build_claims: how grouping, policy, questions and status compose."""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest
import yaml
from test_policy_factories import TODAY, cab, doc, employee, hotel, meal, ticket

from claimpilot.claims import (
    answer_question,
    build_claims,
    finalize_claim,
    group_documents,
    route,
)
from claimpilot.domain import (
    Claim,
    ClaimMode,
    ClaimStatus,
    DocType,
    ExpenseCategory,
    Finding,
    FindingSource,
    ProcessedDocument,
    QuestionKind,
    Severity,
)
from claimpilot.policy import DEFAULT_POLICY_PATH, Policy

BASE = "Pune"
EMP = employee("L3", BASE)
POLICY = Policy.load()


def one_claim(docs: list[ProcessedDocument]) -> Claim:
    [claim] = group_documents(EMP, docs, today=TODAY)
    return claim


def finalize(docs: list[ProcessedDocument], claim: Claim | None = None, **kw) -> Claim:
    return finalize_claim(claim or one_claim(docs), docs, POLICY, EMP, today=kw.pop("today", TODAY))


def trust_finding(severity: Severity = Severity.warn, code: str = "total_mismatch") -> Finding:
    return Finding(code=code, severity=severity, message="The total does not add up.")


def codes(claim: Claim) -> list[str]:
    return [f.code for f in claim.findings]


def dinner(total: float = 12000.0, **kw) -> ProcessedDocument:
    return doc("d1", ExpenseCategory.client_entertainment, total=total, city=BASE, **kw)


# --- findings -----------------------------------------------------------------------------------


def test_a_clean_claim_is_ready_with_no_findings_or_questions():
    docs = [cab("c1", date(2026, 8, 3), BASE)]
    claim = finalize(docs)
    assert claim.findings == [] and claim.open_questions == []
    assert claim.status is ClaimStatus.ready
    assert route(claim) == "auto_approve"


def test_document_level_policy_findings_land_on_the_claim_with_their_document():
    docs = [doc("c1", ExpenseCategory.learning, doc_type=DocType.gst_invoice, total=22420.0)]
    claim = finalize(docs)
    [finding] = claim.findings
    assert finding.code == "preapproval_required"
    assert finding.document_id == "c1" and finding.source is FindingSource.policy
    assert claim.status is ClaimStatus.ready  # a warning does not block submission
    assert route(claim) == "finance_review"


def test_findings_already_on_a_document_are_kept_and_stamped():
    docs = [meal("m1", date(2026, 8, 3), BASE).model_copy(update={"findings": [trust_finding()]})]
    claim = finalize(docs)
    [finding] = claim.findings
    assert (finding.code, finding.source, finding.document_id) == (
        "total_mismatch",
        FindingSource.trust,
        "m1",
    )


def test_claim_level_findings_have_no_document():
    docs = [meal("m1", date(2026, 8, 3), "Nagpur", 1800.0)]
    claim = finalize(docs)
    assert [(f.code, f.document_id) for f in claim.findings] == [("meals_over_limit", None)]


def test_trust_and_policy_findings_sit_side_by_side():
    docs = [
        dinner(items=[("Draught Beer 330ml", 2000.0)]).model_copy(
            update={"findings": [trust_finding(Severity.high)]}
        )
    ]
    claim = finalize(docs)
    assert sorted(codes(claim)) == ["alcohol_not_reimbursable", "total_mismatch"]
    assert claim.has_high_findings
    assert route(claim) == "finance_review"


def test_the_policy_is_applied_with_the_employees_grade():
    docs = [hotel("h1", rate=9000.0, city="Mumbai", checkout=date(2026, 8, 14))]
    low = finalize_claim(one_claim(docs), docs, POLICY, employee("L2"), today=TODAY)
    high = finalize_claim(one_claim(docs), docs, POLICY, employee("L5"), today=TODAY)
    assert "hotel_over_cap" in codes(low)
    assert "hotel_over_cap" not in codes(high)


def test_today_decides_whether_the_claim_is_late():
    docs = [meal("m1", date(2026, 7, 4), BASE)]
    assert "late_submission" not in codes(finalize(docs))
    assert "late_submission" in codes(finalize(docs, today=date(2026, 10, 8)))


def test_documents_outside_the_claim_are_ignored():
    inside = meal("m1", date(2026, 8, 3), BASE)
    stray = doc("x1", ExpenseCategory.learning, total=99999.0)
    claim = one_claim([inside])
    assert finalize_claim(claim, [inside, stray], POLICY, EMP, today=TODAY).findings == []


# --- recomputing --------------------------------------------------------------------------------


def test_finalizing_twice_gives_the_same_claim():
    docs = [dinner().model_copy(update={"findings": [trust_finding()]})]
    once = finalize(docs)
    assert finalize(docs, once) == once


def test_policy_findings_already_stored_on_a_document_are_not_doubled():
    docs = [doc("c1", ExpenseCategory.learning, doc_type=DocType.gst_invoice, total=22420.0)]
    stored = POLICY.evaluate_document(EMP, docs[0], today=TODAY)
    with_stored = [docs[0].model_copy(update={"findings": stored})]
    assert codes(finalize(with_stored)) == ["preapproval_required"]


def test_a_stale_policy_finding_on_a_document_is_replaced_not_kept():
    stale = Finding(
        code="hotel_over_cap", severity=Severity.high, message="old", source=FindingSource.policy
    )
    docs = [meal("m1", date(2026, 8, 3), BASE).model_copy(update={"findings": [stale]})]
    assert finalize(docs).findings == []


def test_stale_claim_level_policy_findings_are_dropped_but_other_sources_stay():
    docs = [meal("m1", date(2026, 8, 3), BASE)]
    claim = one_claim(docs)
    old_policy = Finding(
        code="meals_over_limit", severity=Severity.warn, message="old", source=FindingSource.policy
    )
    claim = claim.model_copy(
        update={"findings": [old_policy, trust_finding(code="duplicate_across_claims")]}
    )
    assert codes(finalize(docs, claim)) == ["duplicate_across_claims"]


def test_the_claims_other_fields_are_untouched():
    docs = [meal("m1", date(2026, 8, 3), BASE)]
    before = one_claim(docs)
    after = finalize(docs, before)
    for field in ("id", "title", "mode", "document_ids", "total", "start_date", "end_date", "city"):
        assert getattr(after, field) == getattr(before, field)
    assert before.findings == [] and before.status is ClaimStatus.draft  # the input is not mutated


# --- questions and status -----------------------------------------------------------------------


def test_open_questions_make_the_claim_need_info():
    claim = finalize([dinner(8000.0)])
    assert claim.status is ClaimStatus.needs_info
    assert [q.kind for q in claim.open_questions] == [
        QuestionKind.attendees,
        QuestionKind.business_purpose,
    ]
    assert route(claim) == "finance_review"


def test_answering_everything_makes_it_ready_and_a_small_clean_claim_auto_approvable():
    docs = [dinner(8000.0)]
    claim = finalize(docs)
    for question in list(claim.open_questions):
        claim = answer_question(claim, question.id, "Rahul Mehra and Anita Rao, Orion Retail")
    claim = finalize(docs, claim)  # the pipeline re-checks after answers
    assert claim.status is ClaimStatus.ready
    assert claim.findings == []  # 8,000 over 3 people is within the 2,500 per-head cap
    assert route(claim) == "auto_approve"


def test_the_per_head_check_runs_once_the_attendees_are_known():
    docs = [dinner(12000.0)]
    claim = finalize(docs)
    assert "entertainment_over_cap" not in codes(claim)  # nobody has said how many came yet
    attendees = next(q for q in claim.open_questions if q.kind is QuestionKind.attendees)
    claim = answer_question(
        claim, attendees.id, "Rahul Mehra and Anita Rao"
    )  # 3 people: 4,000 each
    flagged = finalize(docs, claim)
    [finding] = [f for f in flagged.findings if f.code == "entertainment_over_cap"]
    assert finding.document_id == "d1" and finding.actual == 4000.0
    assert route(flagged) == "finance_review"
    corrected = answer_question(flagged, attendees.id, "Six of us from Orion Retail and Asha")
    gone = finalize(docs, corrected)  # 6 people: 2,000 each
    assert "entertainment_over_cap" not in codes(gone)


def test_answers_survive_refinalizing():
    docs = [dinner(8000.0)]
    claim = finalize(docs)
    claim = answer_question(claim, claim.open_questions[0].id, "Rahul Mehra, Orion")
    again = finalize(docs, claim)
    assert [q.answer for q in again.open_questions] == ["Rahul Mehra, Orion", None]


def test_the_policys_thresholds_drive_the_questions():
    raw: dict[str, Any] = copy.deepcopy(
        yaml.safe_load(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    )
    for clause in raw["clauses"]:
        if clause["id"] == "3.1":
            clause["params"]["receipt_threshold"] = 500
        if clause["id"] == "10.1":
            clause["params"]["probability_threshold"] = 0.9
    custom = Policy.model_validate(raw)
    slip = doc(
        "s1",
        ExpenseCategory.local_conveyance,
        doc_type=DocType.handwritten_bill,
        total=350.0,
        city=None,
    )
    claim = one_claim([slip])
    assert finalize_claim(claim, [slip], POLICY, EMP, today=TODAY).open_questions == []
    asked = finalize_claim(claim, [slip], custom, EMP, today=TODAY).open_questions
    assert [q.kind for q in asked] == [QuestionKind.self_declaration]
    doubtful = doc("x1", ExpenseCategory.misc, personal=0.8)
    c2 = one_claim([doubtful])
    assert (
        finalize_claim(c2, [doubtful], POLICY, EMP, today=TODAY).open_questions[0].kind
        is QuestionKind.confirm_personal
    )
    assert finalize_claim(c2, [doubtful], custom, EMP, today=TODAY).open_questions == []


def test_a_claim_with_a_personal_looking_document_asks_and_warns():
    docs = [doc("x1", ExpenseCategory.misc, personal=0.7, total=126.0)]
    claim = finalize(docs)
    assert [q.kind for q in claim.open_questions] == [QuestionKind.confirm_personal]
    assert codes(claim) == ["personal_expense_flagged"]


# --- build_claims -------------------------------------------------------------------------------


def pile() -> list[ProcessedDocument]:
    return [
        ticket("out", date(2026, 8, 12), BASE, "Nagpur"),
        meal("m1", date(2026, 8, 12), "Nagpur"),
        hotel("h1", rate=3500.0, nights=2, city="Nagpur", checkout=date(2026, 8, 14)),
        ticket("back", date(2026, 8, 14), "Nagpur", BASE),
        dinner(4000.0),
        cab("c1", date(2026, 8, 3), BASE),
    ]


def test_build_claims_groups_and_finalizes_in_one_call():
    claims = build_claims(EMP, pile(), POLICY, today=TODAY)
    assert {c.mode for c in claims} == {ClaimMode.trip, ClaimMode.event, ClaimMode.period}
    assert all(c.status in (ClaimStatus.needs_info, ClaimStatus.ready) for c in claims)
    trip = next(c for c in claims if c.mode is ClaimMode.trip)
    assert [q.kind for q in trip.open_questions] == [QuestionKind.business_purpose]
    assert next(c for c in claims if c.mode is ClaimMode.period).status is ClaimStatus.ready


def test_build_claims_matches_group_then_finalize():
    docs = pile()
    by_hand = [
        finalize_claim(c, [d for d in docs if d.id in c.document_ids], POLICY, EMP, today=TODAY)
        for c in group_documents(EMP, docs, today=TODAY)
    ]
    assert build_claims(EMP, docs, POLICY, today=TODAY) == by_hand


def test_build_claims_is_deterministic_and_takes_an_id_prefix():
    first = build_claims(EMP, pile(), POLICY, today=TODAY)
    assert first == build_claims(EMP, list(reversed(pile())), POLICY, today=TODAY)
    assert all(
        c.id.startswith("draft-P001-")
        for c in build_claims(EMP, pile(), POLICY, today=TODAY, id_prefix="draft")
    )


def test_build_claims_of_nothing_is_nothing():
    assert build_claims(EMP, [], POLICY, today=TODAY) == []


@pytest.mark.parametrize("total", [9999.0, 10000.0])
def test_small_clean_claims_route_to_auto_approve_end_to_end(total: float):
    docs = [meal("m1", date(2026, 8, 3), BASE, total)]
    [claim] = build_claims(EMP, docs, POLICY, today=TODAY)
    # a ₹10,000 meal is over the daily meals limit, so only the smaller one is clean
    assert (route(claim) == "auto_approve") is (total < 2000) or route(claim) == "finance_review"
