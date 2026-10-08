"""Finishing a claim: policy findings, questions and status, kept consistent as answers arrive.

``claims.finalize_claim`` rebuilds a claim's findings and questions from its documents. Two
things must survive that rebuild or be re-added after it: the pipeline's own "what was this for?"
questions (for categories the models were unsure of), and the answers already given. Used both
when a batch is first processed and every time the employee answers, so an answer that changes
the policy picture (the headcount of a client dinner, say) changes the findings and the route.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from claimpilot.claims import finalize_claim, refresh_status
from claimpilot.claims import labels as claim_labels
from claimpilot.domain import Claim
from claimpilot.domain.claims import Employee, OpenQuestion, ProcessedDocument, QuestionKind
from claimpilot.policy import Policy

CATEGORY_QUESTION_PREFIX = "q-category-"
# These already ask what the document was for, so a second question would ask the same twice.
PURPOSE_ASKING_KINDS = frozenset({QuestionKind.confirm_personal, QuestionKind.self_declaration})


def with_category_questions(
    claim: Claim, members: Sequence[ProcessedDocument], min_confidence: float
) -> Claim:
    """Ask what a document was for when System One was not sure of its category.

    Below the confidence gate we ask rather than guess (ADR-021): "What was the ₹120 payment to
    "Chameli Garg" on 3 Oct for?". The question carries the document id so the UI can show it.
    """
    existing = {q.id for q in claim.open_questions}
    explained = {  # documents the employee is already being asked to explain
        doc_id
        for q in claim.open_questions
        if q.kind in PURPOSE_ASKING_KINDS
        for doc_id in q.document_ids
    }
    added: list[OpenQuestion] = []
    for doc in members:
        question_id = f"{CATEGORY_QUESTION_PREFIX}{doc.id[:12]}"
        if (
            doc.decisions.category_confidence >= min_confidence
            or question_id in existing
            or doc.id in explained
        ):
            continue
        day = doc.expense_date
        when = f" on {claim_labels.short_day_label(day)}" if day else ""
        added.append(
            OpenQuestion(
                id=question_id,
                kind=QuestionKind.other,
                text=f"What was {claim_labels.describe(doc)}{when} for?",
                document_ids=[doc.id],
            )
        )
    if not added:
        return claim
    questions = [*claim.open_questions, *added]
    return refresh_status(claim.model_copy(update={"open_questions": questions}))


def refinalize(
    claim: Claim,
    members: Sequence[ProcessedDocument],
    policy: Policy,
    employee: Employee,
    *,
    today: date,
    min_confidence: float,
) -> Claim:
    """Recompute findings, questions and status after something changed, keeping every answer."""
    ours = [q for q in claim.open_questions if q.id.startswith(CATEGORY_QUESTION_PREFIX)]
    rebuilt = finalize_claim(claim, members, policy, employee, today=today)
    if ours:
        known = {q.id for q in rebuilt.open_questions}
        kept = [q for q in ours if q.id not in known]
        rebuilt = rebuilt.model_copy(update={"open_questions": [*rebuilt.open_questions, *kept]})
    return refresh_status(with_category_questions(rebuilt, members, min_confidence))
