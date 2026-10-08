"""Persistence operations for batches, documents, claims and the audit trail.

The only module that talks SQL for the pipeline. Domain objects go in and out as Pydantic
models; JSON columns hold them, and the columns we filter by are mirrored alongside.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from claimpilot.db import AuditEvent, Batch, ClaimRow, Document, SessionFactory
from claimpilot.domain.claims import Claim, ProcessedDocument
from claimpilot.pipeline.views import BatchView, ClaimView, DocumentView, to_claim


@dataclass(frozen=True, slots=True)
class NewFile:
    filename: str
    sha256: str
    storage_key: str


def new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class Repository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    # --- batches & documents ---------------------------------------------------------------
    async def create_batch(
        self, employee_id: str, files: Sequence[NewFile], *, batch_id: str | None = None
    ) -> tuple[str, list[str]]:
        """Insert a queued batch with its documents; returns ``(batch_id, document_ids)``."""
        batch_id = batch_id or new_id()
        doc_ids = [new_id() for _ in files]
        async with self._sessions() as session:
            session.add(
                Batch(id=batch_id, employee_id=employee_id, status="queued", total=len(files))
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
                    )
                )
            await session.commit()
        return batch_id, doc_ids

    async def get_batch(self, batch_id: str) -> BatchView | None:
        async with self._sessions() as session:
            batch = await session.get(Batch, batch_id)
            if batch is None:
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

    async def get_document(self, document_id: str) -> Document | None:
        async with self._sessions() as session:
            return await session.get(Document, document_id)

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
    ) -> None:
        async with self._sessions() as session:
            for claim in claims:
                session.add(
                    ClaimRow(
                        id=claim.id,
                        batch_id=batch_id,
                        employee_id=employee_id,
                        status=claim.status.value,
                        route=routes.get(claim.id),
                        data=claim.model_dump(mode="json"),
                    )
                )
            await session.commit()

    async def get_claim(self, claim_id: str) -> ClaimView | None:
        async with self._sessions() as session:
            row = await session.get(ClaimRow, claim_id)
            return claim_view(row) if row else None

    async def list_claims(
        self,
        *,
        employee_id: str | None = None,
        status: str | None = None,
        route: str | None = None,
    ) -> list[ClaimView]:
        query = select(ClaimRow).order_by(ClaimRow.created_at.desc(), ClaimRow.id)
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
