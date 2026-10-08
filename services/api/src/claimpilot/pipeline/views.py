"""Read models returned by the API (and assembled by the repository)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from claimpilot.domain.claims import Claim, ProcessedDocument


class DocumentView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    filename: str
    position: int
    status: str  # queued | processed | failed
    error: str | None = None
    document: ProcessedDocument | None = None
    trust_score: int | None = None
    verdict: str | None = None


class ClaimView(Claim):
    """A claim plus where it came from and how it will be routed."""

    batch_id: str | None = None
    route: str | None = None  # auto_approve | finance_review


def to_claim(view: Claim) -> Claim:
    """The plain ``Claim`` inside a ``ClaimView`` (drops batch id and route before storing)."""
    return Claim.model_validate(view.model_dump(exclude={"batch_id", "route"}))


class BatchView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    employee_id: str
    status: str  # queued | processing | done | failed
    total: int
    processed: int
    failed: int
    created_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    documents: list[DocumentView] = []
    claims: list[ClaimView] = []
