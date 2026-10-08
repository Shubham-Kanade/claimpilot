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
    assert list(world.finance.submitted) == ["claim-clm-1"]  # finance saw it exactly once
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


async def test_the_finance_key_is_the_claim_whatever_the_client_sends(
    http: AsyncClient, world: World
):
    await seed_claim(world, make_claim(answered=True))
    await http.post("/v1/claims/clm-1/submit", json={"confirmed": True}, headers=as_persona(ASHA))
    assert list(world.finance.submitted) == ["claim-clm-1"]


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
        "/v1/claims/clm-1/decision",
        json={"approved": False, "comment": "no"},
        headers=as_persona(RAVI),
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


async def test_a_rejection_needs_a_reason(http: AsyncClient, world: World):
    await seed_claim(world, make_claim(answered=True))
    await http.post("/v1/claims/clm-1/submit", json={"confirmed": True}, headers=as_persona(ASHA))
    bare = await http.post(
        "/v1/claims/clm-1/decision",
        json={"approved": False, "comment": "  "},
        headers=as_persona(RAVI),
    )
    assert bare.status_code == 422 and bare.json()["type"] == "comment_required"
    assert world.finance.decisions == []  # finance was never asked

    reasoned = await http.post(
        "/v1/claims/clm-1/decision",
        json={"approved": False, "comment": "Alcohol is not reimbursable (6.1)"},
        headers=as_persona(RAVI),
    )
    assert reasoned.status_code == 200 and reasoned.json()["status"] == "rejected"


async def test_two_submissions_with_different_keys_make_one_finance_record(
    http: AsyncClient, world: World
):
    await seed_claim(world, make_claim(answered=True))
    first = await http.post(
        "/v1/claims/clm-1/submit",
        json={"confirmed": True},
        headers={**as_persona(ASHA), "Idempotency-Key": "first-try"},
    )
    again = await http.post(
        "/v1/claims/clm-1/submit",
        json={"confirmed": True},
        headers={**as_persona(ASHA), "Idempotency-Key": "a-different-key"},
    )
    assert first.status_code == again.status_code == 200
    assert first.json()["submission_reference"] == again.json()["submission_reference"]
    assert list(world.finance.submitted) == [
        "claim-clm-1"
    ]  # the key is the claim, not the client's


async def test_a_retry_after_a_half_finished_submission_finds_the_first_record(
    http: AsyncClient, world: World
):
    await seed_claim(world, make_claim(answered=True))
    # finance accepted the claim but our own record of it was lost (the key is derived from the id)
    stored = await world.repo.get_claim("clm-1")
    assert stored is not None
    accepted = await world.finance.submit_claim(stored, idempotency_key="claim-clm-1")
    resp = await http.post(
        "/v1/claims/clm-1/submit", json={"confirmed": True}, headers=as_persona(ASHA)
    )
    assert resp.json()["submission_reference"] == accepted.reference
    assert len(world.finance.submitted) == 1


async def test_nobody_decides_their_own_claim(http: AsyncClient, world: World):
    await seed_claim(world, make_claim(employee_id="DEMO-RAVI", answered=True))
    await http.post("/v1/claims/clm-1/submit", json={"confirmed": True}, headers=as_persona(RAVI))
    resp = await http.post(
        "/v1/claims/clm-1/decision", json={"approved": True}, headers=as_persona(RAVI)
    )
    assert resp.status_code == 403 and resp.json()["type"] == "own_claim"
    assert world.finance.decisions == []


class DownFinance:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def submit_claim(self, claim, *, idempotency_key: str):
        raise self.error

    async def decide_claim(self, reference, **kwargs):
        raise self.error


async def test_finance_down_is_a_503_problem_not_a_stack_trace(http: AsyncClient, world: World):
    from claimpilot.mcp import McpUnavailableError

    await seed_claim(world, make_claim(answered=True))
    world.container.finance = DownFinance(McpUnavailableError("timeout", server="finance"))
    resp = await http.post(
        "/v1/claims/clm-1/submit", json={"confirmed": True}, headers=as_persona(ASHA)
    )
    assert resp.status_code == 503
    assert resp.json()["type"] == "system_unavailable"
    assert "finance system" in resp.json()["title"] and resp.json()["detail"] == {"retryable": True}
    assert (await http.get("/v1/claims/clm-1", headers=as_persona(ASHA))).json()[
        "status"
    ] == "ready"


async def test_finance_rejecting_the_claim_is_a_502_with_its_reason(
    http: AsyncClient, world: World
):
    from claimpilot.mcp import McpToolError

    await seed_claim(world, make_claim(answered=True))
    world.container.finance = DownFinance(
        McpToolError("title must be at most 200 characters", server="finance")
    )
    resp = await http.post(
        "/v1/claims/clm-1/submit", json={"confirmed": True}, headers=as_persona(ASHA)
    )
    assert resp.status_code == 502 and resp.json()["type"] == "system_rejected"
    assert "title must be at most 200 characters" in resp.json()["title"]


async def test_the_corporate_directory_being_down_is_a_503_too(http: AsyncClient, world: World):
    from claimpilot.mcp import McpUnavailableError

    class DownDirectory:
        async def get(self, employee_id: str):
            raise McpUnavailableError("connection refused", server="corp")

        async def list(self):
            raise McpUnavailableError("connection refused", server="corp")

    world.container.directory = DownDirectory()
    resp = await http.get("/v1/claims", headers=as_persona(ASHA))
    assert resp.status_code == 503 and "corporate systems" in resp.json()["title"]


async def test_answers_are_bounded(http: AsyncClient, world: World):
    await seed_claim(world, make_claim())
    too_long = await http.post(
        "/v1/claims/clm-1/answers",
        json={"answers": {"q-attendees": "x" * 2001}},
        headers=as_persona(ASHA),
    )
    too_many = await http.post(
        "/v1/claims/clm-1/answers",
        json={"answers": {f"q-{i}": "x" for i in range(51)}},
        headers=as_persona(ASHA),
    )
    assert too_long.status_code == 422 and too_many.status_code == 422
