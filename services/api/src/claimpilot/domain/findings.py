"""Findings: evidence-carrying flags from trust, policy and decision checks.

A finding is what lets an approver (or the employee) see *why* something was flagged, not just
that it was: it names the rule, quotes the policy clause where one applies, and carries the
expected and actual values.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    info = "info"  # worth showing, never blocks
    warn = "warn"  # needs a human glance or an answer from the employee
    high = "high"  # blocks auto-approval


class FindingSource(StrEnum):
    trust = "trust"  # does the document look genuine and internally consistent?
    policy = "policy"  # does the expense comply with the expense policy?
    decision = "decision"  # System One answers (e.g. alcohol on the bill, personal expense)
    system = "system"  # pipeline notes (e.g. a document that could not be read)


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(description="Stable machine code, e.g. 'total_mismatch'")
    severity: Severity
    message: str = Field(description="One sentence a non-expert understands")
    fields: tuple[str, ...] = Field(default=(), description="Receipt fields the finding is about")
    expected: float | str | None = None
    actual: float | str | None = None
    source: FindingSource = FindingSource.trust
    clause_id: str | None = Field(default=None, description="Policy clause cited, e.g. '4.2'")
    clause_text: str | None = Field(default=None, description="The clause wording, quoted")
    document_id: str | None = Field(
        default=None, description="Document the finding is about; set by the pipeline"
    )

    def for_document(self, document_id: str) -> Finding:
        return self.model_copy(update={"document_id": document_id})
