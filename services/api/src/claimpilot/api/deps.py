"""FastAPI dependencies: the container and the acting persona.

There is no real login in the demo (ADR-010: judges must not need access requests). The UI
sends ``X-Persona: <employee id>`` and the API enforces what that persona may see. Approvers
(``Settings.approver_ids``) see every claim; everyone else only their own.

In the public demo every visitor also gets a private copy of the data: the UI sends
``X-Sandbox: <random id>`` and every query is confined to that sandbox (ADR-034). Outside demo mode
the header is ignored and the sandbox is ``None``, the one shared world.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from claimpilot.container import Container
from claimpilot.domain.claims import Employee
from claimpilot.problem import problem


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


SANDBOX_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,64}")


@dataclass(frozen=True, slots=True)
class Persona:
    employee: Employee
    is_approver: bool
    sandbox: str | None = None  # the demo visitor's private world; ``None`` outside the demo

    @property
    def id(self) -> str:
        return self.employee.id

    def can_see(self, employee_id: str) -> bool:
        return self.is_approver or employee_id == self.employee.id


async def get_persona(
    container: ContainerDep,
    x_persona: Annotated[str | None, Header(description="Acting persona (employee id)")] = None,
    x_sandbox: Annotated[
        str | None,
        Header(
            description="Demo sandbox id: a visitor's private copy of the demo data (demo only)"
        ),
    ] = None,
) -> Persona:
    if not x_persona:
        raise problem(status.HTTP_401_UNAUTHORIZED, "missing_persona", "Send an X-Persona header")
    employee = await container.directory.get(x_persona)
    if employee is None:
        raise problem(
            status.HTTP_401_UNAUTHORIZED, "unknown_persona", f"Unknown persona {x_persona}"
        )
    sandbox = None
    if x_sandbox and container.settings.demo_mode:
        if not SANDBOX_PATTERN.fullmatch(x_sandbox):
            raise problem(
                status.HTTP_400_BAD_REQUEST,
                "invalid_sandbox",
                "X-Sandbox must be 16 to 64 letters, digits, '-' or '_'",
            )
        sandbox = x_sandbox
    return Persona(
        employee=employee, is_approver=employee.id in container.approver_ids, sandbox=sandbox
    )


PersonaDep = Annotated[Persona, Depends(get_persona)]


def require_approver(persona: PersonaDep) -> Persona:
    if not persona.is_approver:
        raise problem(status.HTTP_403_FORBIDDEN, "approver_only", "Only approvers can do this")
    return persona


ApproverDep = Annotated[Persona, Depends(require_approver)]

__all__ = [
    "ApproverDep",
    "ContainerDep",
    "HTTPException",
    "Persona",
    "PersonaDep",
    "get_container",
    "get_persona",
    "require_approver",
]
