"""Who and what a piece of work belongs to, carried alongside it (not through every signature).

The pipeline and the HTTP layer bind ids here (the demo sandbox, the batch being processed); the
cost ledger reads them when it records an LLM call, so every call can be traced back to the batch
it was made for without the LLM layer knowing anything about batches. Built on structlog's
context variables, which asyncio tasks (``gather``) and ``to_thread`` copy when they are created.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import structlog.contextvars

IDS = ("sandbox", "batch_id")  # the context keys the ledger understands


@contextmanager
def bound_ids(**ids: str | None) -> Iterator[None]:
    """Bind ids for the duration of the block (``None`` values are skipped, not bound)."""
    with structlog.contextvars.bound_contextvars(**{k: v for k, v in ids.items() if v is not None}):
        yield


def current_ids() -> dict[str, str | None]:
    """The bound ids the ledger stores; anything not bound is ``None``."""
    bound = structlog.contextvars.get_contextvars()
    return {name: bound.get(name) for name in IDS}
