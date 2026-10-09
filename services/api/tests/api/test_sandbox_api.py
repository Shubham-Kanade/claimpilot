"""Per-visitor demo sandboxes at the HTTP boundary (ADR-034): one visitor never sees another's data.

The header ``X-Sandbox`` selects a private copy of the data, but only in demo mode; everywhere
else it is ignored and every request lives in the one shared world (sandbox ``None``, which means
"rows without a sandbox", never "all rows").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select

from claimpilot.claims import refresh_status
from claimpilot.db import Batch, Document, LlmCall
from claimpilot.domain import Claim, ClaimStatus
from claimpilot.domain.claims import Employee
from claimpilot.pipeline.events import BatchDone
from claimpilot.pipeline.repo import NewFile

from .conftest import ASHA, MEERA, PNG, RAVI, World, as_persona, make_claim

A = "visitor-aaaaaaaaaaaa"  # a sandbox id as the web app generates it: 16 to 64 of [A-Za-z0-9_-]
B = "visitor-bbbbbbbbbbbb"


def in_sandbox_headers(persona: Employee, sandbox: str | None) -> dict[str, str]:
    """Both headers a demo visitor sends (``None``: the sandbox-less world, so persona only)."""
    return {**as_persona(persona), **({"X-Sandbox": sandbox} if sandbox else {})}


def demo_on(world: World) -> None:
    world.container.settings.demo_mode = True


async def seed(
    world: World, claim: Claim, sandbox: str | None, *, route: str = "finance_review"
) -> tuple[str, str]:
    """A batch (one stored file), and the claim in it, in this sandbox: ``(batch_id, doc_id)``."""
    key = f"{claim.id}/a.png"
    await world.container.storage.put(key, PNG)
    batch_id, (doc_id,) = await world.repo.create_batch(
        claim.employee_id, [NewFile("a.png", "a" * 64, key)], sandbox=sandbox
    )
    await world.repo.save_claims(
        batch_id, claim.employee_id, [refresh_status(claim)], {claim.id: route}, sandbox=sandbox
    )
    return batch_id, doc_id


def submitted(claim_id: str, **fields: Any) -> Claim:
    return make_claim(claim_id, answered=True, **fields).model_copy(
        update={"status": ClaimStatus.submitted, "submission_reference": "FIN-2026-000001"}
    )


def ids_of(listing: Any) -> list[str]:
    return sorted(c["id"] for c in listing.json())


def upload_png(http: AsyncClient, persona: Employee, sandbox: str | None):
    return http.post(
        "/v1/batches",
        files=[("files", ("a.png", PNG, "image/png"))],
        headers=in_sandbox_headers(persona, sandbox),
    )


# --- every route that names a batch, a document or a claim ----------------------------------------


@dataclass(frozen=True)
class Case:
    """How to call one route, and the state its data must be in for the owner's call to succeed."""

    method: str
    url: str
    not_found: str  # the problem type of the 404
    claim_state: str = "open"  # open (a question is unanswered) | ready | submitted
    body: dict[str, Any] | None = None
    actor: Employee = ASHA  # who may call it successfully (the owner; the approver for decisions)


ANSWER = {"answers": {"q-attendees": "Orion Retail: A. Rao, S. Nair; quarterly review"}}
BATCH = ("batch_not_found", "/v1/batches/{batch_id}")
DOC = ("document_not_found", "/v1/documents/{document_id}")
CLAIM = ("claim_not_found", "/v1/claims/{claim_id}")

CASES: dict[str, Case] = {
    "GET /v1/batches/{batch_id}": Case("GET", BATCH[1], BATCH[0]),
    "GET /v1/batches/{batch_id}/history": Case("GET", BATCH[1] + "/history", BATCH[0]),
    "GET /v1/batches/{batch_id}/events": Case("GET", BATCH[1] + "/events", BATCH[0]),
    "GET /v1/documents/{document_id}": Case("GET", DOC[1], DOC[0]),
    "GET /v1/documents/{document_id}/file": Case("GET", DOC[1] + "/file", DOC[0]),
    "GET /v1/claims/{claim_id}": Case("GET", CLAIM[1], CLAIM[0]),
    "GET /v1/claims/{claim_id}/prompt": Case("GET", CLAIM[1] + "/prompt", CLAIM[0]),
    "POST /v1/claims/{claim_id}/answers": Case(
        "POST", CLAIM[1] + "/answers", CLAIM[0], body=ANSWER
    ),
    "POST /v1/claims/{claim_id}/reply": Case(
        "POST", CLAIM[1] + "/reply", CLAIM[0], body={"text": "Orion Retail: A. Rao and S. Nair"}
    ),
    "POST /v1/claims/{claim_id}/submit": Case(
        "POST", CLAIM[1] + "/submit", CLAIM[0], "ready", {"confirmed": True}
    ),
    "POST /v1/claims/{claim_id}/decision": Case(
        "POST",
        CLAIM[1] + "/decision",
        CLAIM[0],
        "submitted",
        {"approved": True, "comment": "ok"},
        actor=RAVI,
    ),
}

# Routes with a path parameter that carry no sandboxed data. Empty on purpose: a new route must be
# added to CASES (and so be proven sandbox-safe), not waved through.
EXEMPT: set[str] = set()


def routes_with_path_parameters(app: FastAPI) -> set[str]:
    """Read from the OpenAPI document: ``app.routes`` only holds lazy router wrappers."""
    return {
        f"{method.upper()} {path}"
        for path, operations in app.openapi()["paths"].items()
        if "{" in path
        for method in operations
    }


def test_every_route_with_an_id_in_its_path_is_covered_by_the_isolation_test(app: FastAPI):
    found = routes_with_path_parameters(app) - EXEMPT
    assert found - set(CASES) == set(), "new route(s): add them to CASES in test_sandbox_api.py"
    assert set(CASES) - found == set(), "CASES lists route(s) that no longer exist"


async def arrange(world: World, http: AsyncClient, case: Case, sandbox: str | None) -> dict:
    """A claim in the wanted state in ``sandbox``, owned by Asha; returns the path parameters."""
    claim = make_claim("clm-x", answered=case.claim_state != "open")
    batch_id, document_id = await seed(world, claim, sandbox)
    await world.container.events.publish(  # so the event stream has an ending
        BatchDone(batch_id=batch_id, processed=1, failed=0, claims=1, cost_usd=0.0)
    )
    if case.claim_state == "submitted":
        resp = await http.post(
            "/v1/claims/clm-x/submit",
            json={"confirmed": True},
            headers=in_sandbox_headers(ASHA, sandbox),
        )
        assert resp.status_code == 200
    return {"batch_id": batch_id, "document_id": document_id, "claim_id": "clm-x"}


async def snapshot(world: World, claim_id: str, sandbox: str | None) -> dict:
    """Everything an attempted write could have changed."""
    claim = await world.repo.get_claim(claim_id, sandbox=sandbox)
    return {
        "claim": claim.model_dump(mode="json") if claim else None,
        "finance": sorted(world.finance.submitted),
        "decisions": list(world.finance.decisions),
        "audit": [e.action for e in await world.repo.audit_trail(claim_id)],
    }


@pytest.mark.parametrize("data_sandbox", [A, None], ids=["data-in-A", "data-in-shared-world"])
@pytest.mark.parametrize("key", sorted(CASES))
async def test_a_route_never_reaches_data_of_another_sandbox(
    key: str, data_sandbox: str | None, http: AsyncClient, world: World
):
    demo_on(world)
    case = CASES[key]
    ids = await arrange(world, http, case, data_sandbox)
    url = case.url.format(**ids)
    before = await snapshot(world, ids["claim_id"], data_sandbox)
    viewers = [ASHA, RAVI] if case.actor is ASHA else [RAVI]  # employees cannot decide at all

    for sandbox in (s for s in (A, B, None) if s != data_sandbox):
        for persona in viewers:  # the owner and an approver: neither crosses over
            resp = await http.request(
                case.method, url, json=case.body, headers=in_sandbox_headers(persona, sandbox)
            )
            where = f"{persona.id} in {sandbox}"
            assert resp.status_code == 404, f"{key}: {where} got {resp.status_code} {resp.text}"
            assert resp.json()["type"] == case.not_found, f"{key}: {where}"
    assert await snapshot(world, ids["claim_id"], data_sandbox) == before  # nothing was touched

    own = await http.request(
        case.method, url, json=case.body, headers=in_sandbox_headers(case.actor, data_sandbox)
    )
    assert own.status_code == 200, f"{key}: the owning sandbox got {own.status_code} {own.text}"


async def test_demo_off_ignores_the_header_so_a_sandbox_cannot_be_asked_for(
    http: AsyncClient, world: World
):
    await seed(world, make_claim("clm-a"), A)  # (planted directly: demo mode is off)
    asked = await http.get("/v1/claims/clm-a", headers=in_sandbox_headers(ASHA, A))
    assert asked.status_code == 404  # the header meant nothing: this is the sandbox-less world
    assert (await http.get("/v1/claims", headers=in_sandbox_headers(ASHA, A))).json() == []


# --- lists -------------------------------------------------------------------------------------


async def test_lists_and_approvals_hold_only_the_callers_sandbox(http: AsyncClient, world: World):
    demo_on(world)
    for sandbox, name in ((A, "a"), (B, "b"), (None, "n")):
        await seed(world, submitted(f"clm-{name}"), sandbox)
    await seed(world, make_claim("clm-a-open"), A)

    expected = {A: ["clm-a", "clm-a-open"], B: ["clm-b"], None: ["clm-n"]}
    for sandbox, ids in expected.items():
        for persona in (ASHA, RAVI):
            listing = await http.get("/v1/claims", headers=in_sandbox_headers(persona, sandbox))
            assert ids_of(listing) == ids, f"{persona.id} in {sandbox}"
        queue = await http.get("/v1/approvals", headers=in_sandbox_headers(RAVI, sandbox))
        assert ids_of(queue) == [i for i in ids if i != "clm-a-open"]


async def test_an_empty_sandbox_sees_nothing_while_another_has_data(
    http: AsyncClient, world: World
):
    demo_on(world)
    await seed(world, submitted("clm-a"), A)
    for sandbox in (B, None):
        listing = await http.get("/v1/claims", headers=in_sandbox_headers(ASHA, sandbox))
        assert listing.json() == []
        approvals = await http.get("/v1/approvals", headers=in_sandbox_headers(RAVI, sandbox))
        assert approvals.json() == []


async def test_the_sandbox_less_world_does_not_see_sandboxes_either(
    http: AsyncClient, world: World
):
    demo_on(world)
    await seed(world, submitted("clm-n"), None)
    for sandbox in (A, B):
        listing = await http.get("/v1/claims", headers=in_sandbox_headers(RAVI, sandbox))
        assert listing.json() == []  # None means "without a sandbox", not "everything"


async def test_filters_and_the_approvers_employee_filter_stay_inside_the_sandbox(
    http: AsyncClient, world: World
):
    demo_on(world)
    await seed(world, make_claim("clm-asha-a", employee_id="P001"), A)
    await seed(world, make_claim("clm-meera-a", employee_id="P002"), A)
    await seed(world, make_claim("clm-asha-b", employee_id="P001"), B)

    mine = await http.get("/v1/claims?employee_id=P001", headers=in_sandbox_headers(RAVI, A))
    assert ids_of(mine) == ["clm-asha-a"]
    by_status = await http.get("/v1/claims?status=needs_info", headers=in_sandbox_headers(RAVI, B))
    assert ids_of(by_status) == ["clm-asha-b"]
    by_route = await http.get("/v1/claims?route=auto_approve", headers=in_sandbox_headers(RAVI, B))
    assert by_route.json() == []


async def test_the_same_employee_has_an_independent_list_in_each_sandbox(
    http: AsyncClient, world: World
):
    demo_on(world)
    await seed(world, make_claim("clm-a"), A)
    await seed(world, make_claim("clm-b1"), B)
    await seed(world, make_claim("clm-b2"), B)

    assert ids_of(await http.get("/v1/claims", headers=in_sandbox_headers(ASHA, A))) == ["clm-a"]
    in_b = await http.get("/v1/claims", headers=in_sandbox_headers(ASHA, B))
    assert ids_of(in_b) == ["clm-b1", "clm-b2"]
    # what Asha does in one sandbox does not show in the other
    done = await http.post(
        "/v1/claims/clm-a/answers", json=ANSWER, headers=in_sandbox_headers(ASHA, A)
    )
    assert done.json()["status"] == "ready"
    in_b = await http.get("/v1/claims", headers=in_sandbox_headers(ASHA, B))
    assert {c["status"] for c in in_b.json()} == {"needs_info"}


async def test_an_upload_lands_in_the_senders_sandbox_only(http: AsyncClient, world: World):
    demo_on(world)
    resp = await upload_png(http, ASHA, A)
    batch_id, doc_id = resp.json()["batch_id"], resp.json()["documents"][0]["id"]

    assert await world.repo.batch_sandbox(batch_id) == A
    row = await world.repo.get_document(doc_id, sandbox=A)
    assert row is not None and row.sandbox == A
    url = f"/v1/batches/{batch_id}"
    assert (await http.get(url, headers=in_sandbox_headers(ASHA, A))).status_code == 200
    for sandbox in (B, None):
        other = await http.get(url, headers=in_sandbox_headers(ASHA, sandbox))
        assert other.status_code == 404


# --- the header ------------------------------------------------------------------------------


async def test_without_the_header_the_caller_is_in_the_shared_world(
    http: AsyncClient, world: World
):
    demo_on(world)
    resp = await upload_png(http, ASHA, None)
    batch_id = resp.json()["batch_id"]
    assert await world.repo.batch_sandbox(batch_id) is None
    assert await world.repo.get_batch(batch_id, sandbox=None) is not None
    assert await world.repo.get_batch(batch_id, sandbox=A) is None


async def test_outside_the_demo_the_header_is_ignored_even_when_malformed(
    http: AsyncClient, world: World
):
    for sent in (A, "short", "x" * 65, "has spaces and !!"):
        resp = await http.post(
            "/v1/batches",
            files=[("files", ("a.png", PNG, "image/png"))],
            headers={**as_persona(ASHA), "X-Sandbox": sent},
        )
        assert resp.status_code == 202, sent
        assert await world.repo.batch_sandbox(resp.json()["batch_id"]) is None  # shared world
    listing = await http.get("/v1/claims", headers={**as_persona(ASHA), "X-Sandbox": "nope"})
    assert listing.status_code == 200


@pytest.mark.parametrize(
    "sent",
    ["a" * 15, "x" * 65, "visitor aaaaaaaaaaaa", "visitor.aaaaaaaaaaaa", "../" * 8, "*" * 20],
    ids=["too-short", "too-long", "space", "dot", "dots-and-slashes", "star"],
)
async def test_a_malformed_sandbox_is_rejected_in_the_demo(
    sent: str, http: AsyncClient, world: World
):
    demo_on(world)
    headers = {**as_persona(ASHA), "X-Sandbox": sent}
    resp = await http.get("/v1/claims", headers=headers)
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["type"] == "invalid_sandbox"
    upload = await http.post(  # on a write path too: nothing is stored or queued
        "/v1/batches", files=[("files", ("a.png", PNG, "image/png"))], headers=headers
    )
    assert upload.status_code == 400 and world.enqueued == []


@pytest.mark.parametrize(
    "sent", ["a" * 16, "A" * 64, "0123456789-_abcdEFGH"], ids=["16", "64", "mixed"]
)
async def test_ids_at_the_edges_of_the_allowed_shape_are_accepted(
    sent: str, http: AsyncClient, world: World
):
    demo_on(world)
    resp = await http.get("/v1/claims", headers={**as_persona(ASHA), "X-Sandbox": sent})
    assert resp.status_code == 200


async def test_an_empty_header_means_no_sandbox(http: AsyncClient, world: World):
    demo_on(world)
    await seed(world, make_claim("clm-n"), None)
    resp = await http.get("/v1/claims", headers={**as_persona(ASHA), "X-Sandbox": ""})
    assert ids_of(resp) == ["clm-n"]


async def test_the_persona_is_checked_before_the_sandbox(http: AsyncClient, world: World):
    demo_on(world)
    resp = await http.get("/v1/claims", headers={"X-Persona": "NOBODY", "X-Sandbox": "bad"})
    assert resp.status_code == 401


# --- start over --------------------------------------------------------------------------------


async def populate(world: World) -> None:
    """Asha and Meera each have a batch, a document and a claim in A, in B and in no sandbox."""
    for sandbox, tag in ((A, "a"), (B, "b"), (None, "n")):
        for employee, who in (("P001", "asha"), ("P002", "meera")):
            await seed(world, make_claim(f"clm-{who}-{tag}", employee_id=employee), sandbox)


def stored_files(world: World) -> set[str]:
    return set(world.container.storage.objects)  # type: ignore[attr-defined]


async def surviving_claims(world: World) -> set[str]:
    rows: set[str] = set()
    for sandbox in (A, B, None):
        rows |= {c.id for c in await world.repo.list_claims(sandbox=sandbox)}
    return rows


async def test_an_employee_starts_over_in_their_own_sandbox_only(http: AsyncClient, world: World):
    demo_on(world)
    await populate(world)

    resp = await http.post("/v1/demo/reset", headers=in_sandbox_headers(ASHA, A))

    assert resp.status_code == 200
    assert resp.json() == {"batches": 1, "documents": 1, "claims": 1}
    assert await surviving_claims(world) == {
        "clm-meera-a",  # another employee, same sandbox
        "clm-asha-b",
        "clm-meera-b",  # the same employee, another sandbox
        "clm-asha-n",
        "clm-meera-n",  # the shared world
    }
    assert stored_files(world) == {
        f"clm-{who}-{tag}/a.png"
        for who, tag in (
            ("meera", "a"),
            ("asha", "b"),
            ("meera", "b"),
            ("asha", "n"),
            ("meera", "n"),
        )
    }


async def test_the_approver_empties_the_whole_sandbox_and_nothing_else(
    http: AsyncClient, world: World
):
    demo_on(world)
    await populate(world)

    resp = await http.post("/v1/demo/reset", headers=in_sandbox_headers(RAVI, B))

    assert resp.json() == {"batches": 2, "documents": 2, "claims": 2}
    assert await surviving_claims(world) == {
        "clm-asha-a",
        "clm-meera-a",
        "clm-asha-n",
        "clm-meera-n",
    }
    assert not {k for k in stored_files(world) if "-b/" in k} and len(stored_files(world)) == 4


async def test_without_a_sandbox_the_reset_reaches_only_the_shared_world(
    http: AsyncClient, world: World
):
    demo_on(world)
    await populate(world)

    resp = await http.post("/v1/demo/reset", headers=as_persona(RAVI))

    assert resp.json()["claims"] == 2
    assert await surviving_claims(world) == {
        "clm-asha-a",
        "clm-meera-a",
        "clm-asha-b",
        "clm-meera-b",
    }
    assert not {k for k in stored_files(world) if "-n/" in k} and len(stored_files(world)) == 4


async def test_the_reset_audit_row_says_whole_sandbox(http: AsyncClient, world: World):
    demo_on(world)
    await populate(world)
    await http.post("/v1/demo/reset", headers=in_sandbox_headers(ASHA, A))
    await http.post("/v1/demo/reset", headers=in_sandbox_headers(RAVI, B))

    [mine] = await world.repo.audit_trail("P001")
    [approver] = await world.repo.audit_trail("DEMO-RAVI")
    assert mine.action == approver.action == "demo_reset"
    assert mine.detail == {"whole_sandbox": False, "documents": 1, "claims": 1}
    assert approver.detail == {"whole_sandbox": True, "documents": 2, "claims": 2}


async def test_a_reset_keeps_the_audit_trail_and_ledger_of_other_sandboxes(
    http: AsyncClient, world: World
):
    demo_on(world)
    await seed(world, make_claim("clm-a"), A)
    await seed(world, make_claim("clm-b"), B)
    answered = await http.post(
        "/v1/claims/clm-b/answers", json=ANSWER, headers=in_sandbox_headers(ASHA, B)
    )
    assert answered.status_code == 200
    async with world.container.sessions() as session:
        for sandbox in (A, B):
            session.add(call(0.01, sandbox))
        await session.commit()

    await http.post("/v1/demo/reset", headers=in_sandbox_headers(RAVI, A))

    assert [e.action for e in await world.repo.audit_trail("clm-b")] == ["claim_answered"]
    assert await world.repo.get_claim("clm-b", sandbox=B) is not None
    async with world.container.sessions() as session:  # the ledger is never reset
        kept = await session.scalar(select(func.count()).select_from(LlmCall))
    assert kept == 2


async def test_a_visitor_can_start_over_and_upload_again_without_touching_others(
    http: AsyncClient, world: World
):
    demo_on(world)
    await seed(world, make_claim("clm-b"), B)
    for _ in range(2):
        assert (await upload_png(http, ASHA, A)).status_code == 202
        reset = await http.post("/v1/demo/reset", headers=in_sandbox_headers(ASHA, A))
        assert reset.json() == {"batches": 1, "documents": 1, "claims": 0}
    assert ids_of(await http.get("/v1/claims", headers=in_sandbox_headers(ASHA, B))) == ["clm-b"]


# --- stats -------------------------------------------------------------------------------------


def call(cost: float, sandbox: str | None, *, error: str | None = None) -> LlmCall:
    return LlmCall(
        route="extraction",
        model_key="haiku",
        model_id="m",
        mode="live",
        request_hash="h",
        cost_usd=cost,
        error=error,
        sandbox=sandbox,
    )


async def seed_stats(world: World) -> None:
    """A: 2 documents (1 read, 1 failed), 2 claims (1 auto-approvable), 2 calls and 1 failed call.
    B: 1 read document, 1 claim, 1 call. The shared world: 1 read document, 1 auto claim, 1 call."""
    plan = (
        (A, ("a1", "a2"), ("auto_approve", "finance_review"), (0.01, 0.02), 1),
        (B, ("b1",), ("finance_review",), (0.5,), 0),
        (None, ("n1",), ("auto_approve",), (0.25,), 0),
    )
    for sandbox, docs, routes, costs, failed in plan:
        batch_id, doc_ids = await world.repo.create_batch(
            "P001", [NewFile(f"{d}.png", "a" * 64, f"k/{d}.png") for d in docs], sandbox=sandbox
        )
        for doc_id in doc_ids[:failed]:
            await world.repo.fail_document(doc_id, "unreadable")
        async with world.container.sessions() as session:
            for doc_id in doc_ids[failed:]:
                row = await session.get(Document, doc_id)
                assert row is not None
                row.status = "processed"
            batch = await session.get(Batch, batch_id)
            assert batch is not None
            batch.created_at = datetime.now(UTC) - timedelta(minutes=5)  # before its calls
            for cost in costs:
                session.add(call(cost, sandbox))
            if sandbox == A:
                session.add(call(9.0, A, error="boom"))  # a failed call is not counted
            await session.commit()
        for n, route in enumerate(routes):
            claim = make_claim(f"clm-{docs[0]}-{n}", answered=True)
            await world.repo.save_claims(
                batch_id, "P001", [refresh_status(claim)], {claim.id: route}, sandbox=sandbox
            )


async def test_stats_count_each_sandbox_separately(http: AsyncClient, world: World):
    demo_on(world)
    await seed_stats(world)

    stats = {}
    for sandbox in (A, B, None):
        resp = await http.get("/v1/stats", headers=in_sandbox_headers(ASHA, sandbox))
        assert resp.status_code == 200
        stats[sandbox] = resp.json()

    def counted(body: dict) -> tuple:
        return (
            body["documents_processed"],
            body["documents_failed"],
            body["claims"],
            body["auto_approvable_claims"],
            body["llm_calls"],
            round(body["llm_cost_usd"], 6),
        )

    assert counted(stats[A]) == (1, 1, 2, 1, 2, 0.03)
    assert counted(stats[B]) == (1, 0, 1, 0, 1, 0.5)
    assert counted(stats[None]) == (1, 0, 1, 1, 1, 0.25)
    assert stats[A]["claims_by_status"] == {"ready": 2}
    assert stats[A]["llm_cost_per_document_usd"] == pytest.approx(0.03)
    assert stats[B]["llm_cost_per_document_usd"] == pytest.approx(0.5)


async def test_stats_of_an_unknown_sandbox_are_zero(http: AsyncClient, world: World):
    demo_on(world)
    await seed_stats(world)
    headers = in_sandbox_headers(MEERA, "visitor-cccccccccccc")
    body = (await http.get("/v1/stats", headers=headers)).json()
    assert (body["documents_processed"], body["claims"], body["llm_calls"]) == (0, 0, 0)
    assert body["llm_cost_usd"] == 0 and body["llm_cost_per_document_usd"] is None


# --- CORS --------------------------------------------------------------------------------------


async def test_preflight_allows_the_sandbox_header(http: AsyncClient):
    resp = await http.options(
        "/v1/claims",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-persona, x-sandbox",
        },
    )
    assert resp.status_code == 200
    assert "x-sandbox" in resp.headers["access-control-allow-headers"].lower()
