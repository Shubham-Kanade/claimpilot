"""Production wiring and process lifecycles, with Redis/Arq/MCP replaced by recording fakes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from claimpilot import wiring, worker
from claimpilot.config import Settings
from claimpilot.container import Container
from claimpilot.decisions import LLMEngine
from claimpilot.domain.claims import Employee
from claimpilot.extraction.cache import RedisExtractionCache
from claimpilot.main import create_app
from claimpilot.pipeline.dupindex import DbDuplicateIndex
from claimpilot.pipeline.events import RedisEventBus
from claimpilot.ports import FakeFinance, StaticCalendar, StaticDirectory
from claimpilot.storage import LocalStorage

ASHA = Employee(id="P001", name="Asha", employee_id="E1", grade="L3", base_city="Pune")


class FakeRedis:
    closed = False

    @classmethod
    def from_url(cls, url: str) -> FakeRedis:
        cls.url = url
        return cls()

    async def aclose(self) -> None:
        FakeRedis.closed = True


class FakePool:
    def __init__(self) -> None:
        self.jobs: list[tuple[Any, ...]] = []
        self.closed = False

    async def enqueue_job(self, *args: Any) -> None:
        self.jobs.append(args)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch, tmp_path) -> FakePool:
    pool = FakePool()

    async def create_pool(settings: Any) -> FakePool:
        return pool

    FakeRedis.closed = False
    monkeypatch.setattr(wiring, "Redis", FakeRedis)
    monkeypatch.setattr(wiring, "create_pool", create_pool)
    monkeypatch.setattr(
        wiring,
        "get_mcp_ports",
        lambda settings: SimpleNamespace(
            directory=StaticDirectory([ASHA]), calendar=StaticCalendar(), finance=FakeFinance()
        ),
    )
    return pool


def settings(tmp_path) -> Settings:
    return Settings(
        llm_mode="fake",
        decision_engine="llm",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'w.db').as_posix()}",
        redis_url="redis://cache:6379/1",
        upload_dir=tmp_path / "uploads",
    )


async def test_build_container_wires_the_api_process(fakes: FakePool, tmp_path):
    container = await wiring.build_container(settings(tmp_path))

    assert isinstance(container.storage, LocalStorage)
    assert isinstance(container.events, RedisEventBus)
    assert container.llm is not None and container.llm.mode == "fake"
    assert (await container.directory.get("P001")) == ASHA

    await container.enqueue("batch-1")
    assert fakes.jobs == [("process_batch", "batch-1")]  # the job name the worker registers
    assert "process_batch" in {f.__name__ for f in worker.WorkerSettings.functions}

    await container.aclose()
    assert FakeRedis.closed and fakes.closed  # connections are released


async def test_build_pipeline_deps_wires_the_worker_process(fakes: FakePool, tmp_path):
    deps, close = await wiring.build_pipeline_deps(settings(tmp_path))

    assert isinstance(deps.index, DbDuplicateIndex)
    assert isinstance(deps.decisions, LLMEngine)  # DECISION_ENGINE=llm
    assert deps.policy.version
    assert isinstance(deps.events, RedisEventBus)
    assert deps.extractor is not None and isinstance(deps.storage, LocalStorage)
    assert RedisExtractionCache is not None

    await close()
    assert FakeRedis.closed


async def test_jev_engine_is_a_cascade_with_the_llm_fallback(fakes: FakePool, tmp_path):
    cfg = settings(tmp_path).model_copy(
        update={"decision_engine": "jev", "jev_api_key": SecretStr("k")}
    )
    deps, close = await wiring.build_pipeline_deps(cfg)
    assert deps.decisions.name == "jev+llm"
    await close()


async def test_the_worker_builds_deps_runs_jobs_and_cleans_up(monkeypatch: pytest.MonkeyPatch):
    closed: list[bool] = []
    ran: list[tuple[Any, str]] = []

    async def build_pipeline_deps(settings: Settings):
        async def close() -> None:
            closed.append(True)

        return "DEPS", close

    async def run_pipeline(deps: Any, batch_id: str) -> None:
        ran.append((deps, batch_id))

    monkeypatch.setattr(wiring, "build_pipeline_deps", build_pipeline_deps)
    monkeypatch.setattr(worker, "run_pipeline", run_pipeline)
    ctx: dict[str, Any] = {}

    await worker.startup(ctx)
    assert ctx["deps"] == "DEPS"
    await worker.process_batch(ctx, "b-9")
    assert ran == [("DEPS", "b-9")]
    await worker.shutdown(ctx)
    assert closed == [True]
    await worker.shutdown({})  # a worker that never finished starting shuts down quietly


def test_the_app_builds_its_container_on_startup_and_closes_it(monkeypatch: pytest.MonkeyPatch):
    events: list[str] = []
    container = Container(
        settings=Settings(),
        sessions=None,  # type: ignore[arg-type]
        repo=None,  # type: ignore[arg-type]
        storage=None,  # type: ignore[arg-type]
        events=None,  # type: ignore[arg-type]
        enqueue=None,  # type: ignore[arg-type]
        directory=StaticDirectory([ASHA]),
        finance=FakeFinance(),
        calendar=StaticCalendar(),
    )

    async def close() -> None:
        events.append("closed")

    container.closers.append(close)

    async def build_container(settings: Settings) -> Container:
        events.append("built")
        return container

    monkeypatch.setattr(wiring, "build_container", build_container)
    app = create_app()  # no container injected: the lifespan builds one
    with TestClient(app) as client:
        assert app.state.container is container
        assert client.get("/healthz").json()["status"] == "ok"
        assert client.get("/v1/employees").json()[0]["id"] == "P001"
    assert events == ["built", "closed"]


def test_an_injected_container_is_used_as_is_and_not_closed():
    closed: list[bool] = []

    async def close() -> None:
        closed.append(True)

    container = Container(
        settings=Settings(),
        sessions=None,  # type: ignore[arg-type]
        repo=None,  # type: ignore[arg-type]
        storage=None,  # type: ignore[arg-type]
        events=None,  # type: ignore[arg-type]
        enqueue=None,  # type: ignore[arg-type]
        directory=StaticDirectory([ASHA]),
        finance=FakeFinance(),
        calendar=StaticCalendar(),
        closers=[close],
    )
    with TestClient(create_app(container)) as client:
        assert client.get("/v1/employees").status_code == 200
    assert closed == []  # whoever injected the container owns its lifetime
