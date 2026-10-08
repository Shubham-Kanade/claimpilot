"""The dependency container shared by the API process and the worker.

One place that holds the wired-up collaborators, so request handlers and pipeline jobs depend on
small protocols (storage, event bus, finance, directory ...) and tests can swap any of them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from claimpilot.config import Settings
from claimpilot.db import SessionFactory
from claimpilot.llm.client import LLMClient
from claimpilot.pipeline.events import EventBus
from claimpilot.pipeline.repo import Repository
from claimpilot.policy import Policy
from claimpilot.ports import CalendarSource, EmployeeDirectory, FinanceSystem
from claimpilot.storage import Storage

Enqueue = Callable[[str], Awaitable[None]]
Closer = Callable[[], Awaitable[None]]


@dataclass
class Container:
    settings: Settings
    sessions: SessionFactory
    repo: Repository
    storage: Storage
    events: EventBus
    enqueue: Enqueue  # hand a batch id to the worker
    directory: EmployeeDirectory
    finance: FinanceSystem
    calendar: CalendarSource
    # Only the chat reply needs the LLM; without it the assistant asks one question at a time.
    llm: LLMClient | None = None
    policy: Policy = field(default_factory=Policy.load)
    closers: list[Closer] = field(default_factory=list)

    @property
    def approver_ids(self) -> frozenset[str]:
        return frozenset(self.settings.approver_ids)

    async def aclose(self) -> None:
        for close in reversed(self.closers):
            await close()
