"""``claimpilot.telemetry``: ids carried alongside a piece of work for the cost ledger."""

from __future__ import annotations

import asyncio

import pytest
import structlog.contextvars

from claimpilot.telemetry import bound_ids, current_ids, reset_ids

NOTHING = {"sandbox": None, "batch_id": None}


def ids() -> dict[str, str | None]:
    """The two ids this feature is about (the ledger may track more)."""
    return {name: current_ids()[name] for name in NOTHING}


@pytest.fixture(autouse=True)
def clean_context():
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


def test_nothing_is_bound_by_default():
    assert ids() == NOTHING
    assert set(NOTHING) <= set(current_ids())


def test_ids_are_bound_for_the_block_and_forgotten_after_it():
    with bound_ids(sandbox="visitor-aaaaaaaaaaaa", batch_id="b1"):
        assert ids() == {"sandbox": "visitor-aaaaaaaaaaaa", "batch_id": "b1"}
    assert ids() == NOTHING


def test_none_values_are_skipped_rather_than_bound():
    with bound_ids(sandbox=None, batch_id="b1"):
        assert ids() == {"sandbox": None, "batch_id": "b1"}
        assert "sandbox" not in structlog.contextvars.get_contextvars()


def test_a_none_never_hides_an_outer_value():
    with bound_ids(sandbox="visitor-aaaaaaaaaaaa"), bound_ids(sandbox=None, batch_id="b1"):
        assert ids() == {"sandbox": "visitor-aaaaaaaaaaaa", "batch_id": "b1"}


def test_nesting_restores_the_outer_values():
    with bound_ids(sandbox="outer-outer-outer-1", batch_id="b1"):
        with bound_ids(sandbox="inner-inner-inner-2"):
            assert ids() == {"sandbox": "inner-inner-inner-2", "batch_id": "b1"}
        assert ids() == {"sandbox": "outer-outer-outer-1", "batch_id": "b1"}
    assert ids() == NOTHING


def test_an_exception_does_not_leave_ids_behind():
    with pytest.raises(RuntimeError), bound_ids(sandbox="visitor-aaaaaaaaaaaa", batch_id="b1"):
        raise RuntimeError("boom")
    assert ids() == NOTHING


def test_unrelated_context_is_not_reported_as_an_id():
    with structlog.contextvars.bound_contextvars(request_id="r1", user="u"):
        assert "request_id" not in current_ids() and "user" not in current_ids()


def test_reset_forgets_everything_bound_so_far():
    structlog.contextvars.bind_contextvars(sandbox="visitor-aaaaaaaaaaaa", batch_id="b1")
    reset_ids()
    assert ids() == NOTHING


async def test_a_task_created_inside_the_block_inherits_the_ids():
    async def read() -> dict[str, str | None]:
        await asyncio.sleep(0)
        return ids()

    with bound_ids(sandbox="visitor-aaaaaaaaaaaa", batch_id="b1"):
        task = asyncio.create_task(read())
        gathered = await asyncio.gather(read(), read())
    assert await task == {"sandbox": "visitor-aaaaaaaaaaaa", "batch_id": "b1"}
    assert gathered == [{"sandbox": "visitor-aaaaaaaaaaaa", "batch_id": "b1"}] * 2


async def test_a_task_created_before_the_block_does_not_see_it():
    async def read_later(gate: asyncio.Event) -> dict[str, str | None]:
        await gate.wait()
        return ids()

    gate = asyncio.Event()
    task = asyncio.create_task(read_later(gate))  # its context was copied now
    with bound_ids(sandbox="visitor-aaaaaaaaaaaa"):
        gate.set()
        assert await task == NOTHING


async def test_to_thread_inherits_the_ids():
    with bound_ids(sandbox="visitor-aaaaaaaaaaaa", batch_id="b1"):
        seen = await asyncio.to_thread(ids)
    assert seen == {"sandbox": "visitor-aaaaaaaaaaaa", "batch_id": "b1"}


async def test_concurrent_work_for_different_sandboxes_never_mixes_the_ids():
    async def work(sandbox: str, batch_id: str) -> list[dict[str, str | None]]:
        seen = []
        with bound_ids(sandbox=sandbox, batch_id=batch_id):
            for _ in range(5):
                await asyncio.sleep(0)  # let the other task run in between
                seen.append(ids())
                seen.append(await asyncio.to_thread(ids))
        return seen

    first, second = await asyncio.gather(
        asyncio.create_task(work("visitor-aaaaaaaaaaaa", "b1")),
        asyncio.create_task(work("visitor-bbbbbbbbbbbb", "b2")),
    )

    assert first == [{"sandbox": "visitor-aaaaaaaaaaaa", "batch_id": "b1"}] * 10
    assert second == [{"sandbox": "visitor-bbbbbbbbbbbb", "batch_id": "b2"}] * 10
    assert ids() == NOTHING


async def test_resetting_inside_a_task_leaves_its_parent_alone():
    async def job() -> dict[str, str | None]:
        reset_ids()  # a background job starts from its own row
        return ids()

    with bound_ids(sandbox="visitor-aaaaaaaaaaaa"):
        assert await asyncio.create_task(job()) == NOTHING
        assert ids()["sandbox"] == "visitor-aaaaaaaaaaaa"
