"""The public demo's "start over": delete what a visitor has uploaded so the samples can be re-run.

Only available with ``DEMO_MODE=1``. An employee persona clears their own uploads and claims; the
approver persona clears everyone's (the approvals queue is theirs to empty). Nothing here can
spend money: LLM costs are never touched, and the endpoint does not call a model.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel

from claimpilot.api.deps import ContainerDep, PersonaDep
from claimpilot.problem import Problem, problem

router = APIRouter(prefix="/v1/demo", tags=["demo"])


class ResetResult(BaseModel):
    batches: int
    documents: int
    claims: int


@router.post(
    "/reset",
    response_model=ResetResult,
    responses={404: {"model": Problem, "description": "Not running in demo mode"}},
    summary="Start over: delete your uploads and claims (demo mode only)",
)
async def reset(container: ContainerDep, persona: PersonaDep) -> ResetResult:
    if not container.settings.demo_mode:
        raise problem(status.HTTP_404_NOT_FOUND, "demo_disabled", "Start over is for the demo only")
    deleted = await container.repo.delete_data(None if persona.is_approver else persona.id)
    for key in deleted.storage_keys:
        await container.storage.delete(key)
    await container.repo.audit(
        persona.id,
        "demo_reset",
        "employee",
        persona.id,
        {"everyone": persona.is_approver, "documents": deleted.documents, "claims": deleted.claims},
    )
    return ResetResult(batches=deleted.batches, documents=deleted.documents, claims=deleted.claims)
