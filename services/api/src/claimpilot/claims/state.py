"""The claim state machine.

::

    draft --refresh--> needs_info <--answer--> ready --confirm_and_submit--> submitted
                                                                   |--decide--> approved | rejected

``draft``, ``needs_info`` and ``ready`` are *derived* from the claim's questions by
:func:`refresh_status`: any unanswered question means ``needs_info``, none means ``ready``.
Findings never decide readiness (a claim with a policy breach can still be submitted; it is routed
to finance review instead, see :func:`route`). ``submitted``, ``approved`` and ``rejected`` are
only reached by the explicit transitions below, and nothing moves out of them except
``submitted -> approved | rejected``.

All functions are pure: they return a new ``Claim`` and never mutate the one they are given.
"""

from __future__ import annotations

from typing import Literal

from claimpilot.domain import Claim, ClaimStatus, Severity

# Claims at or below this total, with nothing flagged and nothing open, need no human review.
AUTO_APPROVE_LIMIT = 10_000.0

_LOCKED = frozenset({ClaimStatus.submitted, ClaimStatus.approved, ClaimStatus.rejected})


class InvalidTransition(Exception):
    """The requested change is not allowed from the claim's current state."""


class UnknownQuestion(LookupError):
    """The claim has no question with that id."""


def _with_status(claim: Claim, status: ClaimStatus) -> Claim:
    return claim if claim.status is status else claim.model_copy(update={"status": status})


def refresh_status(claim: Claim) -> Claim:
    """Derive ``draft`` / ``needs_info`` / ``ready`` from the questions; leave locked claims alone.

    A claim with no documents has nothing to submit and stays ``draft``.
    """
    if claim.status in _LOCKED:
        return claim
    if not claim.document_ids:
        return _with_status(claim, ClaimStatus.draft)
    return _with_status(claim, ClaimStatus.needs_info if claim.unanswered else ClaimStatus.ready)


def answer_question(claim: Claim, question_id: str, answer: str) -> Claim:
    """Record the employee's answer to one question and refresh the status.

    Answering again replaces the earlier answer (a correction). Raises ``InvalidTransition`` once
    the claim is submitted, ``UnknownQuestion`` for an id the claim does not have and
    ``ValueError`` for a blank answer (it would leave the question open).
    """
    if claim.status in _LOCKED:
        raise InvalidTransition(f"a {claim.status.value} claim can no longer be changed")
    text = answer.strip()
    if not text:
        raise ValueError("an answer cannot be blank")
    if question_id not in {q.id for q in claim.open_questions}:
        raise UnknownQuestion(question_id)
    questions = [
        q.model_copy(update={"answer": text}) if q.id == question_id else q
        for q in claim.open_questions
    ]
    return refresh_status(claim.model_copy(update={"open_questions": questions}))


def confirm_and_submit(claim: Claim, *, confirmed: bool, reference: str) -> Claim:
    """Submit a ``ready`` claim, but only with the employee's explicit confirmation.

    The claim must already be ``ready`` (run :func:`refresh_status` or ``finalize_claim`` first)
    and have no unanswered question. Retrying a submission that already went through with the same
    ``reference`` returns the claim unchanged (idempotent); a different reference on a submitted
    claim is an error.
    """
    if not confirmed:
        raise InvalidTransition("the employee has not confirmed the submission")
    if not reference.strip():
        raise ValueError("a submission reference is required")
    if claim.status is ClaimStatus.submitted and claim.submission_reference == reference:
        return claim
    if claim.status is not ClaimStatus.ready:
        raise InvalidTransition(f"the claim is {claim.status.value}, not ready to submit")
    if claim.unanswered:  # a stale 'ready': never submit past an open question
        raise InvalidTransition("the claim still has unanswered questions")
    return claim.model_copy(
        update={"status": ClaimStatus.submitted, "submission_reference": reference}
    )


def decide(claim: Claim, *, approved: bool) -> Claim:
    """Finance's decision on a submitted claim. Repeating the same decision changes nothing."""
    target = ClaimStatus.approved if approved else ClaimStatus.rejected
    if claim.status is target:
        return claim
    if claim.status is not ClaimStatus.submitted:
        raise InvalidTransition(
            f"only a submitted claim can be {target.value}, this one is {claim.status.value}"
        )
    return claim.model_copy(update={"status": target})


def route(
    claim: Claim, *, limit: float = AUTO_APPROVE_LIMIT
) -> Literal["auto_approve", "finance_review"]:
    """Does this claim need a human? Auto-approve only a small, clean, complete claim.

    Everything below must hold, otherwise it goes to finance review:

    * no ``high`` finding (a hotel over its cap, alcohol, tampering ...)
    * no unanswered question
    * no ``warn`` finding. The brief names warnings from trust; this goes further and counts a
      warning from any source (policy, decisions), since a warning is by definition something a
      person should look at
    * a total of ``limit`` (default 10,000) or less

    Pass the claim after :func:`claimpilot.claims.service.finalize_claim`, which puts every
    document's findings on the claim.
    """
    severities = {f.severity for f in claim.findings}
    if (
        Severity.high in severities
        or Severity.warn in severities
        or claim.unanswered
        or claim.total > limit
    ):
        return "finance_review"
    return "auto_approve"
