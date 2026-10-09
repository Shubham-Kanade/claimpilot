"""Persistence operations for batches, documents, claims and the audit trail.

The only module that talks SQL for the pipeline. Domain objects go in and out as Pydantic
models; JSON columns hold them, and the columns we filter by are mirrored alongside.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import delete, func, select, true

from claimpilot.db import AuditEvent, Batch, ClaimRow, Document, LlmCall, SessionFactory
from claimpilot.domain.claims import Claim, ProcessedDocument
from claimpilot.pipeline.views import BatchView, ClaimView, DocumentView, to_claim


@dataclass(frozen=True, slots=True)
class NewFile:
    filename: str
    sha256: str
    storage_key: str


@dataclass(frozen=True, slots=True)
class Deleted:
    """What ``Repository.delete_data`` removed; the caller deletes the stored files."""

    batches: int
    documents: int
    claims: int
    storage_keys: list[str]


class _Unscoped:
    """Marker for "every sandbox": only the pipeline's own lookups may ask for that."""

    def __repr__(self) -> str:
        return "UNSCOPED"


UNSCOPED: Final = _Unscoped()
Scope = str | None | _Unscoped
"""Which demo sandbox a query is about. ``None`` is the shared, sandbox-less world (everything
outside the public demo): it means ``IS NULL``, never "no filter". A visitor's rows carry their
sandbox id and are invisible to every other scope. Leaving the argument out therefore fails
closed (sandboxed data is not found), never open."""


def in_sandbox(column: Any, sandbox: Scope) -> Any:
    """The SQL condition for "this row belongs to ``sandbox``"."""
    if isinstance(sandbox, _Unscoped):
        return true()
    return column.is_(None) if sandbox is None else column == sandbox


def _same_sandbox(row_sandbox: str | None, sandbox: Scope) -> bool:
    return isinstance(sandbox, _Unscoped) or row_sandbox == sandbox


def new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class Repository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    # --- batches & documents ---------------------------------------------------------------
    async def create_batch(
        self,
        employee_id: str,
        files: Sequence[NewFile],
        *,
        batch_id: str | None = None,
        sandbox: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[str, list[str]]:
        """Insert a queued batch with its documents; returns ``(batch_id, document_ids)``."""
        batch_id = batch_id or new_id()
        doc_ids = [new_id() for _ in files]
        async with self._sessions() as session:
            session.add(
                Batch(
                    id=batch_id,
                    employee_id=employee_id,
                    status="queued",
                    total=len(files),
                    sandbox=sandbox,
                    trace_id=trace_id,
                )
            )
            await session.flush()  # the batch row must exist before its documents (FK)
            for position, (doc_id, file) in enumerate(zip(doc_ids, files, strict=True)):
                session.add(
                    Document(
                        id=doc_id,
                        batch_id=batch_id,
                        employee_id=employee_id,
                        filename=file.filename,
                        sha256=file.sha256,
                        storage_key=file.storage_key,
                        position=position,
                        status="queued",
                        sandbox=sandbox,
                    )
                )
            await session.commit()
        return batch_id, doc_ids

    async def get_batch(self, batch_id: str, *, sandbox: Scope = None) -> BatchView | None:
        async with self._sessions() as session:
            batch = await session.get(Batch, batch_id)
            if batch is None or not _same_sandbox(batch.sandbox, sandbox):
                return None
            docs = (
                await session.scalars(
                    select(Document)
                    .where(Document.batch_id == batch_id)
                    .order_by(Document.position)
                )
            ).all()
            claims = (
                await session.scalars(
                    select(ClaimRow).where(ClaimRow.batch_id == batch_id).order_by(ClaimRow.id)
                )
            ).all()
            return BatchView(
                id=batch.id,
                employee_id=batch.employee_id,
                status=batch.status,
                total=batch.total,
                processed=batch.processed,
                failed=batch.failed,
                created_at=batch.created_at,
                finished_at=batch.finished_at,
                error=batch.error,
                documents=[document_view(d) for d in docs],
                claims=[claim_view(c) for c in claims],
            )

    async def batch_sandbox(self, batch_id: str) -> str | None:
        """The sandbox a batch belongs to (the pipeline runs a batch inside its own world)."""
        async with self._sessions() as session:
            return await session.scalar(select(Batch.sandbox).where(Batch.id == batch_id))

    async def batch_trace_id(self, batch_id: str) -> str | None:
        """The id of the upload request that made this batch (ties its work to that request)."""
        async with self._sessions() as session:
            return await session.scalar(select(Batch.trace_id).where(Batch.id == batch_id))

    async def batch_documents(self, batch_id: str) -> list[Document]:
        async with self._sessions() as session:
            result = await session.scalars(
                select(Document).where(Document.batch_id == batch_id).order_by(Document.position)
            )
            return list(result.all())

    async def mark_batch(
        self,
        batch_id: str,
        status: str,
        *,
        processed: int | None = None,
        failed: int | None = None,
        error: str | None = None,
    ) -> None:
        async with self._sessions() as session:
            batch = await session.get(Batch, batch_id)
            if batch is None:
                raise KeyError(batch_id)
            batch.status = status
            if processed is not None:
                batch.processed = processed
            if failed is not None:
                batch.failed = failed
            if error is not None:
                batch.error = error[:500]
            if status in ("done", "failed"):
                batch.finished_at = _now()
            await session.commit()

    async def save_document(
        self,
        document_id: str,
        processed: ProcessedDocument,
        *,
        phash: str | None,
        fingerprint: str | None,
        trust: dict[str, Any] | None,
    ) -> None:
        async with self._sessions() as session:
            row = await session.get(Document, document_id)
            if row is None:
                raise KeyError(document_id)
            row.status = "processed"
            row.data = processed.model_dump(mode="json")
            row.phash, row.fingerprint, row.trust = phash, fingerprint, trust
            await session.commit()

    async def fail_document(self, document_id: str, error: str) -> None:
        async with self._sessions() as session:
            row = await session.get(Document, document_id)
            if row is None:
                raise KeyError(document_id)
            row.status, row.error = "failed", error[:500]
            await session.commit()

    async def get_document(self, document_id: str, *, sandbox: Scope = None) -> Document | None:
        async with self._sessions() as session:
            row = await session.get(Document, document_id)
            return row if row is not None and _same_sandbox(row.sandbox, sandbox) else None

    async def processed_documents(self, ids: Sequence[str]) -> list[ProcessedDocument]:
        """The processed documents with these ids, in the order given."""
        if not ids:
            return []
        async with self._sessions() as session:
            rows = (await session.scalars(select(Document).where(Document.id.in_(ids)))).all()
        by_id = {r.id: ProcessedDocument.model_validate(r.data) for r in rows if r.data}
        return [by_id[i] for i in ids if i in by_id]

    async def seen_documents(self, *, exclude_batch: str | None = None) -> list[Document]:
        """Processed documents with an image hash (the duplicate index scans these)."""
        query = select(Document).where(Document.status == "processed")
        if exclude_batch is not None:
            query = query.where(Document.batch_id != exclude_batch)
        async with self._sessions() as session:
            return list((await session.scalars(query)).all())

    # --- claims ----------------------------------------------------------------------------
    async def save_claims(
        self,
        batch_id: str,
        employee_id: str,
        claims: Sequence[Claim],
        routes: dict[str, str],
        *,
        sandbox: str | None = None,
    ) -> None:
        async with self._sessions() as session:
            for claim in claims:
                await session.merge(  # upsert: a retried batch rebuilds the same ids
                    ClaimRow(
                        id=claim.id,
                        batch_id=batch_id,
                        employee_id=employee_id,
                        status=claim.status.value,
                        route=routes.get(claim.id),
                        data=claim.model_dump(mode="json"),
                        sandbox=sandbox,
                    )
                )
            await session.commit()

    async def get_claim(self, claim_id: str, *, sandbox: Scope = None) -> ClaimView | None:
        async with self._sessions() as session:
            row = await session.get(ClaimRow, claim_id)
            return claim_view(row) if row and _same_sandbox(row.sandbox, sandbox) else None

    async def list_claims(
        self,
        *,
        employee_id: str | None = None,
        status: str | None = None,
        route: str | None = None,
        sandbox: Scope = None,
    ) -> list[ClaimView]:
        query = (
            select(ClaimRow)
            .where(in_sandbox(ClaimRow.sandbox, sandbox))
            .order_by(ClaimRow.created_at.desc(), ClaimRow.id)
        )
        if employee_id:
            query = query.where(ClaimRow.employee_id == employee_id)
        if status:
            query = query.where(ClaimRow.status == status)
        if route:
            query = query.where(ClaimRow.route == route)
        async with self._sessions() as session:
            return [claim_view(r) for r in (await session.scalars(query)).all()]

    async def update_claim(
        self,
        claim: Claim,
        *,
        route: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        claim = to_claim(claim)  # never persist the view-only batch_id / route
        async with self._sessions() as session:
            row = await session.get(ClaimRow, claim.id)
            if row is None:
                raise KeyError(claim.id)
            row.data = claim.model_dump(mode="json")
            row.status = claim.status.value
            row.submission_reference = claim.submission_reference
            if route is not None:
                row.route = route
            if idempotency_key is not None:
                row.idempotency_key = idempotency_key
            await session.commit()

    async def claim_for_idempotency_key(self, key: str) -> ClaimView | None:
        async with self._sessions() as session:
            row = await session.scalar(select(ClaimRow).where(ClaimRow.idempotency_key == key))
            return claim_view(row) if row else None

    # --- audit -----------------------------------------------------------------------------
    async def audit(
        self,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        async with self._sessions() as session:
            session.add(
                AuditEvent(
                    actor=actor,
                    action=action,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    detail=detail or {},
                )
            )
            await session.commit()

    async def delete_data(
        self, employee_id: str | None = None, *, sandbox: Scope = None
    ) -> Deleted:
        """Delete uploads, documents and claims of one employee (or of everyone) in one sandbox.

        This is "start over": a visitor's reset reaches only their own sandbox.

        The audit trail of what was deleted goes with it; the LLM cost ledger stays, because the
        money was spent either way.
        """
        tables: dict[str, Any] = {"batch": Batch, "document": Document, "claim": ClaimRow}
        async with self._sessions() as session:

            def owned(table: Any) -> Any:
                here = in_sandbox(table.sandbox, sandbox)
                return here if employee_id is None else here & (table.employee_id == employee_id)

            keys = (
                await session.scalars(select(Document.storage_key).where(owned(Document)))
            ).all()
            entity_ids: list[str] = []
            for table in tables.values():
                entity_ids += (await session.scalars(select(table.id).where(owned(table)))).all()
            counts = {}
            for name in (
                "claim",
                "document",
                "batch",
            ):  # children first: documents point at batches
                result = await session.execute(delete(tables[name]).where(owned(tables[name])))
                counts[name] = result.rowcount  # type: ignore[attr-defined]
            if entity_ids:
                await session.execute(
                    delete(AuditEvent).where(AuditEvent.entity_id.in_(entity_ids))
                )
            await session.commit()
        return Deleted(
            batches=counts["batch"],
            documents=counts["document"],
            claims=counts["claim"],
            storage_keys=list(keys),
        )

    async def audit_trail(self, entity_id: str) -> list[AuditEvent]:
        async with self._sessions() as session:
            result = await session.scalars(
                select(AuditEvent).where(AuditEvent.entity_id == entity_id).order_by(AuditEvent.ts)
            )
            return list(result.all())


def document_view(row: Document) -> DocumentView:
    processed = ProcessedDocument.model_validate(row.data) if row.data else None
    trust = row.trust or {}
    return DocumentView(
        id=row.id,
        filename=row.filename,
        position=row.position,
        status=row.status,
        error=row.error,
        document=processed,
        trust_score=trust.get("score"),
        verdict=trust.get("verdict"),
    )


def claim_view(row: ClaimRow) -> ClaimView:
    claim = Claim.model_validate(row.data)
    return ClaimView(**claim.model_dump(), batch_id=row.batch_id, route=row.route)


ASSUMED_MANUAL_MINUTES_PER_DOCUMENT = 4.0  # collect, type in, categorise and check one receipt


async def collect_stats(sessions: SessionFactory, *, sandbox: Scope = None) -> dict[str, Any]:
    """Aggregates behind the impact meter (documents, claims, routing, LLM spend, time saved).

    Counted per sandbox: a demo visitor sees the numbers of their own session, not of everyone's.
    """
    async with sessions() as session:
        docs = dict(
            (
                await session.execute(
                    select(Document.status, func.count())
                    .where(in_sandbox(Document.sandbox, sandbox))
                    .group_by(Document.status)
                )
            ).all()
        )
        claims = dict(
            (
                await session.execute(
                    select(ClaimRow.status, func.count())
                    .where(in_sandbox(ClaimRow.sandbox, sandbox))
                    .group_by(ClaimRow.status)
                )
            ).all()
        )
        auto = (
            await session.scalar(
                select(func.count()).where(
                    ClaimRow.route == "auto_approve", in_sandbox(ClaimRow.sandbox, sandbox)
                )
            )
            or 0
        )
        finished = (
            await session.execute(
                select(Batch.created_at, Batch.finished_at).where(
                    Batch.status == "done",
                    Batch.finished_at.is_not(None),
                    in_sandbox(Batch.sandbox, sandbox),
                )
            )
        ).all()
        # The cost of reading the receipts that are still here: calls since the oldest remaining
        # batch. (After "Start over" the documents are gone but the ledger is not, and counting its
        # old rows against the few receipts left would inflate the cost per receipt.)
        since = await session.scalar(
            select(func.min(Batch.created_at)).where(in_sandbox(Batch.sandbox, sandbox))
        )
        costs = select(func.count(), func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(
            LlmCall.error.is_(None), in_sandbox(LlmCall.sandbox, sandbox)
        )
        if since is None:
            llm_calls, llm_cost = 0, 0.0
        else:
            llm_calls, llm_cost = (
                await session.execute(costs.where(LlmCall.created_at >= since))
            ).one()
    seconds = [(done - created).total_seconds() for created, done in finished if done is not None]
    processed = docs.get("processed", 0)
    automated_minutes = sum(seconds) / 60
    return {
        "documents_processed": processed,
        "documents_failed": docs.get("failed", 0),
        "claims": sum(claims.values()),
        "claims_by_status": claims,
        "auto_approvable_claims": auto,
        "llm_calls": llm_calls,
        "llm_cost_usd": float(llm_cost),
        "llm_cost_per_document_usd": float(llm_cost) / processed if processed else None,
        "avg_batch_seconds": sum(seconds) / len(seconds) if seconds else None,
        "assumed_manual_minutes_per_document": ASSUMED_MANUAL_MINUTES_PER_DOCUMENT,
        "estimated_minutes_saved": max(
            0.0, processed * ASSUMED_MANUAL_MINUTES_PER_DOCUMENT - automated_minutes
        ),
    }
