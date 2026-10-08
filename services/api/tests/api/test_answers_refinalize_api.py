"""Answering a question re-runs policy on the claim's stored documents (so the route can change)."""

from __future__ import annotations

from datetime import date

from httpx import AsyncClient

from claimpilot.claims import build_claims
from claimpilot.domain import (
    Claim,
    Decisions,
    DocType,
    ExpenseCategory,
    ExtractedReceipt,
    ProcessedDocument,
)
from claimpilot.pipeline.finalize import with_category_questions
from claimpilot.pipeline.repo import NewFile
from claimpilot.policy import Policy

from .conftest import ASHA, World, as_persona, make_claim, seed_claim

TODAY = date(2026, 9, 30)
GATE = 0.7


def dinner(total: float, *, confidence: float = 1.0) -> ProcessedDocument:
    """A restaurant bill in Pune that was categorised as client entertainment."""
    receipt = ExtractedReceipt(
        doc_type=DocType.restaurant_bill,
        merchant_name="Test Cafe",
        merchant_city="Pune",
        date="2026-08-12",
        total=total,
    )
    decisions = Decisions(
        category=ExpenseCategory.client_entertainment,
        category_confidence=confidence,
        alcohol_present=0.0,
        personal_expense=0.0,
        engine="truth",
    )
    return ProcessedDocument(
        id="placeholder",
        filename="dinner.png",
        sha256="a" * 64,
        receipt=receipt,
        decisions=decisions,
    )


async def store(world: World, document: ProcessedDocument) -> Claim:
    """Persist the document under the id the database gives it, then the claim built from it.

    Same order as the pipeline: question ids are derived from document ids, so the claim has to be
    built from the stored document.
    """
    batch_id, [doc_id] = await world.repo.create_batch(
        ASHA.id, [NewFile(document.filename, document.sha256, "k/dinner")]
    )
    stored = document.model_copy(update={"id": doc_id})
    await world.repo.save_document(doc_id, stored, phash=None, fingerprint=None, trust=None)
    [claim] = build_claims(ASHA, [stored], Policy.load(), today=TODAY, id_prefix="clm")
    claim = with_category_questions(claim, [stored], GATE)
    await world.repo.save_claims(batch_id, ASHA.id, [claim], {claim.id: "finance_review"})
    world.container.settings.demo_today = TODAY
    return claim


def question(claim: dict, kind: str) -> dict:
    return next(q for q in claim["open_questions"] if q["kind"] == kind)


async def test_an_answer_that_changes_the_headcount_changes_the_route(
    http: AsyncClient, world: World
):
    claim = await store(world, dinner(9000.0))
    mine = as_persona(ASHA)
    stored = (await http.get(f"/v1/claims/{claim.id}", headers=mine)).json()
    attendees, purpose = question(stored, "attendees"), question(stored, "business_purpose")

    three = await http.post(
        f"/v1/claims/{claim.id}/answers",
        json={
            "answers": {
                attendees["id"]: "Rahul Mehra and Anita Rao",
                purpose["id"]: "Quarterly review",
            }
        },
        headers=mine,
    )

    assert three.status_code == 200
    body = three.json()
    assert [f["code"] for f in body["findings"]] == ["entertainment_over_cap"]  # 3,000 a head
    assert body["status"] == "ready" and body["route"] == "finance_review"

    six = await http.post(
        f"/v1/claims/{claim.id}/answers",
        json={"answers": {attendees["id"]: "Six of us from Orion Retail"}},
        headers=mine,
    )
    body = six.json()
    assert body["findings"] == []  # 1,500 a head
    assert body["status"] == "ready" and body["route"] == "auto_approve"


async def test_the_pipelines_category_question_survives_answering_another_question(
    http: AsyncClient, world: World
):
    claim = await store(world, dinner(9000.0, confidence=0.4))
    mine = as_persona(ASHA)
    stored = (await http.get(f"/v1/claims/{claim.id}", headers=mine)).json()
    assert question(stored, "other")["id"].startswith("q-category-")

    resp = await http.post(
        f"/v1/claims/{claim.id}/answers",
        json={"answers": {question(stored, "business_purpose")["id"]: "Quarterly review"}},
        headers=mine,
    )

    body = resp.json()
    category = question(body, "other")
    assert category["id"].startswith("q-category-") and category["answer"] is None
    assert body["status"] == "needs_info"

    done = await http.post(
        f"/v1/claims/{claim.id}/answers",
        json={"answers": {category["id"]: "A dinner with a client"}},
        headers=mine,
    )
    assert question(done.json(), "other")["answer"] == "A dinner with a client"


async def test_a_claim_whose_documents_are_gone_still_takes_answers(
    http: AsyncClient, world: World
):
    await seed_claim(world, make_claim())  # document_ids=["d1"] was never stored
    resp = await http.post(
        "/v1/claims/clm-1/answers",
        json={"answers": {"q-attendees": "Orion: A. Rao"}},
        headers=as_persona(ASHA),
    )
    assert resp.status_code == 200 and resp.json()["status"] == "ready"
