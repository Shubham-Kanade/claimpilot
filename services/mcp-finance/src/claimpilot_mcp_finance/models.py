"""Wire models of the mock finance system: the structured results its tools return."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ClaimStatus = Literal["received", "under_review", "approved", "rejected"]
Decision = Literal["approved", "rejected"]

FINAL_STATUSES: frozenset[str] = frozenset({"approved", "rejected"})


class StatusEvent(BaseModel):
    """One step in a claim's history."""

    model_config = ConfigDict(frozen=True)

    status: ClaimStatus
    at: datetime = Field(description="When the claim entered this status (UTC).")
    actor: str | None = Field(
        default=None, description="Who caused the change; null for the initial receipt."
    )
    comment: str = ""


class SubmissionReceipt(BaseModel):
    """What ``submit_claim`` returns."""

    model_config = ConfigDict(frozen=True)

    reference: str = Field(description="Finance reference, e.g. 'FIN-2026-000123'.")
    status: Literal["received"] = "received"
    received_at: datetime = Field(description="When finance first received the claim (UTC).")
    duplicate: bool = Field(
        default=False,
        description="True when this idempotency key was used before: the original reference "
        "is returned and nothing new was created.",
    )


class ClaimSummary(BaseModel):
    """A claim without its history (what ``list_claims`` returns)."""

    model_config = ConfigDict(frozen=True)

    reference: str
    claim_id: str = Field(description="The ClaimPilot claim id that was submitted.")
    employee_id: str
    title: str
    total: float
    currency: str
    document_ids: list[str]
    status: ClaimStatus
    received_at: datetime
    updated_at: datetime


class ClaimRecord(ClaimSummary):
    """A claim with its full status history (oldest first)."""

    history: list[StatusEvent]
