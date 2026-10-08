from __future__ import annotations

from datetime import date

from httpx import AsyncClient

from claimpilot.domain import Claim, ClaimMode
from claimpilot.domain.claims import OpenQuestion, QuestionKind
from claimpilot.llm.fake import FakeLLM
from claimpilot.pipeline.reply import reply_model

from .conftest import ASHA, MEERA, RAVI, World, as_persona, make_claim, seed_claim

ATTENDEES = "Who attended the client dinner?"
PURPOSE = "What was the business purpose?"


def two_question_claim() -> Claim:
    return Claim(
        id="clm-2q",
        employee_id="P001",
        title="Client dinner 14 Aug 2026",
        mode=ClaimMode.event,
        document_ids=["d1"],
        total=4102.0,
        start_date=date(2026, 8, 14),
        open_questions=[
            OpenQuestion(id="q-att", kind=QuestionKind.attendees, text=ATTENDEES),
            OpenQuestion(id="q-why", kind=QuestionKind.business_purpose, text=PURPOSE),
        ],
    )


def llm_returning(world: World, registry, **answers: str) -> FakeLLM:
    llm = FakeLLM(registry, env={})
    llm.register(reply_model((ATTENDEES, PURPOSE)), {"a0": "", "a1": "", **answers})
    world.container.llm = llm
    return llm


async def reply(http: AsyncClient, text: str, claim_id: str = "clm-1", persona=ASHA):
    return await http.post(
        f"/v1/claims/{claim_id}/reply", json={"text": text}, headers=as_persona(persona)
    )


async def test_a_single_open_question_takes_the_whole_reply_without_calling_an_llm(
    http: AsyncClient, world: World, models_registry
):
    llm = FakeLLM(models_registry, env={})
    world.container.llm = llm
    await seed_claim(world, make_claim())
    resp = await reply(http, "  Orion Retail: A. Rao and S. Nair  ")
    body = resp.json()
    assert resp.status_code == 200
    assert body["understood"] == {"q-attendees": "Orion Retail: A. Rao and S. Nair"}
    assert body["follow_up"] is None and body["claim"]["status"] == "ready"
    assert llm.requests == []  # nothing to interpret: no model call, no cost


async def test_one_reply_can_answer_several_questions(
    http: AsyncClient, world: World, models_registry
):
    llm = llm_returning(
        world, models_registry, a0="Orion Retail: A. Rao", a1="Quarterly business review"
    )
    await seed_claim(world, two_question_claim())
    body = (await reply(http, "A. Rao from Orion; it was the QBR", "clm-2q")).json()
    assert body["understood"] == {
        "q-att": "Orion Retail: A. Rao",
        "q-why": "Quarterly business review",
    }
    assert body["follow_up"] is None and body["claim"]["status"] == "ready"
    assert len(llm.requests) == 1
    sent = llm.requests[0].body["messages"][0]["content"][0]["text"]
    assert ATTENDEES in sent and "<reply>" in sent  # the reply is passed as delimited data


async def test_a_partial_reply_asks_for_the_rest_in_one_message(
    http: AsyncClient, world: World, models_registry
):
    llm_returning(world, models_registry, a0="Orion Retail: A. Rao")
    await seed_claim(world, two_question_claim())
    body = (await reply(http, "Only A. Rao from Orion came", "clm-2q")).json()
    assert list(body["understood"]) == ["q-att"]
    assert body["claim"]["status"] == "needs_info"
    assert PURPOSE in body["follow_up"] and ATTENDEES not in body["follow_up"]


async def test_an_unrelated_reply_is_asked_again(http: AsyncClient, world: World, models_registry):
    llm_returning(world, models_registry)
    await seed_claim(world, two_question_claim())
    body = (await reply(http, "thanks!", "clm-2q")).json()
    assert body["understood"] == {} and body["claim"]["status"] == "needs_info"
    assert "couldn't match" in body["follow_up"]
    assert ATTENDEES in body["follow_up"] and PURPOSE in body["follow_up"]


async def test_without_an_llm_several_questions_are_asked_again(http: AsyncClient, world: World):
    world.container.llm = None
    await seed_claim(world, two_question_claim())
    body = (await reply(http, "A. Rao; QBR", "clm-2q")).json()
    assert body["understood"] == {} and "couldn't match" in body["follow_up"]


async def test_an_llm_failure_degrades_instead_of_failing(
    http: AsyncClient, world: World, models_registry
):
    world.container.llm = FakeLLM(models_registry, env={})  # nothing registered: every call fails
    await seed_claim(world, two_question_claim())
    resp = await reply(http, "A. Rao; QBR", "clm-2q")
    assert resp.status_code == 200 and resp.json()["understood"] == {}


async def test_reply_when_nothing_is_open_and_access_rules(http: AsyncClient, world: World):
    await seed_claim(world, make_claim(answered=True))
    done = (await reply(http, "anything")).json()
    assert done["understood"] == {} and done["follow_up"] is None
    assert (await reply(http, "x", persona=RAVI)).status_code == 403
    assert (await reply(http, "x", persona=MEERA)).status_code == 404
    empty = await http.post("/v1/claims/clm-1/reply", json={"text": ""}, headers=as_persona(ASHA))
    assert empty.status_code == 422


async def test_stats_summarise_processing_cost_and_time_saved(http: AsyncClient, world: World):
    from datetime import UTC, datetime, timedelta

    from claimpilot.db import Batch, LlmCall

    batch_id = await seed_claim(world, make_claim(answered=True), route="auto_approve")
    docs = await world.repo.batch_documents(batch_id)
    async with world.container.sessions() as session:
        batch = await session.get(Batch, batch_id)
        assert batch is not None
        batch.status, batch.processed = "done", 1
        batch.created_at = datetime.now(UTC) - timedelta(seconds=30)
        batch.finished_at = datetime.now(UTC)
        session.add(
            LlmCall(
                route="extraction",
                model_key="haiku",
                model_id="m",
                mode="live",
                request_hash="h1",
                cost_usd=0.0004,
            )
        )
        session.add(  # a failed call: spent nothing useful, so it is not counted
            LlmCall(
                route="extraction",
                model_key="haiku",
                model_id="m",
                mode="live",
                request_hash="h2",
                cost_usd=0.0006,
                error="boom",
            )
        )
        await session.commit()
    await world.repo.fail_document(docs[0].id, "unreadable")

    stats = (await http.get("/v1/stats", headers=as_persona(ASHA))).json()
    assert stats["documents_failed"] == 1 and stats["documents_processed"] == 0
    assert stats["claims"] == 1 and stats["claims_by_status"] == {"ready": 1}
    assert stats["auto_approvable_claims"] == 1
    assert stats["llm_calls"] == 1 and stats["llm_cost_usd"] == 0.0004
    assert 29 <= stats["avg_batch_seconds"] <= 32
    assert stats["assumed_manual_minutes_per_document"] == 4.0
    assert stats["estimated_minutes_saved"] == 0.0  # nothing processed yet: never negative
    assert (await http.get("/v1/stats")).status_code == 401
