"""API test harness: the real app and database (sqlite via Alembic) with in-memory fakes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

from claimpilot.config import API_ROOT, Settings
from claimpilot.container import Container
from claimpilot.db import create_engine, create_session_factory
from claimpilot.domain import Claim, ClaimMode
from claimpilot.domain.claims import Employee, OpenQuestion, QuestionKind
from claimpilot.main import create_app
from claimpilot.pipeline.events import InMemoryEventBus
from claimpilot.pipeline.repo import Repository
from claimpilot.ports import FakeFinance, StaticCalendar, StaticDirectory
from claimpilot.storage import InMemoryStorage

ASHA = Employee(id="P001", name="Asha Menon", employee_id="EMP1", grade="L3", base_city="Pune")
MEERA = Employee(id="P002", name="Meera Shah", employee_id="EMP2", grade="L2", base_city="Mumbai")
RAVI = Employee(id="DEMO-RAVI", name="Ravi Iyer", employee_id="EMP9", grade="L5", base_city="Pune")

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"\x00" * 64


def migrate(url: str) -> None:
    config = Config(API_ROOT / "alembic.ini")
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


@dataclass
class World:
    container: Container
    finance: FakeFinance
    enqueued: list[str] = field(default_factory=list)

    @property
    def repo(self) -> Repository:
        return self.container.repo


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}"
    await asyncio.to_thread(migrate, url)
    settings = Settings(database_url=url, max_upload_mb=1, max_batch_files=3)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    finance = FakeFinance()
    enqueued: list[str] = []

    async def enqueue(batch_id: str) -> None:
        enqueued.append(batch_id)

    container = Container(
        settings=settings,
        sessions=sessions,
        repo=Repository(sessions),
        storage=InMemoryStorage(),
        events=InMemoryEventBus(),
        enqueue=enqueue,
        directory=StaticDirectory([ASHA, MEERA, RAVI]),
        finance=finance,
        calendar=StaticCalendar(),
    )
    yield World(container=container, finance=finance, enqueued=enqueued)
    await engine.dispose()


@pytest.fixture
async def http(world: World) -> AsyncIterator[AsyncClient]:
    app = create_app(world.container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def as_persona(persona: Employee) -> dict[str, str]:
    return {"X-Persona": persona.id}


def make_claim(
    claim_id: str = "clm-1", *, employee_id: str = "P001", answered: bool = False
) -> Claim:
    answer = "Orion Retail: A. Rao, S. Nair; quarterly review" if answered else None
    return Claim(
        id=claim_id,
        employee_id=employee_id,
        title="Client dinner 14 Aug 2026",
        mode=ClaimMode.event,
        document_ids=["d1"],
        total=4102.0,
        start_date=date(2026, 8, 14),
        end_date=date(2026, 8, 14),
        city="Pune",
        open_questions=[
            OpenQuestion(
                id="q-attendees",
                kind=QuestionKind.attendees,
                text="Who attended the client dinner?",
                answer=answer,
            )
        ],
    )


async def seed_claim(world: World, claim: Claim, route: str = "finance_review") -> str:
    """Create a batch for the claim's owner and store the (state-refreshed) claim."""
    from claimpilot.claims import refresh_status
    from claimpilot.pipeline.repo import NewFile

    batch_id, _ = await world.repo.create_batch(
        claim.employee_id, [NewFile("a.png", "a" * 64, "k/a.png")]
    )
    await world.repo.save_claims(
        batch_id, claim.employee_id, [refresh_status(claim)], {claim.id: route}
    )
    return batch_id
