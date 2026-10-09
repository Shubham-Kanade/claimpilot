"""Upload a pile of receipts, watch it being processed."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import PurePath
from typing import Annotated, Any

from fastapi import APIRouter, File, Header, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from claimpilot.api.deps import ContainerDep, PersonaDep
from claimpilot.api.schemas import BatchCreated, DocumentRef
from claimpilot.extraction.preprocess import UnsupportedDocumentError, sniff_media_type
from claimpilot.pipeline.events import PipelineEvent, stream_events
from claimpilot.pipeline.repo import NewFile, new_id
from claimpilot.pipeline.views import BatchView
from claimpilot.problem import Problem, problem
from claimpilot.telemetry import current_ids

router = APIRouter(prefix="/v1/batches", tags=["batches"])

EXTENSION = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}
HEARTBEAT_S = 15.0
MAX_EVENT_ID_DIGITS = 9


def _resume_point(last_event_id: str | None) -> int:
    """Where to continue a stream: after the id the client last saw, else from the beginning.

    ``str.isdigit`` also accepts characters like a superscript two that ``int`` rejects, and an id
    thousands of digits long is not an id; both would otherwise be a server error.
    """
    if last_event_id is None:
        return 0
    plain = last_event_id.isascii() and last_event_id.isdigit()
    return int(last_event_id) + 1 if plain and len(last_event_id) <= MAX_EVENT_ID_DIGITS else 0


def _safe_name(raw: str | None, index: int) -> str:
    name = PurePath((raw or "").replace("\\", "/")).name.strip()
    return (name or f"receipt-{index + 1}")[:255]


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BatchCreated,
    responses={
        413: {"model": Problem, "description": "A file or the whole upload is too large"},
        422: {"model": Problem, "description": "Nothing to process, or an unsupported file"},
    },
    summary="Upload receipts and start processing",
)
async def create_batch(
    container: ContainerDep,
    persona: PersonaDep,
    files: Annotated[list[UploadFile], File(description="Photos, PDFs or screenshots")],
) -> BatchCreated:
    settings = container.settings
    if not files:
        raise problem(status.HTTP_422_UNPROCESSABLE_CONTENT, "no_files", "Upload at least one file")
    if len(files) > settings.max_batch_files:
        raise problem(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "too_many_files",
            f"At most {settings.max_batch_files} files per upload",
        )

    limit = settings.max_upload_mb * 1024 * 1024
    batch_id = new_id()
    stored: list[tuple[str, bytes]] = []  # (storage key, bytes)
    new_files: list[NewFile] = []
    rejected: list[dict[str, str]] = []
    for index, upload in enumerate(files):
        name = _safe_name(upload.filename, index)
        data = await upload.read(limit + 1)
        if len(data) > limit:
            raise problem(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "file_too_large",
                f"{name} is larger than {settings.max_upload_mb} MB",
            )
        try:
            media_type = sniff_media_type(data)  # magic bytes; the declared type is never trusted
        except UnsupportedDocumentError as exc:
            rejected.append({"filename": name, "reason": str(exc)})
            continue
        digest = hashlib.sha256(data).hexdigest()
        key = f"{batch_id}/{index:02d}-{digest[:12]}.{EXTENSION[media_type]}"
        stored.append((key, data))
        new_files.append(NewFile(filename=name, sha256=digest, storage_key=key))

    if rejected:
        raise problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "unsupported_files",
            "Some files are not JPEG, PNG, WebP or PDF",
            {"files": rejected},
        )

    for key, data in stored:
        await container.storage.put(key, data)
    _, doc_ids = await container.repo.create_batch(
        persona.id,
        new_files,
        batch_id=batch_id,
        sandbox=persona.sandbox,
        trace_id=current_ids()["trace_id"],  # the request's id: the batch's work will carry it
    )
    await container.repo.audit(
        persona.id, "batch_uploaded", "batch", batch_id, {"files": len(new_files)}
    )
    await container.enqueue(batch_id)
    return BatchCreated(
        batch_id=batch_id,
        status="queued",
        documents=[
            DocumentRef(id=d, filename=f.filename) for d, f in zip(doc_ids, new_files, strict=True)
        ],
        events_url=f"/v1/batches/{batch_id}/events",
    )


async def _visible_batch(container: ContainerDep, persona: PersonaDep, batch_id: str) -> BatchView:
    batch = await container.repo.get_batch(batch_id, sandbox=persona.sandbox)
    if batch is None or not persona.can_see(batch.employee_id):
        raise problem(status.HTTP_404_NOT_FOUND, "batch_not_found", "No such batch")
    return batch


@router.get(
    "/{batch_id}",
    response_model=BatchView,
    responses={404: {"model": Problem}},
    summary="Batch status with its documents and claims",
)
async def get_batch(batch_id: str, container: ContainerDep, persona: PersonaDep) -> BatchView:
    return await _visible_batch(container, persona, batch_id)


@router.get(
    "/{batch_id}/history",
    response_model=list[PipelineEvent],
    responses={404: {"model": Problem}},
    summary="Progress events so far (the same events as the SSE stream, as JSON)",
)
async def batch_history(
    batch_id: str,
    container: ContainerDep,
    persona: PersonaDep,
    after: Annotated[int, Query(ge=0, description="Skip this many events")] = 0,
) -> Any:
    await _visible_batch(container, persona, batch_id)
    return await container.events.read(batch_id, after)


@router.get(
    "/{batch_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Server-sent events; each `data` is a PipelineEvent (see /history)",
        },
        404: {"model": Problem},
    },
    summary="Live progress (server-sent events)",
)
async def batch_events(
    batch_id: str,
    container: ContainerDep,
    persona: PersonaDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    await _visible_batch(container, persona, batch_id)
    start = _resume_point(last_event_id)

    async def frames() -> AsyncIterator[str]:
        async for item in stream_events(
            container.events, batch_id, start=start, heartbeat_s=HEARTBEAT_S
        ):
            if item is None:
                yield ": keepalive\n\n"
                continue
            seq, event = item
            yield f"id: {seq}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
