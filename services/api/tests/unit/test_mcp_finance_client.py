"""FinanceClient: typed methods over the finance tools, using FakeMcpClient."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from claimpilot.config import Settings
from claimpilot.mcp import (
    FakeMcpClient,
    FinanceClient,
    McpProtocolError,
    McpToolClient,
    McpToolError,
    StreamableHttpClient,
    get_finance_client,
)

RECEIPT = {
    "reference": "FIN-2026-000001",
    "status": "received",
    "received_at": "2026-10-08T09:00:00Z",
    "duplicate": False,
}


def claim_wire(**overrides: Any) -> dict[str, Any]:
    event = {"status": "received", "at": "2026-10-08T09:00:00Z", "actor": None, "comment": ""}
    base: dict[str, Any] = {
        "reference": "FIN-2026-000001",
        "claim_id": "clm-001",
        "employee_id": "P001",
        "title": "Pune trip 12-14 Aug",
        "total": 4500.0,
        "currency": "INR",
        "document_ids": ["doc-a", "doc-b"],
        "status": "received",
        "received_at": "2026-10-08T09:00:00Z",
        "updated_at": "2026-10-08T09:00:00Z",
        "history": [event],
    }
    return {**base, **overrides}


SUBMIT: dict[str, Any] = {
    "claim_id": "clm-001",
    "employee_id": "P001",
    "title": "Pune trip 12-14 Aug",
    "total": 4500.0,
    "currency": "INR",
    "document_ids": ["doc-a", "doc-b"],
    "idempotency_key": "key-1",
}


@pytest.fixture
def fake() -> FakeMcpClient:
    return FakeMcpClient(name="finance")


@pytest.fixture
def finance(fake: FakeMcpClient) -> FinanceClient:
    return FinanceClient(fake)


# -- submit_claim -------------------------------------------------------------------------------


async def test_submit_claim_sends_every_field_and_parses_the_receipt(
    fake: FakeMcpClient, finance: FinanceClient
):
    fake.add_tool("submit_claim", RECEIPT)
    receipt = await finance.submit_claim(**{**SUBMIT, "document_ids": ("doc-a", "doc-b")})
    assert fake.calls == [("submit_claim", SUBMIT)]  # a tuple goes over the wire as a list
    assert receipt.reference == "FIN-2026-000001"
    assert receipt.status == "received"
    assert receipt.duplicate is False
    assert receipt.received_at == datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


async def test_a_replayed_submission_is_flagged_as_duplicate(
    fake: FakeMcpClient, finance: FinanceClient
):
    fake.add_tool("submit_claim", {**RECEIPT, "duplicate": True})
    assert (await finance.submit_claim(**SUBMIT)).duplicate is True


async def test_submit_claim_is_keyword_only(fake: FakeMcpClient, finance: FinanceClient):
    fake.add_tool("submit_claim", RECEIPT)
    with pytest.raises(TypeError):
        await finance.submit_claim("clm-001", "P001", "t", 1.0, "INR", ["d"], "k")  # type: ignore[misc]


async def test_a_rejected_submission_raises_the_tool_error(
    fake: FakeMcpClient, finance: FinanceClient
):
    fake.add_tool("submit_claim", McpToolError("idempotency_key 'key-1' was already used"))
    with pytest.raises(McpToolError, match="already used"):
        await finance.submit_claim(**SUBMIT)


@pytest.mark.parametrize(
    ("broken", "field"),
    [
        ({"status": "received", "received_at": "2026-10-08T09:00:00Z"}, "reference"),
        ({**RECEIPT, "received_at": "not a time"}, "received_at"),
        ({**RECEIPT, "status": "approved"}, "status"),
    ],
)
async def test_a_receipt_that_does_not_fit_is_a_protocol_error(
    fake: FakeMcpClient, finance: FinanceClient, broken: dict[str, Any], field: str
):
    fake.add_tool("submit_claim", broken)
    with pytest.raises(McpProtocolError) as caught:
        await finance.submit_claim(**SUBMIT)
    assert field in str(caught.value)
    assert (caught.value.server, caught.value.tool) == ("finance", "submit_claim")


async def test_unknown_result_fields_are_ignored(fake: FakeMcpClient, finance: FinanceClient):
    fake.add_tool("submit_claim", {**RECEIPT, "queue_position": 4})
    assert (await finance.submit_claim(**SUBMIT)).reference == "FIN-2026-000001"


# -- reading ------------------------------------------------------------------------------------


async def test_get_claim_status_returns_the_claim_with_its_history(
    fake: FakeMcpClient, finance: FinanceClient
):
    approved = {
        "status": "approved",
        "at": "2026-10-08T10:00:00Z",
        "actor": "DEMO-RAVI",
        "comment": "ok",
    }
    wire = claim_wire(status="approved")
    wire["history"].append(approved)
    fake.add_tool("get_claim_status", wire)
    claim = await finance.get_claim_status("FIN-2026-000001")
    assert fake.calls == [("get_claim_status", {"reference": "FIN-2026-000001"})]
    assert (claim.reference, claim.status, claim.total, claim.currency) == (
        "FIN-2026-000001",
        "approved",
        4500.0,
        "INR",
    )
    assert claim.document_ids == ["doc-a", "doc-b"]
    assert [(e.status, e.actor) for e in claim.history] == [
        ("received", None),
        ("approved", "DEMO-RAVI"),
    ]
    assert claim.history[-1].at == datetime(2026, 10, 8, 10, 0, tzinfo=UTC)


async def test_an_unknown_reference_is_a_tool_error(fake: FakeMcpClient, finance: FinanceClient):
    fake.add_tool("get_claim_status", McpToolError("no claim with reference 'FIN-2026-000009'"))
    with pytest.raises(McpToolError, match="no claim with reference"):
        await finance.get_claim_status("FIN-2026-000009")


async def test_a_claim_with_an_unknown_status_is_a_protocol_error(
    fake: FakeMcpClient, finance: FinanceClient
):
    fake.add_tool("get_claim_status", claim_wire(status="paid"))
    with pytest.raises(McpProtocolError, match="status"):
        await finance.get_claim_status("FIN-2026-000001")


async def test_list_claims_sends_only_the_filters_given(
    fake: FakeMcpClient, finance: FinanceClient
):
    summary = {k: v for k, v in claim_wire().items() if k != "history"}
    fake.add_tool("list_claims", {"result": [summary]})
    everything = await finance.list_claims()
    mine = await finance.list_claims(status="received", employee_id="P001")
    assert fake.calls == [
        ("list_claims", {}),
        ("list_claims", {"status": "received", "employee_id": "P001"}),
    ]
    assert [c.reference for c in everything] == ["FIN-2026-000001"] == [c.reference for c in mine]
    assert not hasattr(everything[0], "history")


async def test_list_claims_with_nothing_returns_an_empty_list(
    fake: FakeMcpClient, finance: FinanceClient
):
    fake.add_tool("list_claims", {"result": []})
    assert await finance.list_claims(status="rejected") == []


@pytest.mark.parametrize("broken", [{}, {"result": "oops"}, {"result": [{"reference": "x"}]}])
async def test_a_malformed_list_is_a_protocol_error(
    fake: FakeMcpClient, finance: FinanceClient, broken: dict[str, Any]
):
    fake.add_tool("list_claims", broken)
    with pytest.raises(McpProtocolError) as caught:
        await finance.list_claims()
    assert caught.value.tool == "list_claims"


# -- approver actions ---------------------------------------------------------------------------


async def test_start_review_and_decide_claim_send_the_documented_arguments(
    fake: FakeMcpClient, finance: FinanceClient
):
    fake.add_tool("start_review", claim_wire(status="under_review"))
    fake.add_tool("decide_claim", claim_wire(status="rejected"))
    reviewed = await finance.start_review("FIN-2026-000001", "DEMO-RAVI")
    decided = await finance.decide_claim("FIN-2026-000001", "rejected", "DEMO-RAVI", "no invoice")
    assert (reviewed.status, decided.status) == ("under_review", "rejected")
    assert fake.calls == [
        ("start_review", {"reference": "FIN-2026-000001", "reviewer_id": "DEMO-RAVI"}),
        (
            "decide_claim",
            {
                "reference": "FIN-2026-000001",
                "decision": "rejected",
                "approver_id": "DEMO-RAVI",
                "comment": "no invoice",
            },
        ),
    ]


async def test_decide_claim_comment_defaults_to_empty(fake: FakeMcpClient, finance: FinanceClient):
    fake.add_tool("decide_claim", claim_wire(status="approved"))
    await finance.decide_claim("FIN-2026-000001", "approved", "DEMO-RAVI")
    assert fake.calls[0][1]["comment"] == ""


async def test_an_invalid_transition_is_a_tool_error(fake: FakeMcpClient, finance: FinanceClient):
    fake.add_tool("decide_claim", McpToolError("claim FIN-2026-000001 is already approved"))
    with pytest.raises(McpToolError, match="already approved"):
        await finance.decide_claim("FIN-2026-000001", "rejected", "DEMO-RAVI", "late")


# -- it is still an MCP tool client -------------------------------------------------------------


async def test_the_typed_client_exposes_the_generic_protocol(
    fake: FakeMcpClient, finance: FinanceClient
):
    fake.add_tool("submit_claim", RECEIPT, description="Submit.")
    fake.add_resource("finance://x", "text")
    assert isinstance(finance, McpToolClient)
    assert [t.name for t in await finance.list_tools()] == ["submit_claim"]
    assert (await finance.call_tool("submit_claim", {}))["reference"] == "FIN-2026-000001"
    assert await finance.read_resource("finance://x") == "text"


def test_the_factory_points_at_the_configured_server():
    settings = Settings(mcp_finance_url="http://mcp-finance:8101/mcp")
    finance = get_finance_client(settings, timeout=5.0)
    inner = finance._inner
    assert isinstance(inner, StreamableHttpClient)
    assert (inner.url, inner.name) == ("http://mcp-finance:8101/mcp", "finance")
    assert inner._timeout == 5.0
