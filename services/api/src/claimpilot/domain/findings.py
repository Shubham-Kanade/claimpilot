"""Findings: evidence-carrying flags from trust and policy checks, shown to users and approvers."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    info = "info"  # worth showing, never blocks
    warn = "warn"  # needs a human glance or an answer from the employee
    high = "high"  # blocks auto-approval


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(description="Stable machine code, e.g. 'total_mismatch'")
    severity: Severity
    message: str = Field(description="One sentence a non-expert understands")
    fields: tuple[str, ...] = Field(default=(), description="Receipt fields the finding is about")
    expected: float | str | None = None
    actual: float | str | None = None
