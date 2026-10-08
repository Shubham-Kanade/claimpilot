"""Who is acting: the demo persona switcher (ADR-010) and the signed-in persona."""

from __future__ import annotations

from fastapi import APIRouter

from claimpilot.api.deps import ContainerDep, PersonaDep
from claimpilot.api.schemas import Me
from claimpilot.domain.claims import Employee
from claimpilot.problem import Problem

router = APIRouter(prefix="/v1", tags=["people"])


@router.get("/employees", response_model=list[Employee], summary="Demo personas to act as")
async def list_employees(container: ContainerDep) -> list[Employee]:
    return await container.directory.list()


@router.get(
    "/me",
    response_model=Me,
    responses={401: {"model": Problem}},
    summary="The persona named by X-Persona",
)
async def me(persona: PersonaDep) -> Me:
    return Me(employee=persona.employee, is_approver=persona.is_approver)
