"""Every LLM call in the cost ledger knows its trace, sandbox, batch, document and claim.

The pipeline runs documents concurrently, so the document id of a ledger row must come from the
task that made the call, never from whichever document happened to be running next to it. The
tests below read the mapping back from the requests the fake model saw (a receipt's image, or the
state the decision engine was shown), so a swapped document id fails them.
"""

# ruff: noqa: F811, F401
# The `fixtures` and `sessions` pytest fixtures come from test_process_batch; using them as test
# arguments looks like shadowing to ruff.

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace
from functools import cache
from typing import Any

import pytest
from sqlalchemy import select
from test_process_batch import (
    FIXTURES,
    TODAY,
    Fixture,
    Harness,
    build_harness,
    fixtures,
    sessions,
)

from claimpilot.api.deps import Persona
from claimpilot.config import Settings
from claimpilot.container import Container
from claimpilot.db import Batch, LlmCall
from claimpilot.decisions.llm_engine import LLMEngine, answer_model
from claimpilot.decisions.questions import DOCUMENT_QUESTIONS
from claimpilot.domain.claims import Claim, ClaimMode, OpenQuestion, QuestionKind
from claimpilot.evals.golden import has_alcohol
from claimpilot.extraction import prepare_document
from claimpilot.extraction.locate import LocatedFields
from claimpilot.extraction.schema import from_domain
from claimpilot.llm.ledger import CostLedger
from claimpilot.llm.params import LLMRequest
from claimpilot.llm.types import CallInfo, TokenUsage
from claimpilot.pipeline import actions
from claimpilot.pipeline.process import process_batch
from claimpilot.pipeline.reply import reply_model
from claimpilot.pipeline.repo import NewFile
from claimpilot.ports import FakeFinance, StaticCalendar
from claimpilot.storage import InMemoryStorage
from claimpilot.telemetry import bound_ids, current_ids

pytestmark = pytest.mark.skipif(not FIXTURES.exists(), reason="synthetic fixtures not present")

A = "visitor-aaaaaaaaaaaa"
B = "visitor-bbbbbbbbbbbb"
# a pile with a tampered bill and an injection: they trigger the second-opinion route too
PILE = (
    "s42-0005",
    "s42-0082",
    "s42-0097",
    "s42-0086",
    "s42-0013",
    "s42-0006",
    "s42-0003",
    "s42-0069",
)
FIRST = ("s42-0005", "s42-0082", "s42-0097")
SECOND = ("s42-0086", "s42-0013", "s42-0006")
ROUTES = {"extraction", "decision_fallback", "locate"}


def harness_with(sessions, models_registry, fixtures, *, responder=None, **settings) -> Harness:
    """The pipeline with all three kinds of LLM call (read, decide, locate) on a real ledger."""
    harness = build_harness(
        sessions,
        models_registry,
        fixtures,
        responder=responder,
        settings=Settings(demo_today=TODAY, **settings),
        locate=lambda _request: LocatedFields.model_validate(
            {name: "" for name in LocatedFields.model_fields}
        ),
    )
    by_state = {
        (f.truth.receipt.merchant_name, f.truth.receipt.total): f for f in fixtures.values()
    }

    def decide(request: LLMRequest) -> dict[str, Any]:
        state = state_of(request)
        fixture = by_state[(state["merchant"], state["total"])]
        return {
            "category": fixture.truth.category.value,
            "category_confidence": 0.95,
            "alcohol_present": float(has_alcohol(fixture.truth)),
            "personal_expense": 0.0,
        }

    harness.llm.register(answer_model(DOCUMENT_QUESTIONS), decide, route="decision_fallback")
    harness.llm.ledger = CostLedger(sessions)
    harness.deps = replace(harness.deps, decisions=LLMEngine(harness.llm), concurrency=4)
    return harness


def state_of(request: LLMRequest) -> dict[str, Any]:
    """The receipt state the decision engine put in its prompt."""
    text = request.body["messages"][0]["content"][0]["text"]
    return json.loads(text.split("<state>\n", 1)[1].split("\n</state>", 1)[0])


@cache
def image_of(raw: bytes) -> str:
    return prepare_document(raw).blocks[0]["source"]["data"]


def fixture_of(request: LLMRequest, fixtures: dict[str, Fixture]) -> str:
    """Which receipt a request was about, read from the request itself (not from any ledger id)."""
    if request.route == "decision_fallback":
        state = state_of(request)
        [match] = [
            f.id
            for f in fixtures.values()
            if (f.truth.receipt.merchant_name, f.truth.receipt.total)
            == (state["merchant"], state["total"])
        ]
        return match
    image = request.body["messages"][0]["content"][0]["source"]["data"]
    [match] = [f.id for f in fixtures.values() if image_of(f.raw) == image]
    return match


async def upload(
    harness: Harness,
    ids: Sequence[str],
    *,
    employee: str = "P001",
    sandbox: str | None = None,
    trace_id: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Upload receipts; returns the batch id and ``{fixture id: document id}``."""
    news = []
    for i, fixture_id in enumerate(ids):
        raw = harness.fixtures[fixture_id].raw
        key = f"{sandbox or 'none'}/{employee}/{len(harness.storage.objects)}-{i}-{fixture_id}"
        await harness.storage.put(key, raw)
        news.append(NewFile(fixture_id, hashlib.sha256(raw).hexdigest(), key))
    batch_id, doc_ids = await harness.repo.create_batch(
        employee, news, sandbox=sandbox, trace_id=trace_id
    )
    return batch_id, dict(zip(ids, doc_ids, strict=True))


async def ledger_rows(sessions) -> list[LlmCall]:
    async with sessions() as session:
        return list((await session.scalars(select(LlmCall))).all())


def made_for(rows: list[LlmCall], harness: Harness, documents: dict[str, str]) -> dict[str, str]:
    """``request_hash -> the document it was about`` for every row (the hash ties them)."""
    seen = {r.request_hash: r for r in harness.llm.requests}
    return {
        row.request_hash: documents[fixture_of(seen[row.request_hash], harness.fixtures)]
        for row in rows
        if row.request_hash in seen
    }


# --- documents, concurrency 4 ---------------------------------------------------------------------


async def test_every_row_carries_the_document_it_was_made_for(sessions, models_registry, fixtures):
    harness = harness_with(sessions, models_registry, fixtures)
    batch_id, documents = await upload(harness, PILE, sandbox=A, trace_id="req-abc-123")
    assert harness.deps.concurrency == 4

    await process_batch(harness.deps, batch_id)
    rows = await ledger_rows(sessions)

    assert {r.route for r in rows} >= ROUTES  # reads, decisions and locates all went through
    assert len(rows) == len(harness.llm.requests) > len(PILE)
    seen = {r.request_hash: r for r in harness.llm.requests}
    assert len(seen) == len(rows)  # a hash identifies exactly one row, so the mapping is exact
    for row in rows:
        assert row.document_id == documents[fixture_of(seen[row.request_hash], fixtures)], row.route
        assert (row.sandbox, row.batch_id, row.trace_id) == (A, batch_id, "req-abc-123")
        assert row.claim_id is None  # claims do not exist yet while documents are read


async def test_each_document_has_its_own_row_for_every_route(sessions, models_registry, fixtures):
    harness = harness_with(sessions, models_registry, fixtures)
    batch_id, documents = await upload(harness, PILE, trace_id="req-1")

    await process_batch(harness.deps, batch_id)
    rows = await ledger_rows(sessions)

    for fixture_id, document_id in documents.items():
        routes = {r.route for r in rows if r.document_id == document_id}
        assert routes >= ROUTES, fixture_id
    assert {r.document_id for r in rows} == set(documents.values())  # none unattributed


async def test_a_swapped_document_id_would_be_caught(sessions, models_registry, fixtures):
    # the guard on the check above: rows of different documents really are different
    harness = harness_with(sessions, models_registry, fixtures)
    batch_id, documents = await upload(harness, FIRST)
    await process_batch(harness.deps, batch_id)
    rows = await ledger_rows(sessions)

    mapping = made_for(rows, harness, documents)
    first, second = (documents[f] for f in FIRST[:2])
    assert first != second
    assert {r.document_id for r in rows if mapping[r.request_hash] == first} == {first}
    assert {r.document_id for r in rows if mapping[r.request_hash] == second} == {second}


async def test_the_second_opinion_read_is_attributed_to_its_document_too(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    batch_id, documents = await upload(harness, ("s42-0082", "s42-0097", "s42-0086"))

    await process_batch(harness.deps, batch_id)
    rows = await ledger_rows(sessions)

    retries = [r for r in rows if r.route == "extraction_retry"]
    assert retries  # the tampered and injected bills were read twice
    seen = {r.request_hash: r for r in harness.llm.requests}
    for row in retries:
        assert row.document_id == documents[fixture_of(seen[row.request_hash], fixtures)]


async def test_a_failed_call_is_attributed_to_its_document(sessions, models_registry, fixtures):
    from claimpilot.llm.errors import LLMError

    def read(request: LLMRequest):
        fixture_id = fixture_of(request, fixtures)
        if fixture_id == "s42-0013":
            raise LLMError("unavailable")
        return from_domain(fixtures[fixture_id].truth.receipt)

    harness = harness_with(sessions, models_registry, fixtures, responder=read)
    batch_id, documents = await upload(harness, ("s42-0005", "s42-0013", "s42-0003"))

    await process_batch(harness.deps, batch_id)

    errors = [r for r in await ledger_rows(sessions) if r.error]
    assert errors and {r.document_id for r in errors} == {documents["s42-0013"]}
    assert {r.batch_id for r in errors} == {batch_id}


# --- batches at the same time ---------------------------------------------------------------------


async def test_two_batches_processed_at_once_never_mix_their_ids(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    first, first_docs = await upload(harness, FIRST, sandbox=A, trace_id="trace-a")
    second, second_docs = await upload(
        harness, SECOND, employee="P005", sandbox=B, trace_id="trace-b"
    )

    await asyncio.gather(process_batch(harness.deps, first), process_batch(harness.deps, second))
    rows = await ledger_rows(sessions)

    seen = {r.request_hash: r for r in harness.llm.requests}
    expected = {
        **dict.fromkeys(first_docs.values(), (A, first, "trace-a")),
        **dict.fromkeys(second_docs.values(), (B, second, "trace-b")),
    }
    documents = {**first_docs, **second_docs}
    assert len(rows) == len(harness.llm.requests)
    for row in rows:
        assert row.document_id == documents[fixture_of(seen[row.request_hash], fixtures)]
        assert row.document_id is not None
        assert (row.sandbox, row.batch_id, row.trace_id) == expected[row.document_id]
    assert {r.batch_id for r in rows} == {first, second}


async def test_two_batches_of_the_same_sandbox_keep_their_own_traces(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    first, _ = await upload(harness, FIRST, sandbox=A, trace_id="trace-1")
    second, _ = await upload(harness, SECOND, sandbox=A, trace_id="trace-2")

    await asyncio.gather(process_batch(harness.deps, first), process_batch(harness.deps, second))
    rows = await ledger_rows(sessions)

    by_batch = {b: {r.trace_id for r in rows if r.batch_id == b} for b in (first, second)}
    assert by_batch == {first: {"trace-1"}, second: {"trace-2"}}
    assert {r.sandbox for r in rows} == {A}


# --- the calling context --------------------------------------------------------------------------


async def test_process_batch_called_directly_binds_the_batch_row_trace(
    sessions, models_registry, fixtures
):
    # what an Arq worker does: no request around it, the trace comes from the batch row alone
    harness = harness_with(sessions, models_registry, fixtures)
    batch_id, _ = await upload(harness, FIRST[:1], sandbox=A, trace_id="req-from-upload")
    assert current_ids() == dict.fromkeys(current_ids())

    await process_batch(harness.deps, batch_id)

    rows = await ledger_rows(sessions)
    assert rows and {(r.sandbox, r.batch_id, r.trace_id) for r in rows} == {
        (A, batch_id, "req-from-upload")
    }


async def test_a_stale_context_in_the_caller_is_replaced_by_the_batch_rows(
    sessions, models_registry, fixtures
):
    # an embedded batch task is created inside the upload request and inherits that request's ids
    harness = harness_with(sessions, models_registry, fixtures)
    batch_id, documents = await upload(harness, FIRST[:1], sandbox=A, trace_id="trace-of-the-batch")

    with bound_ids(
        sandbox="visitor-stale000000",
        batch_id="stale-batch",
        trace_id="stale-trace",
        document_id="stale-document",
        claim_id="stale-claim",
    ):
        await process_batch(harness.deps, batch_id)

    rows = await ledger_rows(sessions)
    assert rows
    for row in rows:
        assert (row.sandbox, row.batch_id, row.trace_id) == (A, batch_id, "trace-of-the-batch")
        assert row.document_id == documents["s42-0005"]
        assert row.claim_id is None
    assert not any(
        "stale" in str(v)
        for r in rows
        for v in (r.sandbox, r.batch_id, r.trace_id, r.document_id, r.claim_id)
    )


async def test_a_stale_trace_is_not_adopted_by_a_batch_that_has_none(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    batch_id, _ = await upload(harness, FIRST[:1])  # no trace_id: made before tracing existed

    with bound_ids(trace_id="stale-trace", sandbox="visitor-stale000000"):
        await process_batch(harness.deps, batch_id)

    rows = await ledger_rows(sessions)
    assert rows and {(r.sandbox, r.batch_id, r.trace_id) for r in rows} == {(None, batch_id, None)}


async def test_a_legacy_batch_without_a_trace_is_processed_and_recorded_with_null(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    batch_id, documents = await upload(harness, FIRST)
    assert await harness.repo.batch_trace_id(batch_id) is None

    await process_batch(harness.deps, batch_id)

    view = await harness.repo.get_batch(batch_id)
    assert view is not None and (view.status, view.failed) == ("done", 0)
    rows = await ledger_rows(sessions)
    assert rows and {r.trace_id for r in rows} == {None}
    assert {r.batch_id for r in rows} == {batch_id}
    assert {r.document_id for r in rows} == set(documents.values())  # the rest still works


async def test_an_unknown_batch_is_ignored_and_binds_nothing(sessions, models_registry, fixtures):
    harness = harness_with(sessions, models_registry, fixtures)

    await process_batch(harness.deps, "no-such-batch")

    assert await ledger_rows(sessions) == []


async def test_nothing_stays_bound_after_a_batch_and_none_leaks_into_the_next_call(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    batch_id, _ = await upload(harness, FIRST[:1], sandbox=A, trace_id="trace-x")
    await process_batch(harness.deps, batch_id)
    before = len(await ledger_rows(sessions))

    await harness.llm.parse(  # a call made afterwards, outside any batch
        "locate",
        system="s",
        content="x",
        output_model=LocatedFields,
    )

    rows = await ledger_rows(sessions)
    assert len(rows) == before + 1
    [outside] = [r for r in rows if r.batch_id is None]
    assert (outside.sandbox, outside.trace_id, outside.document_id, outside.claim_id) == (
        None,
        None,
        None,
        None,
    )


# --- replies and calls outside any context --------------------------------------------------------


def two_question_claim(claim_id: str = "clm-2q") -> Claim:
    return Claim(
        id=claim_id,
        employee_id="P001",
        title="Client dinner 14 Aug 2026",
        mode=ClaimMode.event,
        document_ids=[],
        total=4102.0,
        open_questions=[
            OpenQuestion(id="q-att", kind=QuestionKind.attendees, text="Who attended?"),
            OpenQuestion(id="q-why", kind=QuestionKind.business_purpose, text="Why was it?"),
        ],
    )


async def make_container(harness: Harness, sessions) -> Container:
    async def enqueue(_batch_id: str) -> None:
        return None

    return Container(
        settings=harness.deps.settings,
        sessions=sessions,
        repo=harness.repo,
        storage=InMemoryStorage(),
        events=harness.events,
        enqueue=enqueue,
        directory=harness.deps.directory,
        finance=FakeFinance(),
        calendar=StaticCalendar(),
        llm=harness.llm,
    )


async def seeded_claim(harness: Harness, sandbox: str | None, claim_id: str) -> str:
    batch_id, _ = await harness.repo.create_batch(
        "P001", [NewFile("a.png", "a" * 64, f"k/{claim_id}")], sandbox=sandbox
    )
    claim = two_question_claim(claim_id)
    await harness.repo.save_claims(batch_id, "P001", [claim], {claim.id: "x"}, sandbox=sandbox)
    return batch_id


async def test_a_reply_is_recorded_with_the_claim_its_batch_and_the_visitors_sandbox(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    container = await make_container(harness, sessions)
    asha = await harness.deps.directory.get("P001")
    assert asha is not None
    harness.llm.register(
        reply_model(("Who attended?", "Why was it?")), {"a0": "Orion: A. Rao", "a1": ""}
    )
    batch_id = await seeded_claim(harness, A, "clm-reply-1")

    out = await actions.reply(container, Persona(asha, False, A), "clm-reply-1", "Rao came")

    assert out.understood == {"q-att": "Orion: A. Rao"}
    [row] = await ledger_rows(sessions)
    assert row.route == "reply_parse"
    assert (row.claim_id, row.batch_id, row.sandbox) == ("clm-reply-1", batch_id, A)
    assert (row.document_id, row.trace_id) == (None, None)  # a reply is about no one document


async def test_a_reply_made_inside_a_request_also_carries_the_requests_trace(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    container = await make_container(harness, sessions)
    asha = await harness.deps.directory.get("P001")
    assert asha is not None
    harness.llm.register(
        reply_model(("Who attended?", "Why was it?")), {"a0": "Orion: A. Rao", "a1": ""}
    )
    batch_id = await seeded_claim(harness, None, "clm-reply-2")

    with bound_ids(trace_id="reply-request-7", request_id="reply-request-7"):
        await actions.reply(container, Persona(asha, False, None), "clm-reply-2", "Rao came")

    [row] = await ledger_rows(sessions)
    assert (row.trace_id, row.claim_id, row.batch_id, row.sandbox) == (
        "reply-request-7",
        "clm-reply-2",
        batch_id,
        None,
    )


async def test_the_claim_id_does_not_outlive_the_reply(sessions, models_registry, fixtures):
    harness = harness_with(sessions, models_registry, fixtures)
    container = await make_container(harness, sessions)
    asha = await harness.deps.directory.get("P001")
    assert asha is not None
    harness.llm.register(
        reply_model(("Who attended?", "Why was it?")), {"a0": "Orion: A. Rao", "a1": ""}
    )
    await seeded_claim(harness, A, "clm-reply-3")
    await actions.reply(container, Persona(asha, False, A), "clm-reply-3", "Rao came")
    assert current_ids() == dict.fromkeys(current_ids())

    await harness.llm.parse(
        "reply_parse",
        system="s",
        content="x",
        output_model=reply_model(("Who attended?", "Why was it?")),
    )

    after = [r for r in await ledger_rows(sessions)][-1]
    assert (after.claim_id, after.batch_id, after.sandbox) == (None, None, None)


async def test_two_replies_at_once_keep_their_own_claims(sessions, models_registry, fixtures):
    harness = harness_with(sessions, models_registry, fixtures)
    container = await make_container(harness, sessions)
    asha = await harness.deps.directory.get("P001")
    assert asha is not None
    harness.llm.register(
        reply_model(("Who attended?", "Why was it?")), {"a0": "Orion: A. Rao", "a1": ""}
    )
    batches = {
        "clm-a": await seeded_claim(harness, A, "clm-a"),
        "clm-b": await seeded_claim(harness, B, "clm-b"),
    }

    await asyncio.gather(
        actions.reply(container, Persona(asha, False, A), "clm-a", "Rao came"),
        actions.reply(container, Persona(asha, False, B), "clm-b", "Rao came"),
    )

    rows = await ledger_rows(sessions)
    assert {(r.claim_id, r.batch_id, r.sandbox) for r in rows} == {
        ("clm-a", batches["clm-a"], A),
        ("clm-b", batches["clm-b"], B),
    }


async def test_a_call_recorded_outside_any_context_stores_nulls(sessions):
    await CostLedger(sessions).record(
        CallInfo(
            route="extraction",
            model_key="haiku",
            model_id="m",
            effort=None,
            mode="fake",
            usage=TokenUsage(),
            cost_usd=0.0,
            latency_ms=1,
            stop_reason="end_turn",
            request_hash="h" * 64,
        )
    )

    [row] = await ledger_rows(sessions)
    assert (row.sandbox, row.batch_id, row.trace_id, row.document_id, row.claim_id) == (
        None,
        None,
        None,
        None,
        None,
    )


async def test_the_ledger_stores_all_five_ids_it_is_given(sessions):
    with bound_ids(
        sandbox=A, batch_id="b1", trace_id="t1", document_id="d1", claim_id="c1", request_id="r1"
    ):
        await CostLedger(sessions).record(
            CallInfo(
                route="extraction",
                model_key="haiku",
                model_id="m",
                effort=None,
                mode="fake",
                usage=TokenUsage(),
                cost_usd=0.0,
                latency_ms=1,
                stop_reason="end_turn",
                request_hash="h" * 64,
            )
        )

    [row] = await ledger_rows(sessions)
    assert (row.sandbox, row.batch_id, row.trace_id, row.document_id, row.claim_id) == (
        A,
        "b1",
        "t1",
        "d1",
        "c1",
    )


# --- repository -----------------------------------------------------------------------------------


async def test_a_batch_remembers_the_trace_it_was_created_with(sessions, models_registry, fixtures):
    harness = harness_with(sessions, models_registry, fixtures)
    with_trace, _ = await upload(harness, FIRST[:1], trace_id="req-abc-123")
    without, _ = await upload(harness, FIRST[:1])

    assert await harness.repo.batch_trace_id(with_trace) == "req-abc-123"
    assert await harness.repo.batch_trace_id(without) is None
    assert await harness.repo.batch_trace_id("no-such-batch") is None
    async with sessions() as session:
        assert (
            await session.scalar(select(Batch.trace_id).where(Batch.id == with_trace))
            == "req-abc-123"
        )
