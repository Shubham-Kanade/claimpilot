"""The API's MCP layer against the REAL mock servers (services/mcp-finance, services/mcp-corp).

The two server packages are imported straight from the monorepo (their dependencies are already
in this environment) and run under uvicorn on free loopback ports, so these tests exercise the
real streamable-HTTP transport, the real tool schemas and the real error messages: the contract
between the servers and the typed clients, the adapters and the bridge. Skipped when the server
sources are not in the checkout.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import importlib
import re
import socket
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer

from claimpilot.config import Settings
from claimpilot.domain.claims import Claim, ClaimMode
from claimpilot.mcp import (
    EMPLOYEE_AGENT_TOOLS,
    CorpClient,
    EmployeeNotFoundError,
    FinanceClient,
    McpBridge,
    McpError,
    McpProtocolError,
    McpToolError,
    McpUnavailableError,
    StreamableHttpClient,
    get_mcp_ports,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
FINANCE_SRC = REPO_ROOT / "services" / "mcp-finance" / "src"
CORP_SRC = REPO_ROOT / "services" / "mcp-corp" / "src"
CORP_SEED = REPO_ROOT / "services" / "mcp-corp" / "seed"

pytestmark = pytest.mark.skipif(
    not (FINANCE_SRC.is_dir() and CORP_SRC.is_dir() and CORP_SEED.is_dir()),
    reason="services/mcp-finance and services/mcp-corp are not in this checkout",
)

POLICY_TEXT = "name: Test Policy\nclauses:\n  - id: '1.1'\n    text: Amounts are in ₹.\n"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.asynccontextmanager
async def serve(app: Any) -> AsyncIterator[str]:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            if task.done():
                await task
            await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await task


@dataclass
class Env:
    finance_url: str
    corp_url: str
    policy_path: Path

    def settings(self) -> Settings:
        return Settings(mcp_finance_url=self.finance_url, mcp_corp_url=self.corp_url)

    def finance(self) -> FinanceClient:
        return FinanceClient(StreamableHttpClient(self.finance_url, name="finance"))

    def corp(self) -> CorpClient:
        return CorpClient(StreamableHttpClient(self.corp_url, name="corp"))


@pytest.fixture(scope="module")
def server_modules() -> dict[str, Any]:
    sys.path[:0] = [str(FINANCE_SRC), str(CORP_SRC)]
    try:
        return {
            name: importlib.import_module(name)
            for name in (
                "claimpilot_mcp_finance.server",
                "claimpilot_mcp_finance.store",
                "claimpilot_mcp_corp.server",
                "claimpilot_mcp_corp.directory",
                "claimpilot_mcp_corp.policy",
            )
        }
    finally:
        del sys.path[:2]


@pytest.fixture
def servers(server_modules: dict[str, Any], tmp_path: Path):
    """``async with servers() as env`` starts both servers on free ports (and a fresh database)."""
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(POLICY_TEXT, encoding="utf-8")

    @contextlib.asynccontextmanager
    async def start() -> AsyncIterator[Env]:
        store = server_modules["claimpilot_mcp_finance.store"].FinanceStore()
        finance_app = server_modules["claimpilot_mcp_finance.server"].create_app(store)
        directory = server_modules["claimpilot_mcp_corp.directory"].Directory.load(CORP_SEED)
        policy = server_modules["claimpilot_mcp_corp.policy"].PolicyProvider(policy_path)
        corp_app = server_modules["claimpilot_mcp_corp.server"].create_app(directory, policy)
        try:
            async with serve(finance_app) as finance_url, serve(corp_app) as corp_url:
                yield Env(finance_url, corp_url, policy_path)
        finally:
            store.close()

    return start


def a_claim(**overrides: Any) -> Claim:
    base: dict[str, Any] = {
        "id": "clm-001",
        "employee_id": "P001",
        "title": "Pune trip 12-14 Aug",
        "mode": ClaimMode.trip,
        "document_ids": ["doc-a", "doc-b"],
        "total": 4500.5,
        "currency": "INR",
    }
    return Claim(**{**base, **overrides})


# -- FinanceClient <-> mcp-finance ----------------------------------------------------------------


async def test_the_finance_client_follows_a_claim_through_the_real_server(servers):
    async with servers() as env:
        finance = env.finance()
        tools = {t.name: t for t in await finance.list_tools()}
        assert set(tools) == {
            "submit_claim",
            "get_claim_status",
            "list_claims",
            "start_review",
            "decide_claim",
        }
        assert tools["get_claim_status"].read_only is True
        assert tools["submit_claim"].read_only is False

        arguments: dict[str, Any] = {
            "claim_id": "clm-001",
            "employee_id": "P001",
            "title": "Pune trip 12-14 Aug",
            "total": 4500.5,
            "currency": "INR",
            "document_ids": ["doc-a", "doc-b"],
            "idempotency_key": "key-1",
        }
        receipt = await finance.submit_claim(**arguments)
        assert re.fullmatch(r"FIN-\d{4}-000001", receipt.reference)
        assert (receipt.status, receipt.duplicate) == ("received", False)
        assert receipt.received_at.tzinfo is not None

        replay = await finance.submit_claim(**arguments)
        assert (replay.reference, replay.duplicate) == (receipt.reference, True)

        with pytest.raises(McpToolError, match="already used for a different claim"):
            await finance.submit_claim(**{**arguments, "total": 1.0})

        claim = await finance.get_claim_status(receipt.reference)
        assert (claim.status, claim.total, claim.document_ids) == (
            "received",
            4500.5,
            ["doc-a", "doc-b"],
        )
        assert [e.status for e in claim.history] == ["received"]

        queue = await finance.list_claims(status="received")
        assert [c.reference for c in queue] == [receipt.reference]
        assert await finance.list_claims(employee_id="P002") == []

        assert (await finance.start_review(receipt.reference, "DEMO-RAVI")).status == "under_review"
        with pytest.raises(McpToolError, match="comment"):
            await finance.decide_claim(receipt.reference, "rejected", "DEMO-RAVI")
        decided = await finance.decide_claim(receipt.reference, "approved", "DEMO-RAVI", "ok")
        assert decided.status == "approved"
        assert [e.status for e in decided.history] == ["received", "under_review", "approved"]
        with pytest.raises(McpToolError, match="already approved"):
            await finance.decide_claim(receipt.reference, "rejected", "DEMO-RAVI", "late")
        with pytest.raises(McpToolError, match="no claim with reference"):
            await finance.get_claim_status("FIN-2026-009999")


# -- CorpClient <-> mcp-corp -----------------------------------------------------------------------


async def test_the_corp_client_reads_the_real_seed(servers):
    async with servers() as env:
        corp = env.corp()
        by_id = await corp.get_employee("P001")
        assert await corp.get_employee("EMP85968") == by_id
        assert (by_id.name, by_id.grade, by_id.base_city) == ("Advika Hayer", "L4", "Hyderabad")
        profile = await corp.get_employee_profile("emp85968")
        assert (profile.manager_id, profile.email) == ("DEMO-RAVI", "advika.hayer@example.com")

        with pytest.raises(EmployeeNotFoundError, match="P999"):
            await corp.get_employee("P999")  # the client keys on the server's exact wording
        with pytest.raises(EmployeeNotFoundError):
            await corp.search_calendar("P999", dt.date(2026, 8, 1), dt.date(2026, 8, 2))

        people = await corp.list_employees()
        assert [p.id for p in people][:5] == ["P001", "P002", "P003", "P004", "P005"]
        assert {"DEMO-ASHA", "DEMO-RAVI", "DEMO-MEERA"} <= {p.id for p in people}

        events = await corp.search_calendar("P002", dt.date(2026, 8, 1), dt.date(2026, 8, 31))
        assert [(e.kind, e.date) for e in events] == [
            ("client_dinner", dt.date(2026, 8, 4)),
            ("travel", dt.date(2026, 8, 12)),
            ("travel", dt.date(2026, 8, 18)),
        ]
        dinner, trip = events[0], events[1]
        assert dinner.location == "Chandni Cafe, Bhubaneswar"
        assert dinner.start_time is not None and 2 <= len(dinner.attendees) <= 4
        assert trip.end_date == dt.date(2026, 8, 14) and trip.covers(dt.date(2026, 8, 13))
        with pytest.raises(McpToolError, match="start_date must not be after end_date"):
            await corp.search_calendar("P002", dt.date(2026, 8, 5), dt.date(2026, 8, 1))

        assert await corp.get_policy_text() == POLICY_TEXT


async def test_the_policy_resource_and_tool_agree(servers):
    async with servers() as env:
        corp = env.corp()
        assert (await corp.call_tool("get_policy")) == {"result": POLICY_TEXT}
        assert await corp.read_resource("policy://expense/v3") == POLICY_TEXT
        with pytest.raises(McpProtocolError, match="Unknown resource"):
            await corp.read_resource("policy://expense/v2")


async def test_a_missing_policy_file_is_reported_not_crashed(servers):
    async with servers() as env:
        env.policy_path.unlink()
        corp = env.corp()
        with pytest.raises(McpProtocolError, match="policy not available"):
            await corp.get_policy_text()
        with pytest.raises(McpToolError, match="policy not available"):
            await corp.call_tool("get_policy")


# -- the adapters <-> both servers ----------------------------------------------------------------


async def test_the_adapters_satisfy_the_ports_against_the_real_servers(servers):
    async with servers() as env:
        ports_ = get_mcp_ports(env.settings())

        person = await ports_.directory.get("EMP74851")
        assert person is not None and person.id == "P002"
        assert await ports_.directory.get("P999") is None
        assert len(await ports_.directory.list()) == 8

        events = await ports_.calendar.events("P002", dt.date(2026, 8, 4), dt.date(2026, 8, 4))
        assert [(e.kind, e.title) for e in events] == [
            ("client_dinner", "Client dinner with Juniper Health")
        ]
        assert await ports_.calendar.events("P999", dt.date(2026, 8, 4), dt.date(2026, 8, 4)) == []
        # P001's client dinner on 14 Jul deliberately has no calendar entry: only the trip covers it
        day = dt.date(2026, 7, 14)
        assert [e.kind for e in await ports_.calendar.events("P001", day, day)] == ["travel"]

        first = await ports_.finance.submit_claim(a_claim(), idempotency_key="submit-clm-001")
        again = await ports_.finance.submit_claim(a_claim(), idempotency_key="submit-clm-001")
        assert first.reference.startswith("FIN-") and first.duplicate is False
        assert (again.reference, again.duplicate) == (first.reference, True)
        with pytest.raises(McpToolError):
            await ports_.finance.submit_claim(a_claim(total=1.0), idempotency_key="submit-clm-001")

        assert (
            await ports_.finance.decide_claim(
                first.reference, approved=True, approver_id="DEMO-RAVI"
            )
            == "approved"
        )
        with pytest.raises(McpToolError, match="already approved"):
            await ports_.finance.decide_claim(
                first.reference, approved=False, approver_id="DEMO-RAVI", comment="x"
            )


# -- the bridge <-> both servers -------------------------------------------------------------------


async def test_every_real_tool_converts_to_a_strict_claude_tool(servers):
    async with servers() as env:
        bridge = McpBridge({"finance": env.finance(), "corp": env.corp()})
        tools = await bridge.tools()
    assert len(tools) == 9
    for tool in tools:
        assert tool["strict"] is True, tool["name"]
        assert tool["input_schema"]["type"] == "object"
        assert tool["input_schema"]["additionalProperties"] is False, tool["name"]
        assert tool["description"], tool["name"]
    assert {t["name"] for t in tools} >= EMPLOYEE_AGENT_TOOLS  # every allow-listed name exists


async def test_the_employee_agent_flow_through_the_bridge(servers):
    async with servers() as env:
        bridge = McpBridge(
            {"finance": env.finance(), "corp": env.corp()}, allow=EMPLOYEE_AGENT_TOOLS
        )
        offered = {t["name"] for t in await bridge.tools()}
        assert offered == EMPLOYEE_AGENT_TOOLS

        submit = await bridge.call(
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "submit_claim",
                "input": {
                    "claim_id": "clm-9",
                    "employee_id": "P001",
                    "title": "Hyderabad dinner",
                    "total": 3919.39,
                    "currency": "INR",
                    "document_ids": ["s42-0010"],
                    "idempotency_key": "agent-clm-9",
                },
            }
        )
        assert submit["is_error"] is False and '"duplicate":false' in submit["content"]
        reference = submit["content"].split('"reference":"')[1].split('"')[0]

        status = await bridge.call(
            {"id": "toolu_2", "name": "get_claim_status", "input": {"reference": reference}}
        )
        assert status["is_error"] is False and '"status":"received"' in status["content"]

        calendar = await bridge.call(
            {
                "id": "toolu_3",
                "name": "search_calendar",
                "input": {
                    "employee_id": "P004",
                    "start_date": "2026-07-07",
                    "end_date": "2026-07-07",
                },
            }
        )
        assert "Kesar Grill House, Kochi" in calendar["content"] and calendar["is_error"] is False

        policy = await bridge.call({"id": "toolu_4", "name": "get_policy", "input": {}})
        assert policy["content"] == POLICY_TEXT

        # a server-side rejection comes back as an error result the model can read
        bad = await bridge.call(
            {"id": "toolu_5", "name": "get_claim_status", "input": {"reference": "FIN-2026-009999"}}
        )
        assert bad["is_error"] is True and "no claim with reference" in bad["content"]

        # an approver action was never offered, and can never be executed through this bridge
        denied = await bridge.call(
            {
                "id": "toolu_6",
                "name": "decide_claim",
                "input": {"reference": reference, "decision": "approved", "approver_id": "P001"},
            }
        )
        assert denied["is_error"] is True and denied["content"] == "Unknown tool: decide_claim"
        assert (await env.finance().get_claim_status(reference)).status == "received"


# -- transport failures --------------------------------------------------------------------------


async def test_unknown_tools_and_resources_map_to_typed_errors(servers):
    async with servers() as env:
        corp = env.corp()
        with pytest.raises(McpToolError, match="Unknown tool: nope") as caught:
            await corp.call_tool("nope")
        assert (caught.value.server, caught.value.tool) == ("corp", "nope")
        with pytest.raises(McpToolError):  # arguments are checked against the tool's schema
            await corp.call_tool("get_employee", {"employee_id": 42})


async def test_a_server_that_is_not_there_is_unavailable_and_retryable():
    dead = StreamableHttpClient(
        f"http://127.0.0.1:{free_port()}/mcp", name="finance", timeout=3.0, connect_timeout=0.5
    )
    started = time.perf_counter()
    with pytest.raises(McpUnavailableError) as caught:
        await dead.list_tools()
    assert caught.value.retryable and caught.value.server == "finance"
    assert time.perf_counter() - started < 5
    with pytest.raises(McpUnavailableError):
        await FinanceClient(dead).get_claim_status("FIN-2026-000001")


async def test_a_wrong_path_or_a_non_mcp_endpoint_is_a_typed_error(servers):
    async with servers() as env:
        base = env.corp_url.removesuffix("/mcp")
        with pytest.raises(McpProtocolError, match="Not Found"):
            await StreamableHttpClient(f"{base}/nope", name="corp", timeout=3.0).list_tools()
        with pytest.raises(McpError):  # /healthz only answers GET: the POST gets a 405
            await StreamableHttpClient(f"{base}/healthz", name="corp", timeout=3.0).list_tools()


async def test_a_slow_tool_times_out_as_unavailable():
    server = MCPServer("slow")

    @server.tool()
    async def nap(seconds: float) -> str:
        """Sleep, then answer."""
        await asyncio.sleep(seconds)
        return "awake"

    app = server.streamable_http_app(json_response=True, stateless_http=True, host="127.0.0.1")
    async with serve(app) as url:
        patient = StreamableHttpClient(url, name="slow", timeout=5.0)
        assert await patient.call_tool("nap", {"seconds": 0.05}) == {"result": "awake"}
        impatient = StreamableHttpClient(url, name="slow", timeout=0.4)
        started = time.perf_counter()
        with pytest.raises(McpUnavailableError, match=r"timed out|failed") as caught:
            await impatient.call_tool("nap", {"seconds": 3})
        assert caught.value.retryable
        assert time.perf_counter() - started < 2.5
