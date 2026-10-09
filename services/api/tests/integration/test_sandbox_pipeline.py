"""The receipt pipeline inside demo sandboxes: duplicates, claims, locks, replay and cost ledger.

Each visitor of the hosted demo uploads the same sample receipts into a private sandbox. The
pipeline must treat the sandboxes as separate worlds (a copy in another sandbox is not a
duplicate), yet ask the models exactly the same questions in every one of them, so that the
recorded answers keep hitting for every visitor. The cost ledger must know whose call each row is.
"""

# ruff: noqa: F811, F401
# The `fixtures` and `sessions` pytest fixtures come from test_process_batch; using them as test
# arguments looks like shadowing to ruff.

from __future__ import annotations

import asyncio
import hashlib
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
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
from claimpilot.db import Batch, ClaimRow, Document, LlmCall
from claimpilot.domain.claims import Claim, ClaimMode, OpenQuestion, QuestionKind
from claimpilot.extraction import prepare_document
from claimpilot.extraction.locate import LocatedFields
from claimpilot.extraction.schema import WireReceipt, from_domain
from claimpilot.llm.ledger import CostLedger
from claimpilot.llm.params import LLMRequest
from claimpilot.llm.types import CallInfo, TokenUsage
from claimpilot.pipeline import actions
from claimpilot.pipeline.process import process_batch
from claimpilot.pipeline.reply import interpret_reply, reply_model
from claimpilot.pipeline.repo import UNSCOPED, NewFile, Repository, collect_stats
from claimpilot.pipeline.views import BatchView
from claimpilot.ports import FakeFinance, StaticCalendar
from claimpilot.storage import InMemoryStorage
from claimpilot.telemetry import current_ids

pytestmark = pytest.mark.skipif(not FIXTURES.exists(), reason="synthetic fixtures not present")

A = "visitor-aaaaaaaaaaaa"
B = "visitor-bbbbbbbbbbbb"
CAB = "s42-0005"
# a pile with a tampered bill and an injection: they trigger the second-opinion route too
PILE = ("s42-0005", "s42-0082", "s42-0097", "s42-0086", "s42-0013")


def harness_with(sessions, models_registry, fixtures, *, responder=None, **settings) -> Harness:
    """The pipeline harness, with every LLM call going through a real cost ledger."""
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
    harness.llm.ledger = CostLedger(sessions)
    return harness


async def upload(
    harness: Harness, ids: Sequence[str], *, employee: str = "P001", sandbox: str | None = None
) -> str:
    news = []
    for i, fixture_id in enumerate(ids):
        raw = harness.fixtures[fixture_id].raw
        key = f"{sandbox or 'none'}/{employee}/{len(harness.storage.objects)}-{i}-{fixture_id}"
        await harness.storage.put(key, raw)
        news.append(NewFile(fixture_id, hashlib.sha256(raw).hexdigest(), key))
    batch_id, _ = await harness.repo.create_batch(employee, news, sandbox=sandbox)
    return batch_id


async def run(harness: Harness, batch_id: str, sandbox: str | None) -> BatchView:
    await process_batch(harness.deps, batch_id)
    view = await harness.repo.get_batch(batch_id, sandbox=sandbox)
    assert view is not None and view.status == "done"
    return view


async def upload_and_run(
    harness: Harness, ids: Sequence[str], *, employee: str = "P001", sandbox: str | None = None
) -> BatchView:
    return await run(
        harness, await upload(harness, ids, employee=employee, sandbox=sandbox), sandbox
    )


def duplicate_codes(view: BatchView) -> list[str]:
    [document] = view.documents
    assert document.document is not None
    return [f.code for f in document.document.findings if f.code.startswith("duplicate")]


# --- duplicates ---------------------------------------------------------------------------------


async def test_the_same_receipt_in_another_sandbox_is_not_a_duplicate(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)

    first = await upload_and_run(harness, [CAB], sandbox=A)
    other = await upload_and_run(harness, [CAB], sandbox=B)
    shared = await upload_and_run(harness, [CAB], sandbox=None)

    assert duplicate_codes(first) == duplicate_codes(other) == duplicate_codes(shared) == []


async def test_the_same_receipt_twice_in_one_sandbox_is_an_exact_duplicate(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    await upload_and_run(harness, [CAB], sandbox=A)
    await upload_and_run(harness, [CAB], sandbox=B)

    again_in_a = await upload_and_run(harness, [CAB], sandbox=A)
    again_in_b = await upload_and_run(harness, [CAB], sandbox=B)

    assert "duplicate_exact" in duplicate_codes(again_in_a)
    assert "duplicate_exact" in duplicate_codes(again_in_b)


async def test_the_sandbox_less_world_and_the_sandboxes_do_not_see_each_others_receipts(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    await upload_and_run(harness, [CAB], sandbox=None)
    assert duplicate_codes(await upload_and_run(harness, [CAB], sandbox=A)) == []
    harness2 = harness_with(sessions, models_registry, fixtures)
    assert "duplicate_exact" in duplicate_codes(await upload_and_run(harness2, [CAB], sandbox=A))
    assert "duplicate_exact" in duplicate_codes(await upload_and_run(harness2, [CAB], sandbox=None))


async def test_company_scope_matches_other_employees_inside_a_sandbox_but_not_across(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures, duplicate_scope="company")
    await upload_and_run(harness, [CAB], employee="P001", sandbox=A)

    elsewhere = await upload_and_run(harness, [CAB], employee="P005", sandbox=B)
    neighbour = await upload_and_run(harness, [CAB], employee="P005", sandbox=A)
    colleague_in_b = await upload_and_run(harness, [CAB], employee="P001", sandbox=B)

    assert duplicate_codes(elsewhere) == []
    assert "duplicate_exact" in duplicate_codes(neighbour)  # P001's copy, same sandbox
    assert "duplicate_exact" in duplicate_codes(colleague_in_b)  # P005's copy, same sandbox


async def test_employee_scope_matches_only_the_same_employee_inside_a_sandbox(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures, duplicate_scope="employee")
    await upload_and_run(harness, [CAB], employee="P001", sandbox=A)

    colleague = await upload_and_run(harness, [CAB], employee="P005", sandbox=A)
    same = await upload_and_run(harness, [CAB], employee="P001", sandbox=A)
    elsewhere = await upload_and_run(harness, [CAB], employee="P001", sandbox=B)

    assert duplicate_codes(colleague) == []
    assert "duplicate_exact" in duplicate_codes(same)
    assert duplicate_codes(elsewhere) == []


async def test_a_pile_uploaded_in_two_sandboxes_flags_nothing_in_either(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    for sandbox in (A, B):
        view = await upload_and_run(harness, PILE[:2], sandbox=sandbox)
        for document in view.documents:
            assert document.document is not None
            assert not [f for f in document.document.findings if f.code.startswith("duplicate")]


async def test_two_sandboxes_processed_at_the_same_time_never_flag_each_other(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    ids = ("s42-0002", "s42-0005", "s42-0003")
    in_a = await upload(harness, ids, sandbox=A)
    in_b = await upload(harness, ids, sandbox=B)

    await asyncio.gather(process_batch(harness.deps, in_a), process_batch(harness.deps, in_b))

    for batch_id, sandbox in ((in_a, A), (in_b, B)):
        view = await harness.repo.get_batch(batch_id, sandbox=sandbox)
        assert view is not None and view.status == "done"
        for document in view.documents:
            assert document.document is not None
            assert not [f for f in document.document.findings if f.code.startswith("duplicate")]


# --- claims ------------------------------------------------------------------------------------


async def stored_sandboxes(sessions) -> dict[str, set[str | None]]:
    async with sessions() as session:
        return {
            "batches": {r.sandbox for r in await session.scalars(select(Batch))},
            "documents": {r.sandbox for r in await session.scalars(select(Document))},
            "claims": {r.sandbox for r in await session.scalars(select(ClaimRow))},
        }


async def test_claims_formed_from_a_batch_belong_to_the_batchs_sandbox(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    view = await upload_and_run(harness, PILE[:3], sandbox=A)
    claim_ids = sorted(c.id for c in view.claims)
    assert claim_ids

    assert await stored_sandboxes(sessions) == {
        "batches": {A},
        "documents": {A},
        "claims": {A},
    }
    assert sorted(c.id for c in await harness.repo.list_claims(sandbox=A)) == claim_ids
    assert await harness.repo.list_claims(sandbox=B) == []
    assert await harness.repo.list_claims() == []  # the sandbox-less world has none of them
    assert sorted(c.id for c in await harness.repo.list_claims(sandbox=UNSCOPED)) == claim_ids


async def test_two_sandboxes_each_list_only_their_own_claims(sessions, models_registry, fixtures):
    harness = harness_with(sessions, models_registry, fixtures)
    in_a = await upload_and_run(harness, PILE[:3], sandbox=A)
    in_b = await upload_and_run(harness, PILE[:3], sandbox=B)
    in_none = await upload_and_run(harness, PILE[:3], sandbox=None)

    for sandbox, view in ((A, in_a), (B, in_b), (None, in_none)):
        listed = await harness.repo.list_claims(sandbox=sandbox)
        assert sorted(c.id for c in listed) == sorted(c.id for c in view.claims)
    assert len({c.id for v in (in_a, in_b, in_none) for c in v.claims}) == sum(
        len(v.claims) for v in (in_a, in_b, in_none)
    )  # no claim id is shared between worlds


async def test_a_retried_batch_keeps_its_claims_in_the_sandbox(sessions, models_registry, fixtures):
    harness = harness_with(sessions, models_registry, fixtures)
    batch_id = await upload(harness, PILE[:3], sandbox=A)
    before = await run(harness, batch_id, A)

    await harness.repo.mark_batch(batch_id, "queued")  # the queue delivers the job again
    after = await run(harness, batch_id, A)

    assert sorted(c.id for c in after.claims) == sorted(c.id for c in before.claims)
    assert (await stored_sandboxes(sessions))["claims"] == {A}  # the upsert kept the sandbox
    assert len(await harness.repo.list_claims(sandbox=A)) == len(before.claims)  # no extra rows
    assert await harness.repo.list_claims(sandbox=B) == []


# --- locks -------------------------------------------------------------------------------------


async def test_the_employee_lock_is_per_sandbox_and_employee(sessions, models_registry, fixtures):
    deps = harness_with(sessions, models_registry, fixtures).deps

    assert deps.lock_for("P001", A) is deps.lock_for("P001", A)
    assert deps.lock_for("P001", A) is not deps.lock_for("P001", B)
    assert deps.lock_for("P001", A) is not deps.lock_for("P001", None)
    assert deps.lock_for("P001", A) is not deps.lock_for("P005", A)
    assert deps.lock_for("P001") is deps.lock_for("P001", None)


async def test_a_visitor_never_waits_for_another_visitors_batch(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    in_a = await upload(harness, [CAB], sandbox=A)
    in_b = await upload(harness, [CAB], sandbox=B)

    async with harness.deps.lock_for("P001", A):  # Asha's checks in A are busy
        await asyncio.wait_for(run(harness, in_b, B), timeout=30)  # Asha in B is not held up
        blocked = asyncio.create_task(process_batch(harness.deps, in_a))
        done, pending = await asyncio.wait({blocked}, timeout=0.5)
        assert pending and not done  # the same employee in the same sandbox does wait
    await asyncio.wait_for(blocked, timeout=30)
    assert (await run(harness, in_a, A)).status == "done"


# --- replay: the models are asked the same thing in every world --------------------------------


async def requests_made_in(harness: Harness, sandbox: str | None) -> list[LLMRequest]:
    start = len(harness.llm.requests)
    await upload_and_run_pile(harness, sandbox)
    return harness.llm.requests[start:]


async def upload_and_run_pile(harness: Harness, sandbox: str | None) -> BatchView:
    batch_id = await upload(harness, PILE, sandbox=sandbox)
    await process_batch(harness.deps, batch_id)
    view = await harness.repo.get_batch(batch_id, sandbox=sandbox)
    assert view is not None
    return view


def by_route(requests: Sequence[LLMRequest]) -> dict[str, list[tuple[str, str]]]:
    """``route -> sorted (request_hash, body)``: order within a batch is not part of the contract
    (documents are read in parallel), what is asked is."""
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for request in requests:
        grouped[request.route].append((request.request_hash, request.canonical_json()))
    return {route: sorted(items) for route, items in grouped.items()}


async def test_the_same_documents_produce_identical_requests_in_every_world(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)

    in_a = by_route(await requests_made_in(harness, A))
    in_b = by_route(await requests_made_in(harness, B))
    in_none = by_route(await requests_made_in(harness, None))

    assert in_a == in_b == in_none  # the recordings hit for every visitor
    assert {"extraction", "locate"} <= set(in_a)
    assert len(in_a["extraction"]) == len(PILE)
    assert "extraction_retry" in in_a  # the tampered and injected bills were read twice


async def test_the_same_documents_are_asked_in_the_same_way_whoever_uploads(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    first = by_route(await requests_made_in(harness, A))
    # another employee, another sandbox, a second pass over the same files in the same sandbox
    batch_id = await upload(harness, PILE, employee="P005", sandbox=B)
    start = len(harness.llm.requests)
    await process_batch(harness.deps, batch_id)

    assert by_route(harness.llm.requests[start:]) == first
    start = len(harness.llm.requests)
    await upload_and_run_pile(harness, A)  # duplicates now: still the same questions
    assert by_route(harness.llm.requests[start:]) == first


async def test_no_request_carries_the_sandbox_or_the_batch(sessions, models_registry, fixtures):
    harness = harness_with(sessions, models_registry, fixtures)
    batches = []
    for sandbox in (A, B):
        batches.append(await upload(harness, PILE, sandbox=sandbox))
        await process_batch(harness.deps, batches[-1])

    assert harness.llm.requests
    for request in harness.llm.requests:
        body = request.canonical_json()
        assert not any(secret in body for secret in (A, B, "visitor-", *batches))


# --- cost ledger -------------------------------------------------------------------------------


async def ledger_rows(sessions) -> list[LlmCall]:
    async with sessions() as session:
        return list((await session.scalars(select(LlmCall))).all())


async def test_calls_made_for_a_batch_are_recorded_with_its_batch_and_sandbox(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    in_a = await upload(harness, PILE[:2], sandbox=A)
    await process_batch(harness.deps, in_a)
    in_none = await upload(harness, PILE[:2], sandbox=None)
    await process_batch(harness.deps, in_none)

    rows = await ledger_rows(sessions)

    assert len(rows) == len(harness.llm.requests) > 0
    assert {(r.sandbox, r.batch_id) for r in rows} == {(A, in_a), (None, in_none)}
    assert {r.route for r in rows} >= {"extraction", "locate"}  # every route, not just the first


async def test_nothing_stays_bound_once_the_batch_is_done(sessions, models_registry, fixtures):
    harness = harness_with(sessions, models_registry, fixtures)
    await process_batch(harness.deps, await upload(harness, [CAB], sandbox=A))
    assert current_ids()["sandbox"] is None and current_ids()["batch_id"] is None


async def test_overlapping_batches_in_two_sandboxes_keep_their_calls_apart(
    sessions, models_registry, fixtures
):
    by_image = {
        prepare_document(f.raw).blocks[0]["source"]["data"]: f.id for f in fixtures.values()
    }
    owner_of: dict[str, str] = {}  # request hash -> the fixture whose image it carries

    harness = harness_with(sessions, models_registry, fixtures)
    seen_at_call: list[tuple[str, dict[str, Any]]] = []
    original = harness.llm.complete

    async def slow(request: LLMRequest):
        await asyncio.sleep(0.01)  # let the other batch run in between
        body = request.canonical_json()
        owner_of[request.request_hash] = next(i for data, i in by_image.items() if data in body)
        seen_at_call.append((request.request_hash, current_ids()))
        return await original(request)

    harness.llm.complete = slow  # type: ignore[method-assign]
    mine = ("s42-0005", "s42-0006", "s42-0013")
    theirs = ("s42-0002", "s42-0003", "s42-0011")
    in_a = await upload(harness, mine, employee="P001", sandbox=A)
    in_b = await upload(harness, theirs, employee="P001", sandbox=B)

    await asyncio.gather(process_batch(harness.deps, in_a), process_batch(harness.deps, in_b))

    expected = {**{i: (A, in_a) for i in mine}, **{i: (B, in_b) for i in theirs}}
    rows = await ledger_rows(sessions)
    assert len(rows) == len(seen_at_call) >= len(mine) + len(theirs)
    for row in rows:
        assert (row.sandbox, row.batch_id) == expected[owner_of[row.request_hash]]
    for request_hash, ids in seen_at_call:  # and the ids were already right when the call began
        assert (ids["sandbox"], ids["batch_id"]) == expected[owner_of[request_hash]]
    assert {r.sandbox for r in rows} == {A, B}


def two_question_claim() -> Claim:
    return Claim(
        id="clm-2q",
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


async def test_a_reply_is_recorded_with_the_claims_batch_and_the_visitors_sandbox(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    container = await make_container(harness, sessions)
    asha = await harness.deps.directory.get("P001")
    assert asha is not None
    harness.llm.register(
        reply_model(("Who attended?", "Why was it?")), {"a0": "Orion: A. Rao", "a1": ""}
    )
    batches = {}
    for sandbox in (A, B, None):
        batch_id, _ = await harness.repo.create_batch(
            "P001", [NewFile("a.png", "a" * 64, f"k/{sandbox}")], sandbox=sandbox
        )
        claim = two_question_claim().model_copy(update={"id": f"clm-{sandbox or 'none'}"})
        await harness.repo.save_claims(batch_id, "P001", [claim], {claim.id: "x"}, sandbox=sandbox)
        batches[sandbox] = (batch_id, claim.id)

    for sandbox, (_, claim_id) in batches.items():
        out = await actions.reply(container, Persona(asha, False, sandbox), claim_id, "Rao came")
        assert out.understood == {"q-att": "Orion: A. Rao"}

    rows = await ledger_rows(sessions)
    assert {(r.route, r.sandbox, r.batch_id) for r in rows} == {
        ("reply_parse", sandbox, batch_id) for sandbox, (batch_id, _) in batches.items()
    }
    assert len({r.request_hash for r in rows}) == 1  # one recording serves every visitor


async def test_a_call_outside_any_batch_is_recorded_without_ids(
    sessions, models_registry, fixtures
):
    harness = harness_with(sessions, models_registry, fixtures)
    harness.llm.register(
        reply_model(("Who attended?", "Why was it?")), {"a0": "Orion: A. Rao", "a1": ""}
    )

    understood = await interpret_reply(harness.llm, two_question_claim(), "Rao came")
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

    assert understood == {"q-att": "Orion: A. Rao"}
    rows = await ledger_rows(sessions)
    assert len(rows) == 2
    assert {(r.sandbox, r.batch_id) for r in rows} == {(None, None)}


async def test_a_failed_call_is_attributed_too(sessions, models_registry, fixtures):
    def unreadable(_request):
        from claimpilot.llm.errors import LLMError

        raise LLMError("unavailable")

    harness = harness_with(sessions, models_registry, fixtures, responder=unreadable)
    batch_id = await upload(harness, [CAB], sandbox=A)

    await process_batch(harness.deps, batch_id)

    errors = [r for r in await ledger_rows(sessions) if r.error]
    assert errors and {(r.sandbox, r.batch_id) for r in errors} == {(A, batch_id)}


# --- the impact meter ----------------------------------------------------------------------------


def call_at(when: datetime, cost: float, sandbox: str | None, *, error: str | None = None):
    return LlmCall(
        created_at=when,
        route="extraction",
        model_key="haiku",
        model_id="m",
        mode="live",
        request_hash="h",
        cost_usd=cost,
        error=error,
        sandbox=sandbox,
    )


async def batch_created_at(repo: Repository, sessions, sandbox: str | None, when: datetime) -> str:
    batch_id, _ = await repo.create_batch(
        "P001", [NewFile("a.png", "a" * 64, f"k/{when}")], sandbox=sandbox
    )
    async with sessions() as session:
        batch = await session.get(Batch, batch_id)
        assert batch is not None
        batch.created_at = when
        await session.commit()
    return batch_id


async def test_stats_cost_counts_a_sandboxs_calls_since_its_oldest_batch(sessions):
    repo = Repository(sessions)
    t0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    minute = timedelta(minutes=1)
    await batch_created_at(repo, sessions, A, t0)
    await batch_created_at(repo, sessions, A, t0 + 3 * minute)  # the oldest one counts
    await batch_created_at(repo, sessions, B, t0 - 10 * minute)
    await batch_created_at(repo, sessions, None, t0 + 5 * minute)
    async with sessions() as session:
        session.add_all(
            [
                call_at(t0 - minute, 100.0, A),  # before A's oldest batch: an earlier session
                call_at(t0 + minute, 0.01, A),  # between A's two batches: still A's
                call_at(t0 + 4 * minute, 0.02, A),
                call_at(t0 + 4 * minute, 7.0, A, error="boom"),  # failed: never counted
                call_at(t0 + minute, 0.5, B),  # B's batch is older, so B counts this ...
                call_at(t0 - 20 * minute, 80.0, B),  # ... but not this
                call_at(t0 + 6 * minute, 0.25, None),
                call_at(t0 + 4 * minute, 3.0, "visitor-cccccccccccc"),  # nobody's batch here
            ]
        )
        await session.commit()

    in_a = await collect_stats(sessions, sandbox=A)
    in_b = await collect_stats(sessions, sandbox=B)
    shared = await collect_stats(sessions)
    nobody = await collect_stats(sessions, sandbox="visitor-cccccccccccc")

    assert (in_a["llm_calls"], round(in_a["llm_cost_usd"], 6)) == (2, 0.03)
    assert (in_b["llm_calls"], round(in_b["llm_cost_usd"], 6)) == (1, 0.5)
    assert (shared["llm_calls"], round(shared["llm_cost_usd"], 6)) == (1, 0.25)
    assert (nobody["llm_calls"], nobody["llm_cost_usd"]) == (0, 0.0)  # no batch: no cost either


async def test_stats_cost_vanishes_with_the_sandboxs_batches_but_the_ledger_stays(sessions):
    repo = Repository(sessions)
    t0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    await batch_created_at(repo, sessions, A, t0)
    await batch_created_at(repo, sessions, B, t0)
    async with sessions() as session:
        session.add_all([call_at(t0 + timedelta(minutes=1), 0.4, s) for s in (A, B)])
        await session.commit()

    await repo.delete_data(sandbox=A)  # a visitor starts over

    assert (await collect_stats(sessions, sandbox=A))["llm_cost_usd"] == 0.0
    assert (await collect_stats(sessions, sandbox=B))["llm_cost_usd"] == pytest.approx(0.4)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(LlmCall)) == 2
