"""Claims: list, inspect, answer questions, submit, and (for approvers) decide."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Query

from claimpilot.api.deps import ApproverDep, ContainerDep, PersonaDep
from claimpilot.api.schemas import AnswersIn, DecisionIn, PromptOut, ReplyIn, ReplyOut, SubmitIn
from claimpilot.claims import combined_prompt
from claimpilot.pipeline import actions
from claimpilot.pipeline.views import ClaimView
from claimpilot.problem import Problem

router = APIRouter(prefix="/v1", tags=["claims"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": Problem}}


@router.get("/claims", response_model=list[ClaimView], summary="Claims you may see, newest first")
async def list_claims(
    container: ContainerDep,
    persona: PersonaDep,
    status: Annotated[str | None, Query(description="draft|needs_info|ready|submitted|...")] = None,
    route: Annotated[str | None, Query(description="auto_approve | finance_review")] = None,
    employee_id: Annotated[str | None, Query(description="Approvers only")] = None,
) -> list[ClaimView]:
    owner = employee_id if persona.is_approver else persona.id
    return await container.repo.list_claims(employee_id=owner, status=status, route=route)


@router.get(
    "/claims/{claim_id}", response_model=ClaimView, responses=NOT_FOUND, summary="One claim"
)
async def get_claim(claim_id: str, container: ContainerDep, persona: PersonaDep) -> ClaimView:
    return await actions.visible_claim(container, persona, claim_id)


@router.get(
    "/claims/{claim_id}/prompt",
    response_model=PromptOut,
    responses=NOT_FOUND,
    summary="The single message that asks everything still open",
)
async def get_prompt(claim_id: str, container: ContainerDep, persona: PersonaDep) -> PromptOut:
    claim = await actions.visible_claim(container, persona, claim_id)
    return PromptOut(
        prompt=combined_prompt(claim), open_question_ids=[q.id for q in claim.unanswered]
    )


@router.post(
    "/claims/{claim_id}/answers",
    response_model=ClaimView,
    responses={
        **NOT_FOUND,
        403: {"model": Problem},
        409: {"model": Problem},
        422: {"model": Problem},
    },
    summary="Answer one or more open questions",
)
async def post_answers(
    claim_id: str, body: AnswersIn, container: ContainerDep, persona: PersonaDep
) -> ClaimView:
    return await actions.apply_answers(container, persona, claim_id, body.answers)


@router.post(
    "/claims/{claim_id}/reply",
    response_model=ReplyOut,
    responses={
        **NOT_FOUND,
        403: {"model": Problem},
        409: {"model": Problem},
        422: {"model": Problem},
    },
    summary="Reply in plain words; the assistant works out which questions it answers",
)
async def post_reply(
    claim_id: str, body: ReplyIn, container: ContainerDep, persona: PersonaDep
) -> ReplyOut:
    return await actions.reply(container, persona, claim_id, body.text)


@router.post(
    "/claims/{claim_id}/submit",
    response_model=ClaimView,
    responses={
        **NOT_FOUND,
        403: {"model": Problem},
        409: {"model": Problem},
        422: {"model": Problem},
    },
    summary="Confirm and submit to the finance system (idempotent)",
)
async def post_submit(
    claim_id: str,
    body: SubmitIn,
    container: ContainerDep,
    persona: PersonaDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ClaimView:
    return await actions.submit(
        container, persona, claim_id, confirmed=body.confirmed, idempotency_key=idempotency_key
    )


@router.get(
    "/approvals",
    response_model=list[ClaimView],
    summary="Claims awaiting a decision (approvers)",
)
async def approvals(
    container: ContainerDep,
    approver: ApproverDep,
    status: Annotated[str, Query(description="submitted | approved | rejected")] = "submitted",
) -> list[ClaimView]:
    return await container.repo.list_claims(status=status)


@router.post(
    "/claims/{claim_id}/decision",
    response_model=ClaimView,
    responses={**NOT_FOUND, 403: {"model": Problem}, 409: {"model": Problem}},
    summary="Approve or reject a submitted claim (approvers)",
)
async def post_decision(
    claim_id: str, body: DecisionIn, container: ContainerDep, approver: ApproverDep
) -> ClaimView:
    return await actions.decide_claim(
        container, approver, claim_id, approved=body.approved, comment=body.comment
    )
