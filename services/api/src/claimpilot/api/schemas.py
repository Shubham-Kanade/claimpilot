"""Request/response bodies that are specific to the HTTP API."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from claimpilot.domain.claims import Employee
from claimpilot.pipeline.views import ClaimView


class DocumentRef(BaseModel):
    id: str
    filename: str


class BatchCreated(BaseModel):
    batch_id: str
    status: str
    documents: list[DocumentRef]
    events_url: str = Field(description="Server-sent events stream with live progress")


class AnswersIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: dict[str, Annotated[str, StringConstraints(max_length=2000)]] = Field(
        max_length=50, description="open question id -> the employee's answer"
    )


class SubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = Field(description="Must be true: the employee explicitly confirms")


class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000, description="Free-text reply to the question")


class ReplyOut(BaseModel):
    claim: ClaimView
    understood: dict[str, str] = Field(description="question id -> answer taken from the reply")
    follow_up: str | None = Field(
        description="ONE message with whatever is still open; null when the claim is complete"
    )


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    comment: str = Field(default="", max_length=500)


class PromptOut(BaseModel):
    prompt: str | None = Field(description="ONE message with every open question; null if none")
    open_question_ids: list[str]


class Me(BaseModel):
    employee: Employee
    is_approver: bool


class Stats(BaseModel):
    documents_processed: int
    documents_failed: int
    claims: int
    claims_by_status: dict[str, int]
    auto_approvable_claims: int
    llm_calls: int
    llm_cost_usd: float
    llm_cost_per_document_usd: float | None
    avg_batch_seconds: float | None
    assumed_manual_minutes_per_document: float
    estimated_minutes_saved: float
