"""What this server reads from the ClaimPilot REST API: only the fields it uses, and no more.

These are parse models, written by hand from the API's OpenAPI document, deliberately small and
tolerant (unknown fields are ignored, so the API may grow). Every field without a default must be
one the API always sends; ``tests/unit/test_contract.py`` checks all of this against the committed
``apps/web/openapi.json`` so a renamed or optional field breaks a test, not a tool call.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ApiFinding(ApiModel):
    code: str
    severity: str
    message: str
    clause_id: str | None = None
    clause_text: str | None = None
    document_id: str | None = None


class ApiQuestion(ApiModel):
    id: str
    kind: str
    text: str
    answer: str | None = None

    @property
    def answered(self) -> bool:
        """The API's own rule: a blank answer leaves the question open."""
        return bool(self.answer and self.answer.strip())


class ApiClaim(ApiModel):
    id: str
    employee_id: str
    title: str
    mode: str
    document_ids: list[str]
    total: float
    status: str = "draft"
    currency: str = "INR"
    start_date: date | None = None
    end_date: date | None = None
    city: str | None = None
    findings: list[ApiFinding] = []
    open_questions: list[ApiQuestion] = []
    submission_reference: str | None = None
    batch_id: str | None = None
    route: str | None = None


class ApiReceipt(ApiModel):
    doc_type: str
    merchant_name: str | None = None
    date: str | None = None
    total: float | None = None
    currency: str | None = None


class ApiDecisions(ApiModel):
    category: str


class ApiProcessedDocument(ApiModel):
    receipt: ApiReceipt
    decisions: ApiDecisions


class ApiDocument(ApiModel):
    id: str
    filename: str
    status: str
    error: str | None = None
    document: ApiProcessedDocument | None = None
    trust_score: int | None = None
    verdict: str | None = None


class ApiBatch(ApiModel):
    id: str
    status: str
    total: int
    processed: int
    failed: int
    error: str | None = None
    documents: list[ApiDocument] = []
    claims: list[ApiClaim] = []

    @property
    def finished(self) -> bool:
        """A batch in either final state will not change any more."""
        return self.status in ("done", "failed")


class ApiDocumentRef(ApiModel):
    id: str
    filename: str


class ApiBatchCreated(ApiModel):
    batch_id: str
    status: str
    documents: list[ApiDocumentRef]


class ApiReply(ApiModel):
    claim: ApiClaim
    understood: dict[str, str]
    follow_up: str | None


class ApiMeta(ApiModel):
    """``GET /v1/meta``: the runtime limits the API enforces (older APIs omit them)."""

    max_batch_files: int | None = None
    max_upload_mb: int | None = None


class ApiEmployee(ApiModel):
    id: str
    name: str


class ApiMe(ApiModel):
    employee: ApiEmployee
    is_approver: bool
