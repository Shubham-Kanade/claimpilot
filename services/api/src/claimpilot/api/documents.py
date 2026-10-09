"""Documents: what we read from one receipt, and the original file (for click-to-verify)."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from claimpilot.api.deps import ContainerDep, PersonaDep
from claimpilot.db import Document
from claimpilot.extraction.preprocess import UnsupportedDocumentError, sniff_media_type
from claimpilot.pipeline.repo import document_view
from claimpilot.pipeline.views import DocumentView
from claimpilot.problem import Problem, problem
from claimpilot.storage import StorageError

router = APIRouter(prefix="/v1/documents", tags=["documents"])


async def _visible_document(
    container: ContainerDep, persona: PersonaDep, document_id: str
) -> Document:
    row = await container.repo.get_document(document_id, sandbox=persona.sandbox)
    if row is None or not persona.can_see(row.employee_id):
        raise problem(status.HTTP_404_NOT_FOUND, "document_not_found", "No such document")
    return row


@router.get(
    "/{document_id}",
    response_model=DocumentView,
    responses={404: {"model": Problem}},
    summary="One document: extracted fields, decisions, findings",
)
async def get_document(
    document_id: str, container: ContainerDep, persona: PersonaDep
) -> DocumentView:
    return document_view(await _visible_document(container, persona, document_id))


@router.get(
    "/{document_id}/file",
    response_class=Response,
    responses={
        200: {
            "content": {"image/jpeg": {}, "image/png": {}, "image/webp": {}, "application/pdf": {}}
        },
        404: {"model": Problem},
    },
    summary="The original uploaded file",
)
async def get_document_file(
    document_id: str, container: ContainerDep, persona: PersonaDep
) -> Response:
    row = await _visible_document(container, persona, document_id)
    try:
        data = await container.storage.get(row.storage_key)
        media_type = sniff_media_type(data)
    except (StorageError, UnsupportedDocumentError) as exc:
        raise problem(
            status.HTTP_404_NOT_FOUND, "file_unavailable", "The file is no longer available"
        ) from exc
    return Response(
        data,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )
