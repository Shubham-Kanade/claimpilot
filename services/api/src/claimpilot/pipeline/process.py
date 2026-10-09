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
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from claimpilot.claims import apply_calendar, build_claims, route
from claimpilot.config import Settings
from claimpilot.db import Document
from claimpilot.decisions import DecisionEngine, decide_document
from claimpilot.domain import Claim, ExtractedReceipt
from claimpilot.domain.claims import (
    Box,
    Decisions,
    Employee,
    ProcessedDocument,
)
from claimpilot.extraction import ReceiptExtractor, prepare_document
from claimpilot.extraction.locate import FieldLocator, Located
from claimpilot.extraction.preprocess import PreparedDocument
from claimpilot.llm.errors import ReplayMissError
from claimpilot.pipeline import events as ev
from claimpilot.pipeline.dupindex import DbDuplicateIndex, ScopedIndex
from claimpilot.pipeline.finalize import refinalize
from claimpilot.pipeline.repo import UNSCOPED, Repository
from claimpilot.pipeline.views import BatchView
from claimpilot.policy import Policy
from claimpilot.ports import CalendarSource, EmployeeDirectory
from claimpilot.storage import Storage
from claimpilot.telemetry import bound_ids
from claimpilot.trust import DuplicateIndex, assess_document

logger = logging.getLogger(__name__)

CALENDAR_MARGIN = timedelta(days=1)
# A second opinion costs about twenty times a first read, and an upload of receipts crafted to look
# suspicious could trigger it for every one of them. At most this share of a batch (never fewer
# than MIN_REREADS) is read twice; the rest keep their first read.
REREAD_SHARE = 0.4
MIN_REREADS = 3
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
    locator: FieldLocator | None = None  # click-to-verify boxes; None: no highlights
    concurrency: int = 4
    clock: Callable[[], date] = date.today
    # One lock per employee for the check-and-save step: the duplicate check reads the table and the
    # save writes to it, so two batches of the same receipts must take turns or each misses the
    # other's copy. In-process (embedded runtime, one worker process); with several worker
    # processes a database advisory lock would take its place.
    employee_locks: dict[tuple[str | None, str], asyncio.Lock] = field(default_factory=dict)

    def lock_for(self, employee_id: str, sandbox: str | None = None) -> asyncio.Lock:
        return self.employee_locks.setdefault((sandbox, employee_id), asyncio.Lock())

    def today(self) -> date:
        return self.settings.demo_today or self.clock()


class RereadBudget:
    """How many documents of one batch may still be read a second time."""

    def __init__(self, documents: int) -> None:
        self._left = max(MIN_REREADS, math.ceil(documents * REREAD_SHARE))

    def take(self) -> bool:
        if self._left <= 0:
            return False
        self._left -= 1
        return True


@dataclass(frozen=True)
class _Read:
    """Phase-1 result for one document."""

    row: Document
    raw: bytes
    prepared: PreparedDocument
    receipt: ExtractedReceipt
    decisions: Decisions
    boxes: dict[str, Box]
    cost_usd: float


DEMO_MISS = (
    "This demo reads only its recorded sample receipts. To read your own, run ClaimPilot with "
    "your own API key (see the README)."
)


def _short(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]


def _reason(deps: PipelineDeps, exc: BaseException) -> str:
    """What the employee is told about a document that could not be read."""
    if deps.settings.demo_mode and isinstance(exc, ReplayMissError):
        return DEMO_MISS
    return _short(exc)


def _index_for(deps: PipelineDeps, sandbox: str | None, employee_id: str) -> DuplicateIndex:
    """The duplicate index as this employee in this sandbox should see it."""
    if isinstance(deps.index, DbDuplicateIndex):
        only_theirs = employee_id if deps.settings.duplicate_scope == "employee" else None
        return ScopedIndex(deps.index, sandbox=sandbox, employee_id=only_theirs)
    return deps.index


async def process_batch(deps: PipelineDeps, batch_id: str) -> None:
    """Run the whole pipeline for one batch; never raises (failures become events and status)."""
    batch = await deps.repo.get_batch(batch_id, sandbox=UNSCOPED)  # the pipeline serves every world
    if batch is None or batch.status in ("done", "failed"):
        return  # unknown, or a retried job for work that already finished
    sandbox = await deps.repo.batch_sandbox(batch_id)
    try:
        # Everything below (and every LLM call it makes) runs inside the batch's own world.
        with bound_ids(sandbox=sandbox, batch_id=batch_id):
            await _run(deps, batch, sandbox)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # one bad batch must not take the worker down
        logger.exception("batch %s failed", batch_id)
        try:
            await deps.repo.mark_batch(batch_id, "failed", error=_short(exc))
        except Exception:  # the rows may be gone (a visitor pressed Start over mid-batch)
            logger.warning("could not mark batch %s failed", batch_id)
        # Whatever happened, the stream gets an ending: a client must never wait on it for minutes.
        await deps.events.publish(ev.BatchFailed(batch_id=batch_id, error=_short(exc)))


async def _run(deps: PipelineDeps, batch: BatchView, sandbox: str | None) -> None:
    employee = await deps.directory.get(batch.employee_id)
    if employee is None:
        raise LookupError(f"unknown employee {batch.employee_id}")
    await deps.repo.mark_batch(batch.id, "processing")
    rows = await deps.repo.batch_documents(batch.id)
    await deps.events.publish(ev.BatchStarted(batch_id=batch.id, total=len(rows)))

    reads, read_failures = await _read_all(
        deps, batch.id, [r for r in rows if r.status == "queued"]
    )
    checked, check_failures = await _check_all(deps, batch.id, reads, sandbox)

    earlier = [
        ProcessedDocument.model_validate(r.data) for r in rows if r.status == "processed" and r.data
    ]
    position = {r.id: r.position for r in rows}
    documents = sorted([*earlier, *checked], key=lambda d: position[d.id])
    claims = await _form_claims(deps, batch.id, employee, documents, sandbox)

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
    rereads = RereadBudget(len(rows))

    async def one(row: Document) -> _Read | None:
        async with gate:
            try:
                read = await _read_one(deps, row, rereads)
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


async def _read_one(deps: PipelineDeps, row: Document, rereads: RereadBudget) -> _Read:
    raw = await deps.storage.get(row.storage_key)
    prepared = await asyncio.to_thread(
        prepare_document, raw, max_pdf_pages=deps.settings.max_pdf_pages
    )
    extraction = await deps.extractor.extract(prepared, may_reread=rereads.take)
    notes = await _calendar_notes(deps, row.employee_id, extraction.receipt)
    # Deciding and locating both need only the extracted receipt, so they run together.
    (decisions, result), located = await asyncio.gather(
        decide_document(deps.decisions, extraction.receipt, calendar=notes),
        _locate(deps, prepared, extraction.receipt),
    )
    return _Read(
        row=row,
        raw=raw,
        prepared=prepared,
        receipt=extraction.receipt,
        decisions=decisions,
        boxes=located.boxes,
        cost_usd=extraction.cost_usd + result.cost_usd + located.cost_usd,
    )


async def _locate(
    deps: PipelineDeps, prepared: PreparedDocument, receipt: ExtractedReceipt
) -> Located:
    if deps.locator is None:
        return Located()
    return await deps.locator.locate(prepared, receipt)


async def _calendar_notes(
    deps: PipelineDeps, employee_id: str, receipt: ExtractedReceipt
) -> list[str] | None:
    """What the employee's calendar shows on the receipt's date, as words without names.

    System One needs it to tell hosting clients from an ordinary meal. Best effort: ``None`` when
    the receipt has no readable date or the calendar cannot be reached (the question is then asked
    without it, never failed).
    """
    try:
        day = date.fromisoformat(receipt.date) if receipt.date else None
    except ValueError:
        day = None
    if day is None:
        return None
    try:
        events = await deps.calendar.events(employee_id, day, day)
    except Exception as exc:  # the calendar is a convenience
        logger.warning("calendar unavailable for %s: %s", employee_id, _short(exc))
        return None
    notes = []
    for event in events:
        guests = f" with {len(event.attendees)} guests" if event.attendees else ""
        notes.append(f"{event.kind.replace('_', ' ')}{guests}")
    return notes


async def _fail(deps: PipelineDeps, batch_id: str, row: Document, exc: BaseException) -> None:
    logger.warning("document %s failed: %s", row.id, _short(exc))
    reason = _reason(deps, exc)
    await deps.repo.fail_document(row.id, reason)
    await deps.events.publish(
        ev.DocumentFailed(
            batch_id=batch_id, document_id=row.id, filename=row.filename, error=reason
        )
    )


# --- phase 2: trust + persist ---------------------------------------------------------------


async def _check_all(
    deps: PipelineDeps, batch_id: str, reads: Sequence[_Read], sandbox: str | None
) -> tuple[list[ProcessedDocument], int]:
    if not reads:
        return [], 0
    # one employee's checks take turns (in their own sandbox: visitors never wait for each other)
    async with deps.lock_for(reads[0].row.employee_id, sandbox):
        return await _check_in_turn(deps, batch_id, reads, sandbox)


async def _check_in_turn(
    deps: PipelineDeps, batch_id: str, reads: Sequence[_Read], sandbox: str | None
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
                index=_index_for(deps, sandbox, row.employee_id),
            )
            processed = ProcessedDocument(
                id=row.id,
                filename=row.filename,
                sha256=row.sha256,
                receipt=read.receipt,
                decisions=read.decisions,
                findings=report.findings,
                boxes=read.boxes,
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
    deps: PipelineDeps,
    batch_id: str,
    employee: Employee,
    documents: Sequence[ProcessedDocument],
    sandbox: str | None,
) -> list[Claim]:
    if not documents:
        return []
    by_id = {d.id: d for d in documents}
    claims = build_claims(employee, documents, deps.policy, today=deps.today())
    finished: list[Claim] = []
    for claim in claims:
        members = [by_id[i] for i in claim.document_ids]
        claim = await _apply_calendar(deps, employee, claim, members)
        # Answers change what policy sees (the per-head cap needs the attendees the calendar just
        # supplied), so the findings are recomputed before the claim is routed.
        claim = refinalize(
            claim,
            members,
            deps.policy,
            employee,
            today=deps.today(),
            min_confidence=deps.settings.decision_min_confidence,
        )
        finished.append(claim)
    routes = {c.id: route(c) for c in finished}
    await deps.repo.save_claims(batch_id, employee.id, finished, routes, sandbox=sandbox)
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
