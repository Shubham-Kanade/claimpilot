"""What the tools return: compact results built from the API's payloads.

Every string that came from the API (and so, ultimately, from a receipt) is typed ``Ident``,
``Name``, ``Sentence`` or ``Paragraph`` and is cleaned when the result is built (see ``text.py``).
The ``next_step`` fields are the server's own voice: fixed sentences chosen by status, never put
together from data, so nothing a receipt says can reach them.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, SerializerFunctionWrapHandler, model_serializer

from claimpilot_mcp.api_models import (
    ApiBatch,
    ApiClaim,
    ApiDocument,
    ApiFinding,
    ApiQuestion,
    ApiReply,
)
from claimpilot_mcp.text import Ident, Name, Paragraph, Sentence

MAX_CLAIMS_LISTED = 50
MAX_FLAGS = 8
SEVERITY_ORDER = {"high": 0, "warn": 1, "info": 2}

CLAIM_NEXT_STEP = {
    "needs_info": (
        "Questions are open. Ask the human all of them together in one message, then pass "
        "their reply to answer_question."
    ),
    "ready": (
        "Complete. Go through the findings with the human. If they want to file it, call "
        "submit_claim: it first asks for their explicit confirmation."
    ),
    "submitted": "Submitted and waiting for an approver. Check again later with get_claim.",
    "approved": "Approved. Nothing more to do.",
    "rejected": "Rejected by the approver. This is final: tell the human.",
}
DEFAULT_NEXT_STEP = "Show the human where the claim stands."
UPLOADED_NEXT_STEP = (
    "The receipts are uploaded and queued. Call get_batch with this batch_id until "
    "finished is true."
)
BATCH_NEXT_STEP = {
    "processing": "Still processing. Call get_batch again to keep waiting.",
    "failed": "Processing failed. Tell the human: uploading again starts a new batch.",
    "done": (
        "Processing is done. Call get_claim for each claim to review its findings and open "
        "questions with the human."
    ),
    "empty": (
        "Processing finished but no claims were formed. Check failed_documents and tell the human."
    ),
}
SUBMIT_NEXT_STEP = {
    "needs_confirmation": (
        "NOT SUBMITTED. Show the human this claim (total, findings, documents) and ask them to "
        "confirm explicitly. Only after a clear yes, call submit_claim again with confirmed=true."
    ),
    "not_ready": (
        "NOT SUBMITTED: the claim is not ready. Get the open questions answered first "
        "(see open_questions), then try again."
    ),
    "already_submitted": (
        "Nothing was sent now: the claim was submitted before. Tell the human its status and "
        "finance reference."
    ),
    "submitted": (
        "Submitted to finance. Tell the human the finance reference; an approver decides next."
    ),
}
ANSWER_FOLLOW_UP = (
    "Ask the human what follow_up asks, then call answer_question again with their reply."
)
ANSWER_COMPLETE = (
    "Everything is answered. Show the claim to the human (get_claim) and ask whether to submit it."
)
DECISION_NEXT_STEP = "The decision is recorded and final. Tell the human."


class Result(BaseModel):
    """Base of every tool result. Fields that are ``None`` are left out to keep results compact."""

    @model_serializer(mode="wrap")
    def _compact(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {key: value for key, value in handler(self).items() if value is not None}


# -- pieces of a claim ---------------------------------------------------------------------------


class FindingOut(Result):
    severity: Ident  # info | warn | high
    code: Ident
    message: Sentence  # may quote the receipt
    clause_id: Ident | None = None  # the policy clause it cites, e.g. "6.1"
    clause_text: Sentence | None = None
    document_id: Ident | None = None


class QuestionOut(Result):
    id: Ident
    kind: Ident
    question: Sentence  # may quote the receipt
    answer: Sentence | None = None  # unset while the question is open


class DocumentOut(Result):
    document_id: Ident
    filename: Name | None = None
    status: Ident | None = None  # queued | processed | failed
    doc_type: Ident | None = None
    category: Ident | None = None  # the expense category the assistant chose
    merchant: Name | None = None  # as printed on the receipt
    date: Ident | None = None  # as printed on the receipt
    total: float | None = None
    currency: Ident | None = None
    trust_verdict: Ident | None = None
    error: Sentence | None = None


class ClaimSummary(Result):
    claim_id: Ident
    employee_id: Ident
    title: Name  # derived from the receipts
    mode: Ident  # trip | period | event | allowance
    status: Ident  # draft | needs_info | ready | submitted | approved | rejected
    route: Ident | None = None  # auto_approve | finance_review
    total: float
    currency: Ident
    start_date: date | None = None
    end_date: date | None = None
    document_count: int
    open_question_count: int
    high_findings: int
    warnings: int
    flags: list[Ident]  # codes of the high and warn findings
    submission_reference: Ident | None = None


class ClaimDetail(ClaimSummary):
    city: Name | None = None
    findings: list[FindingOut]
    open_questions: list[QuestionOut]
    answered_questions: list[QuestionOut]
    documents: list[DocumentOut]
    next_step: str


# -- tool results --------------------------------------------------------------------------------


class ClaimList(Result):
    count: int  # how many claims the API returned; claims holds the newest ones
    claims: list[ClaimSummary]
    note: str | None = None


class UploadedFile(Result):
    document_id: Ident
    filename: Name


class UploadResult(Result):
    batch_id: Ident
    status: Ident
    files: list[UploadedFile]
    next_step: str


class FailedDocument(Result):
    filename: Name
    error: Sentence | None = None


class BatchProgress(Result):
    batch_id: Ident
    status: Ident  # queued | processing | done | failed
    finished: bool
    total: int
    processed: int
    failed: int
    error: Sentence | None = None
    failed_documents: list[FailedDocument]
    claims: list[ClaimSummary]
    next_step: str


class Understood(Result):
    question_id: Ident
    question: Sentence | None = None
    answer: Sentence


class AnswerResult(Result):
    claim_id: Ident
    status: Ident
    route: Ident | None = None
    understood: list[Understood]  # what the assistant took from the reply, per question
    open_questions: list[QuestionOut]  # what is still unanswered
    follow_up: Paragraph | None = None  # the assistant's one message for the rest
    ready_to_submit: bool
    next_step: str


SubmitState = Literal["needs_confirmation", "not_ready", "already_submitted", "submitted"]


class SubmitResult(Result):
    claim_id: Ident
    state: SubmitState
    submitted: bool  # the claim is in the finance system (now or before)
    submission_reference: Ident | None = None
    claim: ClaimDetail
    next_step: str


class ApprovalList(Result):
    status: Ident  # the filter that was applied
    count: int
    claims: list[ClaimSummary]
    note: str | None = None


class DecisionResult(Result):
    claim_id: Ident
    decision: Literal["approved", "rejected"]
    status: Ident
    comment: Sentence | None = None
    submission_reference: Ident | None = None
    next_step: str


# -- building results from API payloads ----------------------------------------------------------


def finding_out(finding: ApiFinding) -> FindingOut:
    return FindingOut(
        severity=finding.severity,
        code=finding.code,
        message=finding.message,
        clause_id=finding.clause_id,
        clause_text=finding.clause_text,
        document_id=finding.document_id,
    )


def question_out(question: ApiQuestion) -> QuestionOut:
    return QuestionOut(
        id=question.id,
        kind=question.kind,
        question=question.text,
        answer=question.answer if question.answered else None,
    )


def document_out(document_id: str, document: ApiDocument | None = None) -> DocumentOut:
    """A document of a claim; with only its id when the API could not be asked about it."""
    if document is None:
        return DocumentOut(document_id=document_id)
    processed = document.document
    receipt = processed.receipt if processed else None
    return DocumentOut(
        document_id=document.id,
        filename=document.filename,
        status=document.status,
        doc_type=receipt.doc_type if receipt else None,
        category=processed.decisions.category if processed else None,
        merchant=receipt.merchant_name if receipt else None,
        date=receipt.date if receipt else None,
        total=receipt.total if receipt else None,
        currency=receipt.currency if receipt else None,
        trust_verdict=document.verdict,
        error=document.error,
    )


def _flags(claim: ApiClaim) -> list[str]:
    serious = [f for f in claim.findings if f.severity in ("high", "warn")]
    serious.sort(key=lambda f: SEVERITY_ORDER[f.severity])
    return list(dict.fromkeys(f.code for f in serious))[:MAX_FLAGS]


def _summary_fields(claim: ApiClaim) -> dict[str, Any]:
    return {
        "claim_id": claim.id,
        "employee_id": claim.employee_id,
        "title": claim.title,
        "mode": claim.mode,
        "status": claim.status,
        "route": claim.route,
        "total": claim.total,
        "currency": claim.currency,
        "start_date": claim.start_date,
        "end_date": claim.end_date,
        "document_count": len(claim.document_ids),
        "open_question_count": sum(not q.answered for q in claim.open_questions),
        "high_findings": sum(f.severity == "high" for f in claim.findings),
        "warnings": sum(f.severity == "warn" for f in claim.findings),
        "flags": _flags(claim),
        "submission_reference": claim.submission_reference,
    }


def claim_summary(claim: ApiClaim) -> ClaimSummary:
    return ClaimSummary(**_summary_fields(claim))


def claim_detail(claim: ApiClaim, documents: list[DocumentOut]) -> ClaimDetail:
    findings = sorted(claim.findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 3))
    return ClaimDetail(
        **_summary_fields(claim),
        city=claim.city,
        findings=[finding_out(f) for f in findings],
        open_questions=[question_out(q) for q in claim.open_questions if not q.answered],
        answered_questions=[question_out(q) for q in claim.open_questions if q.answered],
        documents=documents,
        next_step=CLAIM_NEXT_STEP.get(claim.status, DEFAULT_NEXT_STEP),
    )


def claim_list(claims: list[ApiClaim]) -> tuple[list[ClaimSummary], str | None]:
    """The newest claims (the API lists newest first), and a note when some were left out."""
    shown = [claim_summary(c) for c in claims[:MAX_CLAIMS_LISTED]]
    note = None
    if len(claims) > len(shown):
        note = f"Showing the first {len(shown)} of {len(claims)}; use the status filter to narrow."
    return shown, note


def batch_progress(batch: ApiBatch) -> BatchProgress:
    if not batch.finished:
        step = BATCH_NEXT_STEP["processing"]
    elif batch.status == "failed":
        step = BATCH_NEXT_STEP["failed"]
    else:
        step = BATCH_NEXT_STEP["done" if batch.claims else "empty"]
    return BatchProgress(
        batch_id=batch.id,
        status=batch.status,
        finished=batch.finished,
        total=batch.total,
        processed=batch.processed,
        failed=batch.failed,
        error=batch.error,
        failed_documents=[
            FailedDocument(filename=d.filename, error=d.error)
            for d in batch.documents
            if d.status == "failed"
        ],
        claims=[claim_summary(c) for c in batch.claims[:MAX_CLAIMS_LISTED]],
        next_step=step,
    )


def answer_result(reply: ApiReply) -> AnswerResult:
    claim = reply.claim
    asked = {q.id: q.text for q in claim.open_questions}
    open_questions = [question_out(q) for q in claim.open_questions if not q.answered]
    if reply.follow_up:
        step = ANSWER_FOLLOW_UP
    elif claim.status == "ready":
        step = ANSWER_COMPLETE
    else:
        step = CLAIM_NEXT_STEP.get(claim.status, DEFAULT_NEXT_STEP)
    return AnswerResult(
        claim_id=claim.id,
        status=claim.status,
        route=claim.route,
        understood=[
            Understood(question_id=qid, question=asked.get(qid), answer=answer)
            for qid, answer in reply.understood.items()
        ],
        open_questions=open_questions,
        follow_up=reply.follow_up or None,
        ready_to_submit=claim.status == "ready",
        next_step=step,
    )
