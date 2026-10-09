"""Who and what a piece of work belongs to, carried alongside it (not through every signature).

The HTTP layer and the pipeline bind ids here (the request's trace, the demo sandbox, the batch,
the document, the claim); the cost ledger and the log lines read them, so every LLM call and every
log line can be traced back to the upload that caused it without the layers in between knowing
anything about batches. Built on structlog's context variables, which asyncio tasks (``gather``)
and ``to_thread`` copy when they are created.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import structlog.contextvars

IDS = ("sandbox", "batch_id", "trace_id", "document_id", "claim_id")  # what the ledger stores


@contextmanager
def bound_ids(**ids: str | None) -> Iterator[None]:
    """Bind ids for the duration of the block (``None`` values are skipped, not bound)."""
    with structlog.contextvars.bound_contextvars(**{k: v for k, v in ids.items() if v is not None}):
        yield


def current_ids() -> dict[str, str | None]:
    """The bound ids the ledger stores; anything not bound is ``None``."""
    bound = structlog.contextvars.get_contextvars()
    return {name: bound.get(name) for name in IDS}


def reset_ids() -> None:
    """Forget everything bound so far: a background job starts from its own row, not from whatever
    request happened to start it (an embedded batch task inherits the upload request's context)."""
    structlog.contextvars.clear_contextvars()
