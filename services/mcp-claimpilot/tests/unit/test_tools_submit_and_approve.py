"""Submitting (the confirmation gate), the approver tools, and the failures every tool shares.

The REST side is ``FakeApi``, which also holds every request to the committed OpenAPI document.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer

from claimpilot_mcp.client import ClaimPilotClient
from claimpilot_mcp.server import (
    IDEMPOTENCY_PREFIX,
    NOT_AN_APPROVER,
    ClaimPilotTools,
    idempotency_key,
)
from claimpilot_mcp.settings import Settings
from tests import factories as f
from tests.conftest import Sleeper
from tests.fakeapi import Call, FakeApi
from tests.helpers import JPEG, call, data, error_of, script_claim, script_persona

# -- submit_claim: the confirmation gate ---------------------------------------------------------


async def test_without_confirmation_nothing_is_submitted(api: FakeApi, server: MCPServer):
    script_claim(api, f.ready_claim())
    result = data(await call(server, "submit_claim", claim_id=f.CLAIM_ID))
    assert result["state"] == "needs_confirmation"
    assert result["submitted"] is False
    assert "submission_reference" not in result
    assert result["claim"]["title"] == "Mumbai trip 9-10 Oct 2026"
    assert result["claim"]["findings"][0]["code"] == "meals_over_limit"
    assert len(result["claim"]["documents"]) == 2
    assert result["next_step"].startswith("NOT SUBMITTED.")
    assert "confirm explicitly" in result["next_step"]
    assert "confirmed=true" in result["next_step"]
    assert {c.method for c in api.calls} == {"GET"}  # not one write


@pytest.mark.parametrize("flag", [False, "false", "False", "no", "0"])
async def test_every_way_of_saying_not_confirmed_stays_a_preview(
    api: FakeApi, server: MCPServer, flag: Any
):
    script_claim(api, f.ready_claim())
    result = data(await call(server, "submit_claim", claim_id=f.CLAIM_ID, confirmed=flag))
    assert result["state"] == "needs_confirmation"
    assert api.calls_to("POST", "/v1/claims/{claim_id}/submit") == []


async def test_a_claim_with_open_questions_is_not_ready_to_confirm(api: FakeApi, server: MCPServer):
    script_claim(api, f.claim())
    result = data(await call(server, "submit_claim", claim_id=f.CLAIM_ID))
    assert result["state"] == "not_ready"
    assert result["submitted"] is False
    assert result["claim"]["open_questions"]
    assert result["next_step"].startswith("NOT SUBMITTED: the claim is not ready.")
    assert api.calls_to("POST", "/v1/claims/{claim_id}/submit") == []


@pytest.mark.parametrize("status", ["submitted", "approved", "rejected"])
async def test_an_already_submitted_claim_is_reported_not_resubmitted(
    api: FakeApi, server: MCPServer, status: str
):
    script_claim(api, f.submitted_claim(status=status))
    result = data(await call(server, "submit_claim", claim_id=f.CLAIM_ID))
    assert (result["state"], result["submitted"]) == ("already_submitted", True)
    assert result["submission_reference"] == "FIN-2026-000001"
    assert result["next_step"].startswith("Nothing was sent now")
    assert api.calls_to("POST", "/v1/claims/{claim_id}/submit") == []


async def test_with_confirmation_the_claim_is_submitted_with_a_stable_idempotency_key(
    api: FakeApi, server: MCPServer
):
    script_claim(api, f.submitted_claim())
    api.json("POST", "/v1/claims/{claim_id}/submit", f.submitted_claim())
    result = data(await call(server, "submit_claim", claim_id=f.CLAIM_ID, confirmed=True))
    (post,) = api.calls_to("POST", "/v1/claims/{claim_id}/submit")
    assert post.body == {"confirmed": True}
    assert post.headers["idempotency-key"] == f"{IDEMPOTENCY_PREFIX}{f.CLAIM_ID}"
    assert (result["state"], result["submitted"]) == ("submitted", True)
    assert result["submission_reference"] == "FIN-2026-000001"
    assert result["claim"]["status"] == "submitted"
    assert result["next_step"].startswith("Submitted to finance.")


async def test_retrying_a_confirmed_submission_sends_the_same_key_every_time(
    api: FakeApi, server: MCPServer
):
    script_claim(api, f.submitted_claim())
    api.json("POST", "/v1/claims/{claim_id}/submit", f.submitted_claim())
    for _ in range(3):
        await call(server, "submit_claim", claim_id=f.CLAIM_ID, confirmed=True)
    keys = {
        c.headers["idempotency-key"] for c in api.calls_to("POST", "/v1/claims/{claim_id}/submit")
    }
    assert keys == {idempotency_key(f.CLAIM_ID)}


def test_the_key_is_derived_from_the_claim_id_and_differs_between_claims():
    assert idempotency_key("clm-1") == idempotency_key("clm-1")
    assert idempotency_key("clm-1") != idempotency_key("clm-2")
    assert idempotency_key("clm-1").startswith(IDEMPOTENCY_PREFIX)
    assert "clm-1" in idempotency_key("clm-1")


async def test_a_claim_that_is_not_ready_is_refused_by_the_api_with_its_reason(
    api: FakeApi, server: MCPServer
):
    api.respond(
        "POST",
        "/v1/claims/{claim_id}/submit",
        lambda _: f.problem(
            409,
            "claim_not_ready",
            "The claim is not ready to submit",
            {"status": "needs_info", "unanswered": ["q-business_purpose-1bde8f60"]},
        ),
    )
    message = error_of(await call(server, "submit_claim", claim_id=f.CLAIM_ID, confirmed=True))
    assert "The claim is not ready to submit" in message
    assert "q-business_purpose-1bde8f60" in message


async def test_only_the_owner_can_submit(api: FakeApi, server: MCPServer):
    api.respond(
        "POST",
        "/v1/claims/{claim_id}/submit",
        lambda _: f.problem(403, "not_your_claim", "Only the owner can submit"),
    )
    result = await call(server, "submit_claim", claim_id=f.CLAIM_ID, confirmed=True)
    assert error_of(result).endswith("Only the owner can submit")


async def test_a_confirmation_that_is_not_a_boolean_is_rejected(api: FakeApi, server: MCPServer):
    result = await call(server, "submit_claim", claim_id=f.CLAIM_ID, confirmed="maybe")
    assert result.is_error
    assert api.calls == []


# -- approvers -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("list_approvals", {}),
        ("decide_claim", {"claim_id": f.CLAIM_ID, "approve": True}),
    ],
)
async def test_a_persona_that_is_not_an_approver_is_told_so_and_nothing_else_is_called(
    api: FakeApi, server: MCPServer, tool: str, arguments: dict[str, Any]
):
    script_persona(api, approver=False)
    message = error_of(await call(server, tool, **arguments))
    assert NOT_AN_APPROVER in message
    assert f.PERSONA not in message
    assert [c.template for c in api.calls] == ["/v1/me"]


async def test_the_approver_sees_the_queue(api: FakeApi, server: MCPServer):
    script_persona(api, approver=True)
    api.json("GET", "/v1/approvals", [f.submitted_claim(), f.submitted_claim(id="clm-2")])
    result = data(await call(server, "list_approvals"))
    assert (result["status"], result["count"]) == ("submitted", 2)
    assert [c["claim_id"] for c in result["claims"]] == [f.CLAIM_ID, "clm-2"]
    assert [c.template for c in api.calls] == ["/v1/me", "/v1/approvals"]  # the check comes first
    assert api.calls[1].query == {"status": "submitted"}


@pytest.mark.parametrize("status", ["approved", "rejected"])
async def test_the_queue_can_show_decided_claims(api: FakeApi, server: MCPServer, status: str):
    script_persona(api, approver=True)
    api.json("GET", "/v1/approvals", [])
    result = data(await call(server, "list_approvals", status=status))
    assert result["status"] == status
    assert api.calls[1].query == {"status": status}


async def test_a_long_queue_is_cut_with_a_note(api: FakeApi, server: MCPServer):
    script_persona(api, approver=True)
    api.json("GET", "/v1/approvals", [f.submitted_claim(id=f"clm-{i}") for i in range(51)])
    result = data(await call(server, "list_approvals"))
    assert (result["count"], len(result["claims"])) == (51, 50)
    assert "Showing the first 50 of 51" in result["note"]


async def test_the_approver_approves(api: FakeApi, server: MCPServer):
    script_persona(api, approver=True)
    api.json("POST", "/v1/claims/{claim_id}/decision", f.submitted_claim(status="approved"))
    result = data(await call(server, "decide_claim", claim_id=f.CLAIM_ID, approve=True))
    assert api.calls[1].body == {"approved": True, "comment": ""}
    assert (result["decision"], result["status"]) == ("approved", "approved")
    assert result["submission_reference"] == "FIN-2026-000001"
    assert "comment" not in result
    assert result["next_step"] == "The decision is recorded and final. Tell the human."


async def test_the_approver_rejects_with_a_reason(api: FakeApi, server: MCPServer):
    script_persona(api, approver=True)
    api.json("POST", "/v1/claims/{claim_id}/decision", f.submitted_claim(status="rejected"))
    result = data(
        await call(
            server,
            "decide_claim",
            claim_id=f.CLAIM_ID,
            approve=False,
            comment="  Attach the original invoice  ",
        )
    )
    assert api.calls[1].body == {"approved": False, "comment": "Attach the original invoice"}
    assert (result["decision"], result["status"]) == ("rejected", "rejected")
    assert result["comment"] == "Attach the original invoice"


async def test_rejecting_without_a_reason_surfaces_the_apis_message(
    api: FakeApi, server: MCPServer
):
    script_persona(api, approver=True)
    api.respond(
        "POST",
        "/v1/claims/{claim_id}/decision",
        lambda _: f.problem(422, "comment_required", "Say why the claim is rejected"),
    )
    result = await call(server, "decide_claim", claim_id=f.CLAIM_ID, approve=False)
    assert error_of(result).endswith("Say why the claim is rejected")


async def test_nobody_decides_their_own_claim(api: FakeApi, server: MCPServer):
    script_persona(api, approver=True)
    api.respond(
        "POST",
        "/v1/claims/{claim_id}/decision",
        lambda _: f.problem(403, "own_claim", "You cannot approve or reject your own claim"),
    )
    result = await call(server, "decide_claim", claim_id=f.CLAIM_ID, approve=True)
    assert "You cannot approve or reject your own claim" in error_of(result)


async def test_a_claim_that_is_not_submitted_cannot_be_decided(api: FakeApi, server: MCPServer):
    script_persona(api, approver=True)
    api.respond(
        "POST",
        "/v1/claims/{claim_id}/decision",
        lambda _: f.problem(
            409,
            "claim_not_submitted",
            "Only a submitted claim can be approved or rejected",
            {"status": "needs_info"},
        ),
    )
    message = error_of(await call(server, "decide_claim", claim_id=f.CLAIM_ID, approve=True))
    assert "Only a submitted claim can be approved or rejected" in message
    assert "needs_info" in message


async def test_a_comment_over_the_apis_limit_is_rejected_early(api: FakeApi, server: MCPServer):
    result = await call(
        server, "decide_claim", claim_id=f.CLAIM_ID, approve=False, comment="x" * 501
    )
    assert result.is_error
    assert api.calls == []


async def test_a_persona_the_api_does_not_know_cannot_be_checked(api: FakeApi, server: MCPServer):
    api.respond("GET", "/v1/me", lambda _: f.problem(401, "unknown_persona", "Unknown persona X"))
    message = error_of(await call(server, "list_approvals"))
    assert "CLAIMPILOT_PERSONA" in message
    assert [c.template for c in api.calls] == ["/v1/me"]


# -- failures every tool shares ------------------------------------------------------------------

ALL_TOOLS: list[tuple[str, dict[str, Any]]] = [
    ("list_claims", {}),
    ("get_claim", {"claim_id": f.CLAIM_ID}),
    ("get_batch", {"batch_id": f.BATCH_ID}),
    ("answer_question", {"claim_id": f.CLAIM_ID, "text": "Workshop"}),
    ("submit_claim", {"claim_id": f.CLAIM_ID}),
    ("submit_claim", {"claim_id": f.CLAIM_ID, "confirmed": True}),
    ("list_approvals", {}),
    ("decide_claim", {"claim_id": f.CLAIM_ID, "approve": True}),
]


@pytest.mark.parametrize(("tool", "arguments"), ALL_TOOLS)
async def test_when_the_api_is_down_every_tool_says_so_in_plain_words(
    api: FakeApi, server: MCPServer, tool: str, arguments: dict[str, Any]
):
    def down(_: Call) -> httpx.Response:
        raise httpx.ConnectError("[Errno 111] Connection refused")

    for method, template in (
        ("GET", "/v1/me"),
        ("GET", "/v1/claims"),
        ("GET", "/v1/claims/{claim_id}"),
        ("GET", "/v1/batches/{batch_id}"),
        ("POST", "/v1/claims/{claim_id}/reply"),
        ("POST", "/v1/claims/{claim_id}/submit"),
        ("POST", "/v1/claims/{claim_id}/decision"),
    ):
        api.respond(method, template, down)
    message = error_of(await call(server, tool, **arguments))
    assert "Cannot reach the ClaimPilot API at http://api.test" in message
    assert "Traceback" not in message
    assert "Connection refused" not in message


async def test_upload_reports_an_unreachable_api_too(
    api: FakeApi, server: MCPServer, tmp_path: Path
):
    (tmp_path / "taxi.jpg").write_bytes(JPEG)

    def down(_: Call) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    api.respond("POST", "/v1/batches", down)
    message = error_of(await call(server, "upload_receipts", paths=[str(tmp_path / "taxi.jpg")]))
    assert "did not answer in time" in message


async def test_a_crash_inside_a_tool_reaches_the_model_only_as_a_generic_error(
    api: FakeApi, server: MCPServer
):
    def crash(_: Call) -> httpx.Response:
        raise RuntimeError("secret internals: /srv/app/db.py line 42")

    api.respond("GET", "/v1/claims", crash)
    message = error_of(await call(server, "list_claims"))
    assert message == "Error executing tool list_claims"


async def test_the_tools_object_can_be_driven_directly(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, sleeper: Sleeper
):
    api.json("GET", "/v1/claims", [f.claim()])
    tools = ClaimPilotTools(client, settings, allow_any_path=True, sleep=sleeper)
    listed = await tools.list_claims()
    assert listed.count == 1
    assert listed.claims[0].claim_id == f.CLAIM_ID


async def test_a_session_ending_closes_the_connection_and_the_next_one_reopens_it(
    api: FakeApi, server: MCPServer, client: ClaimPilotClient
):
    api.json("GET", "/v1/claims", [])
    await call(server, "list_claims")
    assert client._client is None  # pyright: ignore[reportPrivateUsage]
    await call(server, "list_claims")
    assert len(api.calls) == 2
