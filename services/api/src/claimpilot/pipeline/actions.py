"""What an employee or approver can do to a claim: answer, submit, decide.

Business rules live here (not in the HTTP handlers) so they are unit-testable and so the chat
agent can use the same functions. Every action writes to the audit trail.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import status

from claimpilot.api.deps import Persona
from claimpilot.api.schemas import ReplyOut
from claimpilot.claims import (
    InvalidTransition,
    UnknownQuestion,
    answer_question,
    confirm_and_submit,
    decide,
    route,
)
from claimpilot.container import Container
from claimpilot.domain import ClaimStatus
from claimpilot.pipeline.reply import follow_up_message, interpret_reply
from claimpilot.pipeline.views import ClaimView
from claimpilot.problem import problem


async def visible_claim(container: Container, persona: Persona, claim_id: str) -> ClaimView:
    view = await container.repo.get_claim(claim_id)
    if view is None or not persona.can_see(view.employee_id):
        raise problem(status.HTTP_404_NOT_FOUND, "claim_not_found", "No such claim")
    return view


async def apply_answers(
    container: Container, persona: Persona, claim_id: str, answers: Mapping[str, str]
) -> ClaimView:
    view = await visible_claim(container, persona, claim_id)
    if persona.id != view.employee_id:
        raise problem(status.HTTP_403_FORBIDDEN, "not_your_claim", "Only the owner can answer")
    claim = view
    for question_id, text in answers.items():
        try:
            claim = answer_question(claim, question_id, text)
        except UnknownQuestion as exc:
            raise problem(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "unknown_question",
                f"This claim has no question {question_id}",
            ) from exc
        except ValueError as exc:
            raise problem(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "blank_answer", "An answer cannot be blank"
            ) from exc
        except InvalidTransition as exc:
            raise problem(status.HTTP_409_CONFLICT, "claim_locked", str(exc)) from exc
    await container.repo.update_claim(claim, route=route(claim))
    await container.repo.audit(
        persona.id, "claim_answered", "claim", claim_id, {"questions": sorted(answers)}
    )
    return await visible_claim(container, persona, claim_id)


async def reply(container: Container, persona: Persona, claim_id: str, text: str) -> ReplyOut:
    """The employee's one free-text reply: apply what it answers, ask once more for the rest."""
    view = await visible_claim(container, persona, claim_id)
    if persona.id != view.employee_id:
        raise problem(status.HTTP_403_FORBIDDEN, "not_your_claim", "Only the owner can answer")
    if not view.unanswered:
        return ReplyOut(claim=view, understood={}, follow_up=None)
    understood = await interpret_reply(container.llm, view, text)
    updated = await apply_answers(container, persona, claim_id, understood) if understood else view
    await container.repo.audit(
        persona.id, "claim_replied", "claim", claim_id, {"answered": sorted(understood)}
    )
    return ReplyOut(
        claim=updated,
        understood=understood,
        follow_up=follow_up_message(updated, understood=bool(understood)),
    )


async def submit(
    container: Container,
    persona: Persona,
    claim_id: str,
    *,
    confirmed: bool,
    idempotency_key: str | None,
) -> ClaimView:
    view = await visible_claim(container, persona, claim_id)
    if persona.id != view.employee_id:
        raise problem(status.HTTP_403_FORBIDDEN, "not_your_claim", "Only the owner can submit")
    key = idempotency_key or f"submit-{claim_id}"

    if view.status is ClaimStatus.submitted and view.submission_reference:
        return view  # a retry of a submission that already went through: same result
    if not confirmed:
        raise problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "confirmation_required",
            "The employee must explicitly confirm the submission",
        )
    if view.status is not ClaimStatus.ready:
        raise problem(
            status.HTTP_409_CONFLICT,
            "claim_not_ready",
            "The claim is not ready to submit",
            {"status": view.status.value, "unanswered": [q.id for q in view.unanswered]},
        )

    result = await container.finance.submit_claim(view, idempotency_key=key)
    try:
        claim = confirm_and_submit(view, confirmed=True, reference=result.reference)
    except InvalidTransition as exc:  # defensive: state changed between the check and now
        raise problem(status.HTTP_409_CONFLICT, "claim_not_ready", str(exc)) from exc
    await container.repo.update_claim(claim, idempotency_key=key)
    await container.repo.audit(
        persona.id,
        "claim_submitted",
        "claim",
        claim_id,
        {"reference": result.reference, "duplicate": result.duplicate, "total": claim.total},
    )
    return await visible_claim(container, persona, claim_id)


async def decide_claim(
    container: Container,
    approver: Persona,
    claim_id: str,
    *,
    approved: bool,
    comment: str,
) -> ClaimView:
    view = await visible_claim(container, approver, claim_id)
    already = ClaimStatus.approved if approved else ClaimStatus.rejected
    if view.status is already:
        return view  # repeating a decision that was already made is harmless
    if view.status is not ClaimStatus.submitted or not view.submission_reference:
        raise problem(
            status.HTTP_409_CONFLICT,
            "claim_not_submitted",
            "Only a submitted claim can be approved or rejected",
            {"status": view.status.value},
        )
    await container.finance.decide_claim(
        view.submission_reference, approved=approved, approver_id=approver.id, comment=comment
    )
    claim = decide(view, approved=approved)
    await container.repo.update_claim(claim)
    detail: dict[str, Any] = {"approved": approved, "comment": comment, "route": view.route}
    await container.repo.audit(approver.id, "claim_decided", "claim", claim_id, detail)
    return await visible_claim(container, approver, claim_id)
