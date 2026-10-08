from __future__ import annotations

from httpx import AsyncClient

from claimpilot.domain import ClaimStatus

from .conftest import ASHA, MEERA, RAVI, World, as_persona, make_claim, seed_claim


async def test_list_and_get_claims_are_scoped_to_the_persona(http: AsyncClient, world: World):
    await seed_claim(world, make_claim("clm-asha", employee_id="P001"))
    await seed_claim(world, make_claim("clm-meera", employee_id="P002"))

    mine = (await http.get("/v1/claims", headers=as_persona(ASHA))).json()
    assert [c["id"] for c in mine] == ["clm-asha"]
    everyone = (await http.get("/v1/claims", headers=as_persona(RAVI))).json()
    assert {c["id"] for c in everyone} == {"clm-asha", "clm-meera"}
    only_meera = await http.get("/v1/claims?employee_id=P002", headers=as_persona(RAVI))
    assert [c["id"] for c in only_meera.json()] == ["clm-meera"]
    # a non-approver cannot widen the view with the employee_id filter
    sneaky = await http.get("/v1/claims?employee_id=P002", headers=as_persona(ASHA))
    assert [c["id"] for c in sneaky.json()] == ["clm-asha"]

    assert (await http.get("/v1/claims/clm-asha", headers=as_persona(ASHA))).status_code == 200
    other = await http.get("/v1/claims/clm-meera", headers=as_persona(ASHA))
    assert other.status_code == 404 and other.json()["type"] == "claim_not_found"


async def test_status_and_route_filters(http: AsyncClient, world: World):
    await seed_claim(world, make_claim("clm-a"), route="finance_review")
    await seed_claim(world, make_claim("clm-b", answered=True), route="auto_approve")
    needs = await http.get("/v1/claims?status=needs_info", headers=as_persona(ASHA))
    assert [c["id"] for c in needs.json()] == ["clm-a"]
    auto = await http.get("/v1/claims?route=auto_approve", headers=as_persona(ASHA))
    assert [c["id"] for c in auto.json()] == ["clm-b"]


async def test_prompt_is_one_combined_message(http: AsyncClient, world: World):
    await seed_claim(world, make_claim())
    body = (await http.get("/v1/claims/clm-1/prompt", headers=as_persona(ASHA))).json()
    assert body["open_question_ids"] == ["q-attendees"]
    assert "Who attended the client dinner?" in body["prompt"]

    await http.post(
        "/v1/claims/clm-1/answers",
        json={"answers": {"q-attendees": "Orion: A. Rao"}},
        headers=as_persona(ASHA),
    )
    done = (await http.get("/v1/claims/clm-1/prompt", headers=as_persona(ASHA))).json()
    assert done == {"prompt": None, "open_question_ids": []}


async def test_answering_moves_the_claim_to_ready(http: AsyncClient, world: World):
    await seed_claim(world, make_claim())
    before = (await http.get("/v1/claims/clm-1", headers=as_persona(ASHA))).json()
    assert before["status"] == "needs_info"

    resp = await http.post(
        "/v1/claims/clm-1/answers",
        json={"answers": {"q-attendees": "  Orion Retail: A. Rao  "}},
        headers=as_persona(ASHA),
    )
    assert resp.status_code == 200
    claim = resp.json()
    # small, complete, nothing flagged: no human review needed
    assert claim["status"] == "ready" and claim["route"] == "auto_approve"
    assert claim["open_questions"][0]["answer"] == "Orion Retail: A. Rao"
    assert [e.action for e in await world.repo.audit_trail("clm-1")] == ["claim_answered"]


async def test_answer_errors(http: AsyncClient, world: World):
    await seed_claim(world, make_claim())
    unknown = await http.post(
        "/v1/claims/clm-1/answers", json={"answers": {"nope": "x"}}, headers=as_persona(ASHA)
    )
    assert unknown.status_code == 422 and unknown.json()["type"] == "unknown_question"
    blank = await http.post(
        "/v1/claims/clm-1/answers",
        json={"answers": {"q-attendees": "   "}},
        headers=as_persona(ASHA),
    )
    assert blank.status_code == 422 and blank.json()["type"] == "blank_answer"
    # approvers can look, but only the owner may answer
    foreign = await http.post(
        "/v1/claims/clm-1/answers", json={"answers": {"q-attendees": "x"}}, headers=as_persona(RAVI)
    )
    assert foreign.status_code == 403 and foreign.json()["type"] == "not_your_claim"
    other = await http.post(
        "/v1/claims/clm-1/answers",
        json={"answers": {"q-attendees": "x"}},
        headers=as_persona(MEERA),
    )
    assert other.status_code == 404


async def test_cannot_submit_a_claim_that_still_has_open_questions(http: AsyncClient, world: World):
    await seed_claim(world, make_claim())
    resp = await http.post(
        "/v1/claims/clm-1/submit", json={"confirmed": True}, headers=as_persona(ASHA)
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["type"] == "claim_not_ready"
    assert body["detail"] == {"status": "needs_info", "unanswered": ["q-attendees"]}
    assert world.finance.submitted == {}  # nothing reached the finance system


async def test_submission_needs_explicit_confirmation(http: AsyncClient, world: World):
    await seed_claim(world, make_claim(answered=True))
    resp = await http.post(
        "/v1/claims/clm-1/submit", json={"confirmed": False}, headers=as_persona(ASHA)
    )
    assert resp.status_code == 422 and resp.json()["type"] == "confirmation_required"
    assert world.finance.submitted == {}
    missing = await http.post("/v1/claims/clm-1/submit", json={}, headers=as_persona(ASHA))
    assert missing.status_code == 422


async def test_submit_calls_finance_once_and_is_idempotent(http: AsyncClient, world: World):
    await seed_claim(world, make_claim(answered=True))
    first = await http.post(
        "/v1/claims/clm-1/submit",
        json={"confirmed": True},
        headers={**as_persona(ASHA), "Idempotency-Key": "k-1"},
    )
    assert first.status_code == 200
    claim = first.json()
    assert claim["status"] == ClaimStatus.submitted.value
    assert claim["submission_reference"] == "FIN-2026-000001"

    again = await http.post(
        "/v1/claims/clm-1/submit",
        json={"confirmed": True},
        headers={**as_persona(ASHA), "Idempotency-Key": "k-1"},
    )
    assert again.status_code == 200 and again.json()["submission_reference"] == "FIN-2026-000001"
    assert list(world.finance.submitted) == ["k-1"]  # finance saw it exactly once
    actions = [e.action for e in await world.repo.audit_trail("clm-1")]
    assert actions == ["claim_submitted"]

    late = await http.post(
        "/v1/claims/clm-1/answers", json={"answers": {"q-attendees": "x"}}, headers=as_persona(ASHA)
    )
    assert late.status_code == 409 and late.json()["type"] == "claim_locked"


async def test_only_the_owner_can_submit(http: AsyncClient, world: World):
    await seed_claim(world, make_claim(answered=True))
    for persona, expected in ((RAVI, 403), (MEERA, 404)):
        resp = await http.post(
            "/v1/claims/clm-1/submit", json={"confirmed": True}, headers=as_persona(persona)
        )
        assert resp.status_code == expected
    assert world.finance.submitted == {}


async def test_default_idempotency_key_is_per_claim(http: AsyncClient, world: World):
    await seed_claim(world, make_claim(answered=True))
    await http.post("/v1/claims/clm-1/submit", json={"confirmed": True}, headers=as_persona(ASHA))
    assert list(world.finance.submitted) == ["submit-clm-1"]


async def test_approver_flow(http: AsyncClient, world: World):
    await seed_claim(world, make_claim(answered=True))
    assert (await http.get("/v1/approvals", headers=as_persona(RAVI))).json() == []  # not submitted

    await http.post("/v1/claims/clm-1/submit", json={"confirmed": True}, headers=as_persona(ASHA))
    queue = (await http.get("/v1/approvals", headers=as_persona(RAVI))).json()
    assert [c["id"] for c in queue] == ["clm-1"]

    decision = await http.post(
        "/v1/claims/clm-1/decision",
        json={"approved": True, "comment": "Looks fine"},
        headers=as_persona(RAVI),
    )
    assert decision.status_code == 200 and decision.json()["status"] == "approved"
    assert world.finance.decisions == [("FIN-2026-000001", True, "DEMO-RAVI")]
    assert (await http.get("/v1/approvals", headers=as_persona(RAVI))).json() == []
    done = await http.get("/v1/approvals?status=approved", headers=as_persona(RAVI))
    assert [c["id"] for c in done.json()] == ["clm-1"]

    again = await http.post(
        "/v1/claims/clm-1/decision", json={"approved": True}, headers=as_persona(RAVI)
    )
    assert again.status_code == 200  # repeating the same decision is harmless


async def test_approver_endpoints_reject_everyone_else(http: AsyncClient, world: World):
    await seed_claim(world, make_claim(answered=True))
    for method, url, body in (
        ("get", "/v1/approvals", None),
        ("post", "/v1/claims/clm-1/decision", {"approved": True}),
    ):
        resp = await http.request(method, url, json=body, headers=as_persona(ASHA))
        assert resp.status_code == 403 and resp.json()["type"] == "approver_only"


async def test_cannot_decide_a_claim_that_was_not_submitted(http: AsyncClient, world: World):
    await seed_claim(world, make_claim(answered=True))
    resp = await http.post(
        "/v1/claims/clm-1/decision", json={"approved": False}, headers=as_persona(RAVI)
    )
    assert resp.status_code == 409 and resp.json()["type"] == "claim_not_submitted"


async def test_a_high_finding_routes_the_claim_to_finance_review(http: AsyncClient, world: World):
    from claimpilot.domain import Finding, Severity

    flagged = make_claim().model_copy(
        update={
            "findings": [
                Finding(code="alcohol_not_reimbursable", severity=Severity.high, message="m")
            ]
        }
    )
    await seed_claim(world, flagged, route="auto_approve")
    resp = await http.post(
        "/v1/claims/clm-1/answers",
        json={"answers": {"q-attendees": "Orion: A. Rao"}},
        headers=as_persona(ASHA),
    )
    assert resp.status_code == 200 and resp.json()["route"] == "finance_review"
