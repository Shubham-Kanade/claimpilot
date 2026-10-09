"""``claimpilot.telemetry`` with all five ids: what the request, the pipeline and the ledger share.

(``test_telemetry.py`` covers the sandbox and batch ids this module started with; this file covers
the full set and how it travels through tasks, threads and resets.)
"""

from __future__ import annotations

import asyncio

import pytest
import structlog.contextvars

from claimpilot.telemetry import IDS, bound_ids, current_ids, reset_ids

NOTHING: dict[str, str | None] = dict.fromkeys(IDS)
SOME: dict[str, str] = {
    "sandbox": "visitor-aaaaaaaaaaaa",
    "batch_id": "b1",
    "trace_id": "t1",
    "document_id": "d1",
    "claim_id": "c1",
}


@pytest.fixture(autouse=True)
def clean_context():
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


def test_the_ledger_ids_are_exactly_these_five():
    assert set(IDS) == {"sandbox", "batch_id", "trace_id", "document_id", "claim_id"}


def test_every_key_is_present_and_none_when_nothing_is_bound():
    assert current_ids() == NOTHING


def test_all_five_ids_can_be_bound_and_read_back():
    with bound_ids(**SOME):
        assert current_ids() == SOME
    assert current_ids() == NOTHING


def test_binding_some_ids_leaves_the_others_none():
    with bound_ids(trace_id="t1", document_id="d1"):
        assert current_ids() == {**NOTHING, "trace_id": "t1", "document_id": "d1"}


def test_none_values_are_skipped_not_bound():
    with bound_ids(trace_id=None, claim_id="c1"):
        assert "trace_id" not in structlog.contextvars.get_contextvars()
        assert current_ids() == {**NOTHING, "claim_id": "c1"}


def test_nesting_restores_the_outer_values_for_every_id():
    with bound_ids(**SOME):
        with bound_ids(document_id="d2", claim_id="c2"):
            assert current_ids() == {**SOME, "document_id": "d2", "claim_id": "c2"}
        assert current_ids() == SOME
    assert current_ids() == NOTHING


def test_an_inner_none_does_not_hide_an_outer_id():
    with bound_ids(**SOME), bound_ids(document_id=None):
        assert current_ids() == SOME


def test_an_exception_unwinds_every_id():
    with pytest.raises(RuntimeError), bound_ids(**SOME):
        raise RuntimeError("boom")
    assert current_ids() == NOTHING


def test_ids_outside_the_five_are_bound_but_not_reported():
    with bound_ids(request_id="r1", trace_id="t1"):
        assert current_ids() == {**NOTHING, "trace_id": "t1"}  # request_id is for the logs only


def test_reset_clears_every_id_including_unrelated_context():
    structlog.contextvars.bind_contextvars(**SOME, request_id="r1")
    reset_ids()
    assert current_ids() == NOTHING
    assert structlog.contextvars.get_contextvars() == {}


async def test_gathered_tasks_inherit_all_the_ids():
    async def read() -> dict[str, str | None]:
        await asyncio.sleep(0)
        return current_ids()

    with bound_ids(**SOME):
        seen = await asyncio.gather(read(), read(), asyncio.create_task(read()))
    assert seen == [SOME] * 3


async def test_to_thread_inherits_all_the_ids():
    with bound_ids(**SOME):
        seen = await asyncio.to_thread(current_ids)
    assert seen == SOME


async def test_sibling_tasks_do_not_see_each_others_document_id():
    async def read_document(document_id: str) -> list[str | None]:
        seen = []
        with bound_ids(document_id=document_id):
            for _ in range(5):
                await asyncio.sleep(0)  # let the siblings run in between
                seen.append(current_ids()["document_id"])
                seen.append(await asyncio.to_thread(lambda: current_ids()["document_id"]))
        return seen

    with bound_ids(batch_id="b1", trace_id="t1"):
        results = await asyncio.gather(*(read_document(f"doc-{i}") for i in range(4)))

    assert results == [[f"doc-{i}"] * 10 for i in range(4)]
    assert current_ids() == NOTHING  # nothing leaked back into the parent


async def test_a_sibling_keeps_the_parents_ids_while_another_binds_its_own():
    async def with_document() -> None:
        with bound_ids(document_id="d1"):
            await asyncio.sleep(0.01)

    async def without_document() -> dict[str, str | None]:
        await asyncio.sleep(0.005)  # while the sibling is inside its block
        return current_ids()

    with bound_ids(batch_id="b1"):
        _, seen = await asyncio.gather(with_document(), without_document())
    assert seen == {**NOTHING, "batch_id": "b1"}


async def test_reset_inside_a_task_leaves_the_parent_alone():
    async def job() -> dict[str, str | None]:
        reset_ids()  # a background job starts from its own row
        return current_ids()

    with bound_ids(**SOME):
        assert await asyncio.create_task(job()) == NOTHING
        assert current_ids() == SOME
