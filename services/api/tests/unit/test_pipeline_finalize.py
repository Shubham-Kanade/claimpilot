"""Finishing a claim after the pipeline built it, and again after every answer."""

from __future__ import annotations

from test_policy_factories import TODAY, doc, employee

from claimpilot.claims import answer_question, group_documents, route
from claimpilot.claims.labels import quote
from claimpilot.domain import (
    Claim,
    ClaimStatus,
    DocType,
    ExpenseCategory,
    ProcessedDocument,
    QuestionKind,
)
from claimpilot.pipeline.finalize import (
    CATEGORY_QUESTION_PREFIX,
    refinalize,
    with_category_questions,
)
from claimpilot.policy import Policy

EMP = employee("L3", "Pune")
POLICY = Policy.load()
GATE = 0.7


def unsure(
    doc_id: str = "d1",
    confidence: float = 0.5,
    category: ExpenseCategory = ExpenseCategory.misc,
    **kw,
) -> ProcessedDocument:
    """A document whose category System One was not sure of."""
    sure = doc(doc_id, category, **kw)
    decisions = sure.decisions.model_copy(update={"category_confidence": confidence})
    return sure.model_copy(update={"decisions": decisions})


def dinner(total: float = 12000.0) -> ProcessedDocument:
    return doc("d1", ExpenseCategory.client_entertainment, total=total, city="Pune")


def grouped(docs: list[ProcessedDocument]) -> Claim:
    [claim] = group_documents(EMP, docs, today=TODAY)
    return claim


def claim_of(docs: list[ProcessedDocument]) -> Claim:
    """One claim holding every document (grouping is tested elsewhere; this is about questions)."""
    return grouped(docs[:1]).model_copy(update={"document_ids": [d.id for d in docs]})


def refinal(claim: Claim, docs: list[ProcessedDocument]) -> Claim:
    return refinalize(claim, docs, POLICY, EMP, today=TODAY, min_confidence=GATE)


def category_questions(claim: Claim):
    return [q for q in claim.open_questions if q.id.startswith(CATEGORY_QUESTION_PREFIX)]


# --- with_category_questions -----------------------------------------------------------------


def test_a_confident_category_asks_nothing():
    docs = [doc("d1", ExpenseCategory.meals)]
    claim = grouped(docs)
    assert with_category_questions(claim, docs, GATE) is claim


def test_a_confidence_exactly_at_the_gate_is_trusted():
    docs = [unsure(confidence=GATE)]
    claim = grouped(docs)
    assert with_category_questions(claim, docs, GATE) is claim


def test_an_unsure_category_asks_what_the_document_was_for():
    docs = [unsure(total=120.0, merchant="Chameli Garg")]
    claim = with_category_questions(grouped(docs), docs, GATE)
    [question] = category_questions(claim)
    assert question.id == "q-category-d1"
    assert question.kind is QuestionKind.other
    assert question.document_ids == ["d1"]
    assert question.text == (
        f"What was the ₹120 restaurant bill from {quote('Chameli Garg')} on 12 Aug for?"
    )
    assert claim.status is ClaimStatus.needs_info


def test_a_document_without_a_date_is_asked_about_without_one():
    docs = [unsure(day=None)]
    [question] = category_questions(with_category_questions(grouped(docs), docs, GATE))
    assert " on " not in question.text and question.text.endswith(" for?")


def test_the_category_question_is_never_added_twice():
    docs = [unsure()]
    once = with_category_questions(grouped(docs), docs, GATE)
    twice = with_category_questions(once, docs, GATE)
    assert twice is once
    assert len(category_questions(twice)) == 1


def test_only_the_unsure_documents_are_asked_about():
    docs = [unsure("d1"), doc("d2", ExpenseCategory.meals), unsure("d3", confidence=0.2)]
    claim = with_category_questions(claim_of(docs), docs, GATE)
    assert [q.document_ids for q in category_questions(claim)] == [["d1"], ["d3"]]


def test_a_document_already_asked_about_as_personal_is_not_asked_twice():
    docs = [unsure(confidence=0.4, personal=0.8)]  # unsure category AND looks personal
    claim = refinal(grouped(docs), docs)
    assert [q.kind for q in claim.open_questions] == [QuestionKind.confirm_personal]
    assert category_questions(claim) == []


def test_a_receiptless_declaration_already_asks_what_it_was_for():
    slip = unsure(
        confidence=0.4,
        category=ExpenseCategory.local_conveyance,
        doc_type=DocType.handwritten_bill,
        total=120.0,
        city=None,
    )
    claim = refinal(grouped([slip]), [slip])
    assert QuestionKind.self_declaration in {q.kind for q in claim.open_questions}
    assert category_questions(claim) == []


# --- refinalize -------------------------------------------------------------------------------


def test_refinalizing_keeps_the_category_question_and_its_answer():
    docs = [unsure(total=120.0)]
    claim = with_category_questions(grouped(docs), docs, GATE)
    claim = answer_question(claim, "q-category-d1", "Snacks for the team offsite")

    again = refinal(claim, docs)

    [question] = category_questions(again)
    assert question.answer == "Snacks for the team offsite"
    assert again.status is ClaimStatus.ready


def test_a_category_question_nobody_answered_still_blocks_the_claim():
    docs = [unsure(total=120.0)]
    claim = with_category_questions(grouped(docs), docs, GATE)
    again = refinal(claim, docs)
    assert [q.id for q in again.unanswered] == ["q-category-d1"]
    assert again.status is ClaimStatus.needs_info


def test_refinalizing_a_claim_the_pipeline_never_asked_about_adds_the_question():
    docs = [unsure()]
    again = refinal(grouped(docs), docs)
    assert [q.id for q in category_questions(again)] == ["q-category-d1"]


def test_the_per_head_check_changes_the_findings_and_the_route_with_the_answer():
    docs = [dinner(9000.0)]
    claim = refinal(grouped(docs), docs)
    attendees = next(q for q in claim.open_questions if q.kind is QuestionKind.attendees)
    purpose = next(q for q in claim.open_questions if q.kind is QuestionKind.business_purpose)
    claim = answer_question(claim, purpose.id, "Quarterly review with Orion Retail")
    assert "entertainment_over_cap" not in {f.code for f in claim.findings}  # nobody counted yet

    three = refinal(answer_question(claim, attendees.id, "Rahul Mehra and Anita Rao"), docs)
    assert [f.code for f in three.findings] == ["entertainment_over_cap"]  # 3,000 a head
    assert three.status is ClaimStatus.ready and route(three) == "finance_review"

    six = refinal(answer_question(three, attendees.id, "Six of us from Orion Retail"), docs)
    assert six.findings == []  # 1,500 a head
    assert six.status is ClaimStatus.ready and route(six) == "auto_approve"


def test_refinalizing_is_stable():
    docs = [dinner(8000.0), unsure("d2", total=90.0)]
    claim = with_category_questions(claim_of(docs), docs, GATE)
    once = refinal(claim, docs)
    assert refinal(once, docs) == once
