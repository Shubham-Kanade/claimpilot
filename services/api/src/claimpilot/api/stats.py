"""The impact meter: what ClaimPilot has done so far, and what it cost."""

from __future__ import annotations

from fastapi import APIRouter

from claimpilot.api.deps import ContainerDep, PersonaDep
from claimpilot.api.schemas import Stats
from claimpilot.pipeline.repo import collect_stats

router = APIRouter(prefix="/v1", tags=["stats"])


@router.get("/stats", response_model=Stats, summary="Documents, claims, LLM spend and time saved")
async def get_stats(container: ContainerDep, persona: PersonaDep) -> Stats:
    return Stats(**await collect_stats(container.sessions, sandbox=persona.sandbox))
