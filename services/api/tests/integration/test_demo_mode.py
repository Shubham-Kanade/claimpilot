"""The hosted demo's settings: who counts as a duplicate, and what a visitor is told on a miss."""

# ruff: noqa: F811, F401
# The `fixtures` and `sessions` pytest fixtures come from test_process_batch; using them as test
# arguments looks like shadowing to ruff.

from __future__ import annotations

import pytest
from test_process_batch import (
    FIXTURES,
    TODAY,
    Harness,
    build_harness,
    fixtures,
    sessions,
)

from claimpilot.config import Settings
from claimpilot.llm.errors import ReplayMissError
from claimpilot.pipeline.process import DEMO_MISS, process_batch

pytestmark = pytest.mark.skipif(not FIXTURES.exists(), reason="synthetic fixtures not present")

CAB = "s42-0005"


def codes_of(view) -> list[str]:
    [document] = view.documents
    assert document.document is not None
    return [f.code for f in document.document.findings]


def harness_with(sessions, models_registry, fixtures, **settings) -> Harness:
    return build_harness(
        sessions, models_registry, fixtures, settings=Settings(demo_today=TODAY, **settings)
    )


async def test_company_scope_flags_a_receipt_another_employee_already_submitted(
    sessions,
    models_registry,
    fixtures,
):
    harness = harness_with(sessions, models_registry, fixtures)
    await harness.run(await harness.upload_fixtures(CAB, employee="P001"))

    second = await harness.run(await harness.upload_fixtures(CAB, employee="P005"))

    assert "duplicate_exact" in codes_of(second)


async def test_employee_scope_leaves_another_employees_upload_alone(
    sessions,
    models_registry,
    fixtures,
):
    harness = harness_with(sessions, models_registry, fixtures, duplicate_scope="employee")
    await harness.run(await harness.upload_fixtures(CAB, employee="P001"))

    another = await harness.run(await harness.upload_fixtures(CAB, employee="P005"))
    same = await harness.run(await harness.upload_fixtures(CAB, employee="P001"))

    assert not any(code.startswith("duplicate") for code in codes_of(another))
    assert "duplicate_exact" in codes_of(same)  # their own earlier copy still counts


async def test_after_the_visitor_starts_over_the_samples_are_fresh_again(
    sessions,
    models_registry,
    fixtures,
):
    harness = harness_with(sessions, models_registry, fixtures, duplicate_scope="employee")
    await harness.run(await harness.upload_fixtures(CAB, employee="P001"))
    await harness.repo.delete_data("P001")

    again = await harness.run(await harness.upload_fixtures(CAB, employee="P001"))

    assert not any(code.startswith("duplicate") for code in codes_of(again))


def unrecorded(_request):
    raise ReplayMissError("no recording for route 'extraction' (model claude-haiku-5-5) at /app/x")


async def test_an_unrecorded_receipt_in_the_demo_gets_a_plain_explanation(
    sessions,
    models_registry,
    fixtures,
):
    harness = build_harness(
        sessions,
        models_registry,
        fixtures,
        responder=unrecorded,
        settings=Settings(demo_today=TODAY, demo_mode=True),
    )
    view = await harness.run(await harness.upload_fixtures(CAB))

    [document] = view.documents
    assert document.status == "failed" and document.error == DEMO_MISS
    assert "API key" in DEMO_MISS and "/app/" not in DEMO_MISS


async def test_outside_the_demo_the_technical_reason_is_kept(
    sessions,
    models_registry,
    fixtures,
):
    harness = build_harness(sessions, models_registry, fixtures, responder=unrecorded)
    view = await harness.run(await harness.upload_fixtures(CAB))

    [document] = view.documents
    assert document.error is not None and document.error.startswith("ReplayMissError")


async def test_two_batches_of_the_same_receipts_flag_each_copy_exactly_once(
    sessions, models_registry, fixtures
):
    import asyncio

    harness = harness_with(sessions, models_registry, fixtures)
    ids = ("s42-0002", "s42-0005", "s42-0003")
    first = await harness.upload_fixtures(*ids, employee="P001")
    second = await harness.upload_fixtures(*ids, employee="P001")

    await asyncio.gather(process_batch(harness.deps, first), process_batch(harness.deps, second))

    flagged = 0
    for batch_id in (first, second):
        view = await harness.repo.get_batch(batch_id)
        assert view is not None and view.status == "done"
        for document in view.documents:
            assert document.document is not None
            flagged += any(f.code.startswith("duplicate") for f in document.document.findings)
    assert flagged == len(ids)  # one copy of each file, never zero and never both


async def test_a_batch_whose_rows_vanish_midway_still_ends_the_stream(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    batch_id = await harness.upload_fixtures(CAB, employee="P001")
    original = harness.deps.extractor.extract

    async def start_over_during_the_read(*args, **kwargs):
        await harness.repo.delete_data("P001")  # the visitor pressed "Start over" mid-batch
        return await original(*args, **kwargs)

    harness.deps.extractor.extract = start_over_during_the_read  # type: ignore[method-assign]

    await process_batch(harness.deps, batch_id)  # must neither raise nor hang

    types = await harness.event_types(batch_id)
    assert types[-1] in ("batch_failed", "batch_done")  # the stream has an ending
