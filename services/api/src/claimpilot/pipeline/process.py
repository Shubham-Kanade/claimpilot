"""The receipt pipeline: one uploaded batch in, claims out.

Three phases, shaped by what is slow and what must be deterministic:

1. **Read + decide** (parallel, bounded): rasterise the file, extract the receipt with the
   vision model, then ask System One for category / alcohol / personal. These are network calls,
   so they overlap; a document that fails here is marked failed and the rest carry on.
2. **Trust + persist** (sequential, in upload order): duplicate, forensic, injection and
   arithmetic checks. Sequential on purpose: when the same receipt appears twice in one upload
   the *second* is the duplicate, every time, not whichever finished reading first.
3. **Claims**: group the processed documents, apply policy, pull answers from the calendar,
   add a question for anything the models were unsure about, route each claim.

Every step publishes a typed event (the web app streams them) and AI decisions go to the audit
trail. The job is idempotent: a retried batch skips documents already processed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from claimpilot.claims import apply_calendar, build_claims, refresh_status, route
from claimpilot.claims import labels as claim_labels
from claimpilot.config import Settings
from claimpilot.db import Document
from claimpilot.decisions import DecisionEngine, decide_document
from claimpilot.domain import Claim, ExtractedReceipt
from claimpilot.domain.claims import (
    Decisions,
    Employee,
    OpenQuestion,
    ProcessedDocument,
    QuestionKind,
)
from claimpilot.extraction import ReceiptExtractor, prepare_document
from claimpilot.extraction.preprocess import PreparedDocument
from claimpilot.pipeline import events as ev
from claimpilot.pipeline.repo import Repository
from claimpilot.pipeline.views import BatchView
from claimpilot.policy import Policy
from claimpilot.ports import CalendarSource, EmployeeDirectory
from claimpilot.storage import Storage
from claimpilot.trust import DuplicateIndex, assess_document

logger = logging.getLogger(__name__)

CALENDAR_MARGIN = timedelta(days=1)
MAX_ERROR_CHARS = 200


@dataclass(frozen=True)
class PipelineDeps:
    repo: Repository
    storage: Storage
    events: ev.EventBus
    extractor: ReceiptExtractor
    decisions: DecisionEngine
    policy: Policy
    directory: EmployeeDirectory
    calendar: CalendarSource
    index: DuplicateIndex
    settings: Settings
    concurrency: int = 4
    clock: Callable[[], date] = date.today

    def today(self) -> date:
        return self.settings.demo_today or self.clock()


@dataclass(frozen=True)
class _Read:
    """Phase-1 result for one document."""

    row: Document
    raw: bytes
    prepared: PreparedDocument
    receipt: ExtractedReceipt
    decisions: Decisions
    cost_usd: float


def _short(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]


async def process_batch(deps: PipelineDeps, batch_id: str) -> None:
    """Run the whole pipeline for one batch; never raises (failures become events and status)."""
    batch = await deps.repo.get_batch(batch_id)
    if batch is None or batch.status in ("done", "failed"):
        return  # unknown, or a retried job for work that already finished
    try:
        await _run(deps, batch)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # one bad batch must not take the worker down
        logger.exception("batch %s failed", batch_id)
        await deps.repo.mark_batch(batch_id, "failed", error=_short(exc))
        await deps.events.publish(ev.BatchFailed(batch_id=batch_id, error=_short(exc)))


async def _run(deps: PipelineDeps, batch: BatchView) -> None:
    employee = await deps.directory.get(batch.employee_id)
    if employee is None:
        raise LookupError(f"unknown employee {batch.employee_id}")
    await deps.repo.mark_batch(batch.id, "processing")
    rows = await deps.repo.batch_documents(batch.id)
    await deps.events.publish(ev.BatchStarted(batch_id=batch.id, total=len(rows)))

    reads, read_failures = await _read_all(
        deps, batch.id, [r for r in rows if r.status == "queued"]
    )
    checked, check_failures = await _check_all(deps, batch.id, reads)

    earlier = [
        ProcessedDocument.model_validate(r.data) for r in rows if r.status == "processed" and r.data
    ]
    position = {r.id: r.position for r in rows}
    documents = sorted([*earlier, *checked], key=lambda d: position[d.id])
    claims = await _form_claims(deps, batch.id, employee, documents)

    failed = sum(r.status == "failed" for r in rows) + read_failures + check_failures
    cost = sum(read.cost_usd for read in reads)
    await deps.repo.mark_batch(batch.id, "done", processed=len(documents), failed=failed)
    await deps.events.publish(
        ev.BatchDone(
            batch_id=batch.id,
            processed=len(documents),
            failed=failed,
            claims=len(claims),
            cost_usd=round(cost, 6),
        )
    )


# --- phase 1: read + decide -----------------------------------------------------------------


async def _read_all(
    deps: PipelineDeps, batch_id: str, rows: Sequence[Document]
) -> tuple[list[_Read], int]:
    gate = asyncio.Semaphore(deps.concurrency)

    async def one(row: Document) -> _Read | None:
        async with gate:
            try:
                read = await _read_one(deps, row)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await _fail(deps, batch_id, row, exc)
                return None
            await deps.events.publish(
                ev.DocumentExtracted(
                    batch_id=batch_id,
                    document_id=row.id,
                    filename=row.filename,
                    position=row.position,
                    doc_type=read.receipt.doc_type.value,
                    merchant=read.receipt.merchant_name,
                    total=read.receipt.total,
                    category=read.decisions.category.value,
                    category_confidence=read.decisions.category_confidence,
                    engine=read.decisions.engine,
                    cached=read.cost_usd == 0.0,
                    cost_usd=round(read.cost_usd, 6),
                )
            )
            return read

    results = await asyncio.gather(*(one(row) for row in rows))
    reads = sorted((r for r in results if r is not None), key=lambda r: r.row.position)
    return reads, len(rows) - len(reads)


async def _read_one(deps: PipelineDeps, row: Document) -> _Read:
    raw = await deps.storage.get(row.storage_key)
    prepared = await asyncio.to_thread(
        prepare_document, raw, max_pdf_pages=deps.settings.max_pdf_pages
    )
    extraction = await deps.extractor.extract(prepared)
    decisions, result = await decide_document(deps.decisions, extraction.receipt)
    return _Read(
        row=row,
        raw=raw,
        prepared=prepared,
        receipt=extraction.receipt,
        decisions=decisions,
        cost_usd=extraction.cost_usd + result.cost_usd,
    )


async def _fail(deps: PipelineDeps, batch_id: str, row: Document, exc: BaseException) -> None:
    logger.warning("document %s failed: %s", row.id, _short(exc))
    await deps.repo.fail_document(row.id, _short(exc))
    await deps.events.publish(
        ev.DocumentFailed(
            batch_id=batch_id, document_id=row.id, filename=row.filename, error=_short(exc)
        )
    )


# --- phase 2: trust + persist ---------------------------------------------------------------


async def _check_all(
    deps: PipelineDeps, batch_id: str, reads: Sequence[_Read]
) -> tuple[list[ProcessedDocument], int]:
    documents: list[ProcessedDocument] = []
    failures = 0
    for read in reads:  # upload order: see the module docstring
        row = read.row
        try:
            report = await assess_document(
                document_id=row.id,
                filename=row.filename,
                raw=read.raw,
                prepared=read.prepared,
                receipt=read.receipt,
                employee_id=row.employee_id,
                index=deps.index,
            )
            processed = ProcessedDocument(
                id=row.id,
                filename=row.filename,
                sha256=row.sha256,
                receipt=read.receipt,
                decisions=read.decisions,
                findings=report.findings,
            )
            await deps.repo.save_document(
                row.id,
                processed,
                phash=report.phash,
                fingerprint=report.fingerprint,
                trust={"score": report.score, "verdict": report.verdict},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failures += 1
            await _fail(deps, batch_id, row, exc)
            continue
        documents.append(processed)
        await deps.repo.audit(
            read.decisions.engine,
            "document_processed",
            "document",
            row.id,
            {
                "category": read.decisions.category.value,
                "category_confidence": read.decisions.category_confidence,
                "alcohol_present": read.decisions.alcohol_present,
                "personal_expense": read.decisions.personal_expense,
                "trust_score": report.score,
                "verdict": report.verdict,
                "findings": [f.code for f in report.findings],
                "cost_usd": round(read.cost_usd, 6),
            },
        )
        await deps.events.publish(
            ev.DocumentChecked(
                batch_id=batch_id,
                document_id=row.id,
                trust_score=report.score,
                verdict=report.verdict,
                findings=len(report.findings),
            )
        )
    return documents, failures


# --- phase 3: claims ------------------------------------------------------------------------


async def _form_claims(
    deps: PipelineDeps, batch_id: str, employee: Employee, documents: Sequence[ProcessedDocument]
) -> list[Claim]:
    if not documents:
        return []
    by_id = {d.id: d for d in documents}
    claims = build_claims(employee, documents, deps.policy, today=deps.today())
    finished: list[Claim] = []
    for claim in claims:
        members = [by_id[i] for i in claim.document_ids]
        claim = await _apply_calendar(deps, employee, claim, members)
        claim = with_category_questions(claim, members, deps.settings.decision_min_confidence)
        finished.append(claim)
    routes = {c.id: route(c) for c in finished}
    await deps.repo.save_claims(batch_id, employee.id, finished, routes)
    await deps.events.publish(ev.ClaimsReady(batch_id=batch_id, claim_ids=[c.id for c in finished]))
    return finished


async def _apply_calendar(
    deps: PipelineDeps, employee: Employee, claim: Claim, members: Sequence[ProcessedDocument]
) -> Claim:
    """Let the employee's calendar answer attendee/purpose questions (best effort)."""
    if not claim.unanswered or claim.start_date is None or claim.end_date is None:
        return claim
    try:
        events = await deps.calendar.events(
            employee.id, claim.start_date - CALENDAR_MARGIN, claim.end_date + CALENDAR_MARGIN
        )
    except Exception as exc:  # the calendar is a convenience: never fail a batch over it
        logger.warning("calendar unavailable for %s: %s", employee.id, _short(exc))
        return claim
    return apply_calendar(claim, members, events)


def with_category_questions(
    claim: Claim, members: Sequence[ProcessedDocument], min_confidence: float
) -> Claim:
    """Ask what a document was for when System One was not sure of its category.

    Below the confidence gate we ask rather than guess (ADR-021): "What was the ₹120 payment to
    "Chameli Garg" on 3 Oct for?". The question carries the document id so the UI can show it.
    """
    existing = {q.id for q in claim.open_questions}
    added: list[OpenQuestion] = []
    for doc in members:
        question_id = f"q-category-{doc.id[:12]}"
        if doc.decisions.category_confidence >= min_confidence or question_id in existing:
            continue
        day = doc.expense_date
        when = f" on {claim_labels.short_day_label(day)}" if day else ""
        added.append(
            OpenQuestion(
                id=question_id,
                kind=QuestionKind.other,
                text=f"What was {claim_labels.describe(doc)}{when} for?",
                document_ids=[doc.id],
            )
        )
    if not added:
        return claim
    return refresh_status(
        claim.model_copy(update={"open_questions": [*claim.open_questions, *added]})
    )
