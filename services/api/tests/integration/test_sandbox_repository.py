"""Sandbox scoping in the repository: ``None`` means "no sandbox", never "no filter".

A demo visitor's rows carry their sandbox id. A lookup that forgets to say which sandbox it is
about must find only sandbox-less rows (so a missing argument fails closed), and only the
pipeline, which serves every world, may ask for ``UNSCOPED``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from claimpilot.config import API_ROOT, Settings
from claimpilot.db import (
    AuditEvent,
    Batch,
    ClaimRow,
    Document,
    LlmCall,
    SessionFactory,
    create_engine,
    create_session_factory,
)
from claimpilot.domain.claims import Claim, ClaimMode
from claimpilot.pipeline.repo import UNSCOPED, NewFile, Repository, _same_sandbox, in_sandbox

A = "visitor-aaaaaaaaaaaa"
B = "visitor-bbbbbbbbbbbb"


def migrate(url: str) -> None:
    config = Config(API_ROOT / "alembic.ini")
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[SessionFactory]:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'sandbox.db').as_posix()}"
    await asyncio.to_thread(migrate, url)
    engine = create_engine(Settings(database_url=url))
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def repo(sessions: SessionFactory) -> Repository:
    return Repository(sessions)


def claim(claim_id: str, employee_id: str = "P001") -> Claim:
    return Claim(
        id=claim_id,
        employee_id=employee_id,
        title="Local conveyance Oct 2026",
        mode=ClaimMode.period,
        document_ids=[],
        total=320.0,
        start_date=date(2026, 10, 3),
    )


async def put(
    repo: Repository, sandbox: str | None, employee_id: str = "P001", tag: str = ""
) -> tuple[str, str, str]:
    """One batch with one document and one claim: ``(batch_id, document_id, claim_id)``."""
    label = f"{sandbox or 'none'}-{employee_id}{tag}"
    batch_id, (doc_id,) = await repo.create_batch(
        employee_id, [NewFile("a.png", "a" * 64, f"k/{label}.png")], sandbox=sandbox
    )
    claim_id = f"clm-{label}"
    await repo.save_claims(
        batch_id, employee_id, [claim(claim_id, employee_id)], {claim_id: "finance_review"},
        sandbox=sandbox,
    )  # fmt: skip
    return batch_id, doc_id, claim_id


@pytest.fixture
async def world(repo: Repository) -> dict[str | None, tuple[str, str, str]]:
    """The same employee has one set of rows in A, one in B and one in no sandbox."""
    return {sandbox: await put(repo, sandbox) for sandbox in (A, B, None)}


# --- the predicate -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "scope", "visible"),
    [
        (A, A, True),
        (A, B, False),
        (A, None, False),  # None is "no sandbox", not "any"
        (None, None, True),
        (None, A, False),
        (A, UNSCOPED, True),
        (None, UNSCOPED, True),
    ],
)
def test_a_row_is_in_a_scope_only_when_it_belongs_to_it(row, scope, visible):
    assert _same_sandbox(row, scope) is visible


async def test_the_sql_condition_agrees_with_the_python_one(
    sessions: SessionFactory, world: dict[str | None, tuple[str, str, str]]
):
    async def claims_in(scope) -> set[str]:
        async with sessions() as session:
            query = select(ClaimRow.id).where(in_sandbox(ClaimRow.sandbox, scope))
            return set((await session.scalars(query)).all())

    every = {c for _, _, c in world.values()}
    assert await claims_in(A) == {world[A][2]}
    assert await claims_in(B) == {world[B][2]}
    assert await claims_in(None) == {world[None][2]}  # IS NULL
    assert await claims_in("visitor-nobody-here") == set()
    assert await claims_in(UNSCOPED) == every


def test_unscoped_is_not_a_string_a_visitor_could_send():
    assert not isinstance(UNSCOPED, str) and repr(UNSCOPED) == "UNSCOPED"
    assert not _same_sandbox("UNSCOPED", A)


# --- lookups fail closed -----------------------------------------------------------------------


async def test_a_lookup_that_omits_the_sandbox_finds_only_sandbox_less_rows(
    repo: Repository, world: dict[str | None, tuple[str, str, str]]
):
    for sandbox in (A, B):
        batch_id, doc_id, claim_id = world[sandbox]
        assert await repo.get_batch(batch_id) is None
        assert await repo.get_document(doc_id) is None
        assert await repo.get_claim(claim_id) is None
    batch_id, doc_id, claim_id = world[None]
    assert (await repo.get_batch(batch_id)) is not None
    assert (await repo.get_document(doc_id)) is not None
    assert (await repo.get_claim(claim_id)) is not None
    assert [c.id for c in await repo.list_claims()] == [claim_id]


async def test_a_lookup_in_the_right_sandbox_finds_the_row_and_in_any_other_does_not(
    repo: Repository, world: dict[str | None, tuple[str, str, str]]
):
    for owner, (batch_id, doc_id, claim_id) in world.items():
        for asker in (A, B, None):
            found = asker == owner
            batch = await repo.get_batch(batch_id, sandbox=asker)
            document = await repo.get_document(doc_id, sandbox=asker)
            found_claim = await repo.get_claim(claim_id, sandbox=asker)
            assert (batch is not None, document is not None, found_claim is not None) == (
                found,
                found,
                found,
            ), f"rows of {owner} asked for from {asker}"


async def test_the_pipelines_unscoped_lookup_sees_every_world(
    repo: Repository, world: dict[str | None, tuple[str, str, str]]
):
    for batch_id, doc_id, claim_id in world.values():
        assert await repo.get_batch(batch_id, sandbox=UNSCOPED) is not None
        assert await repo.get_document(doc_id, sandbox=UNSCOPED) is not None
        assert await repo.get_claim(claim_id, sandbox=UNSCOPED) is not None
    assert {c.id for c in await repo.list_claims(sandbox=UNSCOPED)} == {
        c for _, _, c in world.values()
    }


async def test_a_batch_view_carries_only_its_own_documents_and_claims(
    repo: Repository, world: dict[str | None, tuple[str, str, str]]
):
    for sandbox, (batch_id, doc_id, claim_id) in world.items():
        view = await repo.get_batch(batch_id, sandbox=sandbox)
        assert view is not None
        assert [d.id for d in view.documents] == [doc_id]
        assert [c.id for c in view.claims] == [claim_id]


async def test_list_claims_keeps_its_other_filters_inside_the_sandbox(repo: Repository):
    await put(repo, A, "P001")
    await put(repo, A, "P002")
    await put(repo, B, "P001")
    assert [c.id for c in await repo.list_claims(employee_id="P001", sandbox=A)] == [
        "clm-" + A + "-P001"
    ]
    assert await repo.list_claims(employee_id="P002", sandbox=B) == []
    assert await repo.list_claims(status="draft", route="auto_approve", sandbox=A) == []
    assert len(await repo.list_claims(route="finance_review", sandbox=A)) == 2


# --- batch_sandbox -----------------------------------------------------------------------------


async def test_batch_sandbox_tells_which_world_a_batch_belongs_to(
    repo: Repository, world: dict[str | None, tuple[str, str, str]]
):
    assert await repo.batch_sandbox(world[A][0]) == A
    assert await repo.batch_sandbox(world[B][0]) == B
    assert await repo.batch_sandbox(world[None][0]) is None
    assert await repo.batch_sandbox("no-such-batch") is None  # the caller looks the batch up first


async def test_create_batch_gives_the_documents_the_batches_sandbox(
    repo: Repository, sessions: SessionFactory
):
    batch_id, doc_ids = await repo.create_batch(
        "P001", [NewFile("a.png", "a" * 64, "k/a"), NewFile("b.png", "b" * 64, "k/b")], sandbox=A
    )
    async with sessions() as session:
        docs = (await session.scalars(select(Document).where(Document.batch_id == batch_id))).all()
        batch = await session.get(Batch, batch_id)
    assert batch is not None and batch.sandbox == A
    assert sorted(d.id for d in docs) == sorted(doc_ids) and {d.sandbox for d in docs} == {A}


async def test_saving_claims_again_for_a_retried_batch_keeps_one_row_in_the_sandbox(
    repo: Repository, sessions: SessionFactory
):
    batch_id, _, claim_id = await put(repo, A)
    await repo.save_claims(
        batch_id, "P001", [claim(claim_id)], {claim_id: "auto_approve"}, sandbox=A
    )
    async with sessions() as session:
        rows = (await session.scalars(select(ClaimRow))).all()
    assert [(r.id, r.sandbox, r.route) for r in rows] == [(claim_id, A, "auto_approve")]


# --- delete_data -------------------------------------------------------------------------------


async def populate(repo: Repository) -> None:
    for sandbox in (A, B, None):
        for employee in ("P001", "P002"):
            await put(repo, sandbox, employee)


async def remaining(sessions: SessionFactory) -> set[tuple[str | None, str]]:
    """``(sandbox, employee)`` of every surviving claim, with the batches and documents checked."""
    async with sessions() as session:
        claims = {(r.sandbox, r.employee_id) for r in await session.scalars(select(ClaimRow))}
        batches = {(r.sandbox, r.employee_id) for r in await session.scalars(select(Batch))}
        docs = {(r.sandbox, r.employee_id) for r in await session.scalars(select(Document))}
    assert claims == batches == docs
    return claims


@pytest.mark.parametrize(
    ("employee", "sandbox", "gone"),
    [
        ("P001", A, {(A, "P001")}),
        (None, A, {(A, "P001"), (A, "P002")}),
        ("P002", B, {(B, "P002")}),
        ("P001", None, {(None, "P001")}),
        (None, None, {(None, "P001"), (None, "P002")}),
        (None, "visitor-nobody-here", set()),
    ],
    ids=[
        "employee-in-A",
        "everyone-in-A",
        "employee-in-B",
        "employee-no-sandbox",
        "everyone-no-sandbox",
        "empty-sandbox",
    ],
)
async def test_delete_data_reaches_only_the_given_employee_in_the_given_sandbox(
    repo: Repository, sessions: SessionFactory, employee, sandbox, gone
):
    await populate(repo)
    everyone = {(s, e) for s in (A, B, None) for e in ("P001", "P002")}

    deleted = await repo.delete_data(employee, sandbox=sandbox)

    assert await remaining(sessions) == everyone - gone
    assert (deleted.batches, deleted.documents, deleted.claims) == (len(gone),) * 3
    assert sorted(deleted.storage_keys) == sorted(f"k/{s or 'none'}-{e}.png" for s, e in gone)


async def test_delete_data_without_a_sandbox_leaves_every_sandbox_alone(
    repo: Repository, sessions: SessionFactory
):
    await populate(repo)
    await repo.delete_data()  # the old call shape: the sandbox-less world only
    assert await remaining(sessions) == {(s, e) for s in (A, B) for e in ("P001", "P002")}


async def test_the_pipelines_unscoped_delete_means_everything(
    repo: Repository, sessions: SessionFactory
):
    await populate(repo)
    deleted = await repo.delete_data(sandbox=UNSCOPED)
    assert await remaining(sessions) == set() and deleted.claims == 6


async def test_delete_data_forgets_the_audit_trail_of_deleted_rows_only(
    repo: Repository, sessions: SessionFactory, world: dict[str | None, tuple[str, str, str]]
):
    for _, _, claim_id in world.values():
        await repo.audit("P001", "claim_answered", "claim", claim_id)

    await repo.delete_data("P001", sandbox=A)

    assert await repo.audit_trail(world[A][2]) == []
    assert [e.action for e in await repo.audit_trail(world[B][2])] == ["claim_answered"]
    assert [e.action for e in await repo.audit_trail(world[None][2])] == ["claim_answered"]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 2


async def test_delete_data_never_touches_the_cost_ledger(
    repo: Repository, sessions: SessionFactory, world: dict[str | None, tuple[str, str, str]]
):
    async with sessions() as session:
        for sandbox in (A, B, None):
            session.add(
                LlmCall(
                    route="extraction", model_key="haiku", model_id="m", mode="fake",
                    request_hash="h", sandbox=sandbox, batch_id=world[sandbox][0],
                )
            )  # fmt: skip
        await session.commit()

    await repo.delete_data(sandbox=A)

    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(LlmCall)) == 3
