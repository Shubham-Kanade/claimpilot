"""`/v1/ops/llm`: the AI-operations view. System-wide figures, but only your own failures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from claimpilot.db import LlmCall
from claimpilot.llm.ops import BUDGET, NOT_RECORDED

from .conftest import ASHA, MEERA, World, as_persona

A = "sandbox-aaaaaaaaaaaaaaaa"
B = "sandbox-bbbbbbbbbbbbbbbb"
URL = "/v1/ops/llm"


def headers(sandbox: str | None = None) -> dict[str, str]:
    """What a demo visitor sends (``None``: the shared world, so persona only)."""
    return {**as_persona(ASHA), **({"X-Sandbox": sandbox} if sandbox else {})}


def demo_on(world: World) -> None:
    world.container.settings.demo_mode = True


def ago(**delta: float) -> datetime:
    return datetime.now(UTC) - timedelta(**delta)


def call(
    *,
    route: str = "extraction",
    model_key: str = "haiku",
    mode: str = "live",
    cost: float = 0.0,
    latency: int = 100,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    error: str | None = None,
    sandbox: str | None = None,
    at: datetime | None = None,
    **ids: str,
) -> LlmCall:
    return LlmCall(
        route=route,
        model_key=model_key,
        model_id="m",
        mode=mode,
        request_hash="h",
        cost_usd=cost,
        latency_ms=latency,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        error=error,
        sandbox=sandbox,
        created_at=at or ago(minutes=5),
        **ids,
    )


async def put(world: World, *rows: LlmCall) -> None:
    async with world.container.sessions() as session:
        session.add_all(rows)
        await session.commit()


async def ops(http: AsyncClient, sandbox: str | None = None, **params: str | int) -> dict:
    resp = await http.get(URL, params=params, headers=headers(sandbox))
    assert resp.status_code == 200, resp.text
    return resp.json()


def naive(stamp: str) -> datetime:
    """A response timestamp without its zone (sqlite hands datetimes back naive)."""
    return datetime.fromisoformat(stamp).replace(tzinfo=None)


# --- the empty ledger and the shape --------------------------------------------------------------


async def test_an_empty_ledger_gives_zeros_and_empty_lists(http: AsyncClient):
    body = await ops(http)
    assert body["hours"] == 24
    assert body["since"] is None and body["sampled"] is False
    assert body["routes"] == [] and body["failures"] == [] and body["trace_calls"] == []
    assert body["totals"] == {
        "calls": 0,
        "live_calls": 0,
        "recorded_calls": 0,
        "errors": 0,
        "not_recorded": 0,
        "budget_refusals": 0,
        "error_rate": 0.0,
        "live_cost_usd": 0.0,
        "recorded_cost_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_read_share": 0.0,
    }


async def test_the_view_needs_a_known_persona(http: AsyncClient):
    resp = await http.get(URL)
    assert resp.status_code == 401 and resp.json()["type"] == "missing_persona"
    resp = await http.get(URL, headers={"X-Persona": "nobody"})
    assert resp.status_code == 401 and resp.json()["type"] == "unknown_persona"


async def test_any_persona_may_look_not_only_approvers(http: AsyncClient):
    resp = await http.get(URL, headers=as_persona(MEERA))
    assert resp.status_code == 200


async def test_the_numbers_and_the_route_table_describe_the_ledger(http: AsyncClient, world: World):
    await put(
        world,
        call(route="extraction", model_key="haiku", cost=0.25, latency=100),
        call(route="extraction", model_key="haiku", mode="replay", cost=0.5, latency=300),
        call(route="reply", model_key="sonnet", cost=1.0, latency=900, error="TimeoutError: slow"),
    )

    body = await ops(http)

    totals = body["totals"]
    assert totals["calls"] == 3 and totals["live_calls"] == 2 and totals["recorded_calls"] == 1
    assert totals["live_cost_usd"] == pytest.approx(1.25)
    assert totals["recorded_cost_usd"] == pytest.approx(0.5)
    assert totals["errors"] == 1 and totals["error_rate"] == pytest.approx(1 / 3)
    assert [(r["route"], r["model_key"], r["calls"]) for r in body["routes"]] == [
        ("extraction", "haiku", 2),
        ("reply", "sonnet", 1),
    ]
    assert body["routes"][0]["p50_ms"] == 100 and body["routes"][0]["p95_ms"] == 300
    assert body["routes"][1]["errors"] == 1


async def test_tokens_and_the_cache_share_come_through(http: AsyncClient, world: World):
    await put(
        world,
        call(input_tokens=100, output_tokens=10, cache_read_tokens=300),
    )
    totals = (await ops(http))["totals"]
    assert (totals["input_tokens"], totals["output_tokens"]) == (100, 10)
    assert totals["cache_read_tokens"] == 300 and totals["cache_read_share"] == 0.75


# --- who sees what -------------------------------------------------------------------------------


async def test_totals_cover_every_sandbox_but_failures_only_your_own(
    http: AsyncClient, world: World
):
    demo_on(world)
    await put(
        world,
        call(sandbox=A, cost=0.25, error="ValueError: mine", trace_id="t-a"),
        call(sandbox=B, cost=0.5, error="ValueError: theirs", trace_id="t-b"),
        call(sandbox=None, cost=1.0, error="ValueError: shared", trace_id="t-n"),
    )

    for sandbox, own in ((A, "mine"), (B, "theirs"), (None, "shared")):
        body = await ops(http, sandbox)
        assert body["totals"]["calls"] == 3  # system-wide
        assert body["totals"]["live_cost_usd"] == pytest.approx(1.75)
        assert body["totals"]["errors"] == 3
        assert [f["message"] for f in body["failures"]] == [f"ValueError: {own}"]


async def test_the_shared_world_sees_only_rows_without_a_sandbox(http: AsyncClient, world: World):
    await put(
        world,
        call(sandbox=A, error="ValueError: in a sandbox"),
        call(sandbox=None, error="ValueError: shared"),
    )
    body = await ops(http)
    assert [f["message"] for f in body["failures"]] == ["ValueError: shared"]
    assert body["totals"]["calls"] == 2


async def test_a_sandbox_header_is_ignored_outside_the_demo(http: AsyncClient, world: World):
    await put(
        world,
        call(sandbox=A, error="ValueError: in a sandbox"),
        call(sandbox=None, error="ValueError: shared"),
    )
    body = await ops(http, A)  # demo mode is off: the header means nothing
    assert [f["message"] for f in body["failures"]] == ["ValueError: shared"]


async def test_a_bad_sandbox_id_is_refused_in_the_demo(http: AsyncClient, world: World):
    demo_on(world)
    resp = await http.get(URL, headers={**as_persona(ASHA), "X-Sandbox": "short"})
    assert resp.status_code == 400 and resp.json()["type"] == "invalid_sandbox"


async def test_a_visitor_with_no_failures_sees_none_of_the_others(http: AsyncClient, world: World):
    demo_on(world)
    await put(world, call(sandbox=B, error="ValueError: theirs", document_id="d-theirs"))
    body = await ops(http, A)
    assert body["failures"] == []
    assert "theirs" not in str(body)
    assert body["totals"]["errors"] == 1  # but they are part of the system's numbers


# --- the window ----------------------------------------------------------------------------------


async def test_hours_limits_the_window(http: AsyncClient, world: World):
    await put(
        world,
        call(at=ago(minutes=30), cost=1.0),
        call(at=ago(hours=30), cost=2.0),
        call(at=ago(hours=100), cost=4.0),
    )

    assert (await ops(http))["totals"]["calls"] == 1  # the default is 24 hours
    assert (await ops(http, hours=1))["totals"]["calls"] == 1
    assert (await ops(http, hours=48))["totals"]["live_cost_usd"] == pytest.approx(3.0)
    wide = await ops(http, hours=168)
    assert wide["totals"]["live_cost_usd"] == pytest.approx(7.0)
    assert wide["hours"] == 168


async def test_an_old_failure_leaves_the_failure_list_with_its_window(
    http: AsyncClient, world: World
):
    await put(world, call(at=ago(hours=30), error="ValueError: yesterday"))
    assert (await ops(http, hours=24))["failures"] == []
    assert len((await ops(http, hours=48))["failures"]) == 1


async def test_since_is_the_oldest_call_in_the_window(http: AsyncClient, world: World):
    oldest = ago(hours=10)
    await put(world, call(at=ago(hours=2)), call(at=oldest), call(at=ago(hours=5)))
    await put(world, call(at=ago(hours=60)))  # outside the 24 hours
    since = naive((await ops(http))["since"])
    assert since == oldest.replace(tzinfo=None)


@pytest.mark.parametrize("hours", [0, -1, 169, 1000])
async def test_hours_outside_one_to_168_is_refused(http: AsyncClient, hours: int):
    resp = await http.get(URL, params={"hours": hours}, headers=headers())
    assert resp.status_code == 422


async def test_hours_must_be_a_number(http: AsyncClient):
    resp = await http.get(URL, params={"hours": "many"}, headers=headers())
    assert resp.status_code == 422


@pytest.mark.parametrize("hours", [1, 168])
async def test_the_ends_of_the_range_are_allowed(http: AsyncClient, hours: int):
    assert (await ops(http, hours=hours))["hours"] == hours


# --- the cap -------------------------------------------------------------------------------------


async def test_a_full_window_covers_the_newest_calls_and_says_it_is_sampled(
    http: AsyncClient, world: World, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("claimpilot.api.ops.MAX_ROWS", 3)
    stamps = [ago(hours=5 - i) for i in range(5)]
    await put(world, *(call(at=at, cost=float(10**i)) for i, at in enumerate(stamps)))
    # Costs 1, 10, 100, 1000, 10000 from the oldest to the newest call.

    body = await ops(http)

    assert body["sampled"] is True
    assert body["totals"]["calls"] == 3
    assert body["totals"]["live_cost_usd"] == pytest.approx(11100.0)  # the newest three
    assert naive(body["since"]) == stamps[2].replace(tzinfo=None)


async def test_since_is_the_oldest_of_the_sampled_calls(
    http: AsyncClient, world: World, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("claimpilot.api.ops.MAX_ROWS", 2)
    stamps = [ago(hours=h) for h in (9, 6, 3, 1)]
    await put(world, *(call(at=at) for at in stamps))
    body = await ops(http)
    assert body["sampled"] is True and body["totals"]["calls"] == 2
    assert naive(body["since"]) == stamps[2].replace(tzinfo=None)


async def test_a_window_under_the_cap_is_not_sampled(
    http: AsyncClient, world: World, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("claimpilot.api.ops.MAX_ROWS", 3)
    await put(world, call(), call())
    body = await ops(http)
    assert body["sampled"] is False and body["totals"]["calls"] == 2


async def test_a_window_that_just_fills_the_cap_is_reported_as_sampled(
    http: AsyncClient, world: World, monkeypatch: pytest.MonkeyPatch
):
    """Exactly the cap could mean there were more: say so rather than claim a full count."""
    monkeypatch.setattr("claimpilot.api.ops.MAX_ROWS", 3)
    await put(world, call(), call(), call())
    assert (await ops(http))["sampled"] is True


# --- failures ------------------------------------------------------------------------------------


async def test_at_most_ten_failures_newest_first(http: AsyncClient, world: World):
    await put(world, *(call(at=ago(minutes=60 - i), error=f"ValueError: e{i}") for i in range(12)))
    await put(world, call(at=ago(minutes=1)))  # a success is not a failure

    body = await ops(http)

    assert [f["message"] for f in body["failures"]] == [
        f"ValueError: e{i}" for i in range(11, 1, -1)
    ]
    assert body["totals"]["calls"] == 13 and body["totals"]["errors"] == 12  # totals are not cut


async def test_a_failure_names_the_call_and_its_ids(http: AsyncClient, world: World):
    await put(
        world,
        call(
            route="extraction",
            model_key="sonnet",
            error="ValueError: bad json",
            trace_id="trace-1",
            batch_id="batch-1",
            document_id="doc-1",
        ),
    )
    (failure,) = (await ops(http))["failures"]
    assert failure["kind"] == "error" and failure["message"] == "ValueError: bad json"
    assert (failure["route"], failure["model_key"]) == ("extraction", "sonnet")
    assert failure["trace_id"] == "trace-1" and failure["batch_id"] == "batch-1"
    assert failure["document_id"] == "doc-1"
    assert failure["at"]


async def test_a_missing_recording_and_a_used_up_budget_are_listed_but_not_errors(
    http: AsyncClient, world: World
):
    await put(
        world,
        call(at=ago(minutes=3), mode="replay", error="ReplayMissError: no recording for h"),
        call(at=ago(minutes=2), error="LLMBudgetError: 5.01 of 5.00 used"),
        call(at=ago(minutes=1), error="ValueError: real"),
    )

    body = await ops(http)

    assert [(f["kind"], f["message"]) for f in body["failures"]] == [
        ("error", "ValueError: real"),
        ("budget", BUDGET),
        ("not_recorded", NOT_RECORDED),
    ]
    totals = body["totals"]
    assert (totals["errors"], totals["not_recorded"], totals["budget_refusals"]) == (1, 1, 1)
    assert totals["error_rate"] == pytest.approx(1 / 3)


async def test_a_server_path_never_reaches_the_caller(http: AsyncClient, world: World):
    await put(
        world,
        call(
            at=ago(minutes=3),
            mode="replay",
            error=r"ReplayMissError: no recording C:\srv\claimpilot\replay\extraction\a1.json",
            trace_id="t-1",
        ),
        call(
            at=ago(minutes=2),
            mode="replay",
            error="ReplayMissError: no recording /app/replay/extraction/a2.json",
            trace_id="t-1",
        ),
        call(
            at=ago(minutes=1),
            error=r"FileNotFoundError: C:\Users\svc\data\x.json: missing",
            trace_id="t-1",
        ),
        call(at=ago(seconds=30), error="OSError: /var/lib/claimpilot/x.png: busy", trace_id="t-1"),
    )

    resp = await http.get(URL, params={"trace_id": "t-1"}, headers=headers())

    assert resp.status_code == 200
    for leaked in (
        "srv",
        "extraction/a2",
        "/app",
        "/var",
        "lib",
        "Users",
        "svc",
        "a1.json",
        "x.png",
    ):
        assert leaked not in resp.text, leaked
    body = resp.json()
    assert "<path>" in body["failures"][0]["message"] + body["failures"][1]["message"]
    assert {f["message"] for f in body["failures"]} >= {NOT_RECORDED}
    assert {c["error"] for c in body["trace_calls"]} >= {NOT_RECORDED}


async def test_a_long_error_is_cut_before_it_is_shown(http: AsyncClient, world: World):
    await put(world, call(error="ValueError: " + "x" * 400))
    (failure,) = (await ops(http))["failures"]
    assert len(failure["message"]) == 160


# --- one trace -----------------------------------------------------------------------------------


async def test_a_trace_lists_its_calls_oldest_first(http: AsyncClient, world: World):
    demo_on(world)
    await put(
        world,
        call(sandbox=A, trace_id="t-1", route="decision", at=ago(minutes=1), document_id="d-1"),
        call(sandbox=A, trace_id="t-1", route="extraction", at=ago(minutes=3), document_id="d-1"),
        call(
            sandbox=A,
            trace_id="t-1",
            route="reply",
            at=ago(minutes=2),
            mode="replay",
            cost=0.5,
            latency=700,
            claim_id="clm-1",
            error="ValueError: slip",
        ),
        call(sandbox=A, trace_id="t-2", route="extraction"),
    )

    body = await ops(http, A, trace_id="t-1")

    assert [c["route"] for c in body["trace_calls"]] == ["extraction", "reply", "decision"]
    reply = body["trace_calls"][1]
    assert reply["mode"] == "replay" and reply["latency_ms"] == 700 and reply["cost_usd"] == 0.5
    assert reply["claim_id"] == "clm-1" and reply["error"] == "ValueError: slip"
    assert (
        body["trace_calls"][0]["document_id"] == "d-1" and body["trace_calls"][0]["error"] is None
    )
    assert body["totals"]["calls"] == 4  # the trace narrows the drill-down, not the figures


async def test_no_trace_id_means_no_trace_calls(http: AsyncClient, world: World):
    await put(world, call(trace_id="t-1"))
    assert (await ops(http))["trace_calls"] == []
    assert (await ops(http, trace_id=""))["trace_calls"] == []


async def test_an_unknown_trace_has_no_calls(http: AsyncClient, world: World):
    await put(world, call(trace_id="t-1"))
    assert (await ops(http, trace_id="t-none"))["trace_calls"] == []


async def test_another_visitors_trace_shows_nothing(http: AsyncClient, world: World):
    demo_on(world)
    await put(
        world,
        call(sandbox=B, trace_id="t-b", error="ValueError: theirs", document_id="d-theirs"),
        call(sandbox=None, trace_id="t-n"),
    )

    for sandbox in (A, None):
        body = await ops(http, sandbox, trace_id="t-b")
        assert body["trace_calls"] == []
        assert "d-theirs" not in str(body) and "theirs" not in str(body)

    assert len((await ops(http, B, trace_id="t-b"))["trace_calls"]) == 1
    assert (await ops(http, A, trace_id="t-n"))["trace_calls"] == []  # nor the shared world's
    assert len((await ops(http, None, trace_id="t-n"))["trace_calls"]) == 1


async def test_a_trace_id_is_not_bound_by_the_window(http: AsyncClient, world: World):
    """The drill-down looks the trace up directly, so an old trace still opens."""
    await put(world, call(trace_id="t-old", at=ago(hours=100)))
    body = await ops(http, hours=1, trace_id="t-old")
    assert body["totals"]["calls"] == 0
    assert len(body["trace_calls"]) == 1


async def test_a_trace_shows_at_most_a_hundred_calls_oldest_first(http: AsyncClient, world: World):
    await put(
        world,
        *(call(trace_id="t-big", at=ago(minutes=200 - i), latency=i) for i in range(105)),
    )
    calls = (await ops(http, trace_id="t-big"))["trace_calls"]
    assert [c["latency_ms"] for c in calls] == list(range(100))


async def test_a_trace_id_over_64_characters_is_refused(http: AsyncClient):
    resp = await http.get(URL, params={"trace_id": "t" * 65}, headers=headers())
    assert resp.status_code == 422
    assert (
        await http.get(URL, params={"trace_id": "t" * 64}, headers=headers())
    ).status_code == 200


# --- the contract --------------------------------------------------------------------------------


async def test_the_route_is_in_the_openapi_document_with_its_response_schema(http: AsyncClient):
    spec = (await http.get("/openapi.json")).json()

    operation = spec["paths"][URL]["get"]
    assert "ops" in operation["tags"]
    parameters = {p["name"]: p for p in operation["parameters"] if p["in"] == "query"}
    assert {"hours", "trace_id"} <= set(parameters)
    assert parameters["hours"]["schema"]["minimum"] == 1
    assert parameters["hours"]["schema"]["maximum"] == 168
    assert parameters["hours"]["required"] is False
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/LlmOps")
    schema = spec["components"]["schemas"]["LlmOps"]
    assert set(schema["properties"]) == {
        "hours",
        "since",
        "sampled",
        "totals",
        "routes",
        "failures",
        "trace_calls",
    }
    assert {"OpsTotals", "OpsRoute", "OpsFailure", "OpsCall"} <= set(spec["components"]["schemas"])
