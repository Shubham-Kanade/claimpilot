"""Claim-level domain types: employees, per-document System One decisions, claims, questions.

These are the contract shared by decisions, trust, policy, claims grouping, the pipeline and the
API. They are plain Pydantic models so OpenAPI and the frontend types derive from them.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from claimpilot.domain.findings import Finding, Severity
from claimpilot.domain.receipt import ExpenseCategory, ExtractedReceipt


class Employee(BaseModel):
    """An employee profile (from the corporate directory, mocked by ``mcp-corp``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(description="Directory id, e.g. 'P001'")
    name: str
    employee_id: str = Field(description="HR employee number, e.g. 'EMP85968'")
    grade: str = Field(description="Grade band L1 (junior) to L5 (senior)")
    base_city: str
    base_state_code: str | None = Field(default=None, description="GST state code of the base city")


class Decisions(BaseModel):
    """System One answers for one document (Jev, or the LLM fallback with the same shape)."""

    model_config = ConfigDict(extra="forbid")

    category: ExpenseCategory
    category_confidence: float = Field(ge=0, le=1)
    alcohol_present: float = Field(ge=0, le=1, description="Probability the bill has alcohol")
    personal_expense: float = Field(ge=0, le=1, description="Probability it is a personal expense")
    engine: str = Field(description="'jev', 'llm', 'fake' or 'truth' (ground truth in tests)")


class Box(BaseModel):
    """A region of one page, as fractions (0 to 1) of the page width and height."""

    model_config = ConfigDict(extra="forbid")

    page: int = 0
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(ge=0, le=1)
    h: float = Field(ge=0, le=1)


class ProcessedDocument(BaseModel):
    """One uploaded document after extraction, decisions and per-document checks."""

    model_config = ConfigDict(extra="forbid")

    id: str
    filename: str
    sha256: str = Field(description="Of the original upload bytes")
    receipt: ExtractedReceipt
    decisions: Decisions
    findings: list[Finding] = Field(default_factory=list)
    boxes: dict[str, Box] = Field(
        default_factory=dict,
        description="Receipt field name -> where it is printed (click-to-verify); may be empty",
    )

    @property
    def category(self) -> ExpenseCategory:
        return self.decisions.category

    @property
    def expense_date(self) -> date | None:
        """The printed date as a ``date`` (None when missing or not ISO)."""
        raw = self.receipt.date
        try:
            return date.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    @property
    def amount(self) -> float:
        return self.receipt.total or 0.0


class ClaimMode(StrEnum):
    trip = "trip"  # one business trip: tickets, hotel, cabs and meals on the road
    period = "period"  # a recurring monthly bucket: mobile bill, local conveyance, fuel
    event = "event"  # one occasion: a client dinner, a course
    allowance = "allowance"  # per-diem or mileage with no receipt


class ClaimStatus(StrEnum):
    draft = "draft"  # just grouped
    needs_info = "needs_info"  # waiting for an answer from the employee
    ready = "ready"  # complete; the employee can confirm and submit
    submitted = "submitted"  # sent to the finance system
    approved = "approved"
    rejected = "rejected"


class QuestionKind(StrEnum):
    attendees = "attendees"  # who attended (client entertainment)
    business_purpose = "business_purpose"  # why the expense was incurred
    missing_date = "missing_date"  # no date is printed on the document
    confirm_personal = "confirm_personal"  # looks personal; confirm or remove it
    self_declaration = "self_declaration"  # no receipt: declare the amount and reason
    other = "other"


class OpenQuestion(BaseModel):
    """Something we need from the employee. The chat asks all open ones in ONE message."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: QuestionKind
    text: str
    document_ids: list[str] = Field(default_factory=list)
    answer: str | None = None

    @property
    def answered(self) -> bool:
        return bool(self.answer and self.answer.strip())


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    employee_id: str
    title: str = Field(description="e.g. 'Pune trip 12-14 Aug' or 'Local conveyance Sept 2026'")
    mode: ClaimMode
    status: ClaimStatus = ClaimStatus.draft
    document_ids: list[str]
    total: float = Field(description="Sum of document totals, in the claim currency")
    currency: str = "INR"
    start_date: date | None = None
    end_date: date | None = None
    city: str | None = None
    findings: list[Finding] = Field(default_factory=list, description="Claim-level findings")
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    submission_reference: str | None = None

    @property
    def unanswered(self) -> list[OpenQuestion]:
        return [q for q in self.open_questions if not q.answered]

    @property
    def has_high_findings(self) -> bool:
        return any(f.severity is Severity.high for f in self.findings)
