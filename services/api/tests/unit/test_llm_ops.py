"""The AI-operations numbers: percentiles, error kinds, the live/recorded split, route tables."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from claimpilot.db import LlmCall
from claimpilot.llm.ops import (
    BUDGET,
    MESSAGE_CHARS,
    NOT_RECORDED,
    OpsRoute,
    OpsTotals,
    call_of,
    classify_error,
    failure_of,
    percentile,
    summarise,
)

NOON = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def row(
    route: str = "extraction",
    model_key: str = "haiku",
    *,
    mode: str = "live",
    cost: float = 0.0,
    latency: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    error: str | None = None,
    **ids: str,
) -> LlmCall:
    """An unsaved ledger row: ``summarise`` and the mappers only read its columns."""
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
        cache_read_tokens=cache_read,
        error=error,
        created_at=NOON,
        **ids,
    )


# --- percentile --------------------------------------------------------------------------------


def test_percentile_of_nothing_is_zero():
    assert percentile([], 0.5) == 0
    assert percentile([], 0.95) == 0


def test_percentile_of_one_value_is_that_value():
    assert percentile([42], 0.0) == 42
    assert percentile([42], 0.5) == 42
    assert percentile([42], 0.95) == 42
    assert percentile([42], 1.0) == 42


def test_percentile_is_nearest_rank():
    values = list(range(1, 101))
    assert percentile(values, 0.50) == 50
    assert percentile(values, 0.95) == 95
    assert percentile(values, 0.99) == 99
    assert percentile(values, 1.0) == 100
    assert percentile(values, 0.0) == 1  # never indexes before the first value


def test_percentile_picks_a_real_value_never_an_average():
    assert percentile([10, 20], 0.5) == 10
    assert percentile([10, 20], 0.51) == 20
    assert percentile([100, 200, 300, 400], 0.5) == 200
    assert percentile([100, 200, 300, 400, 500], 0.5) == 300
    assert percentile([100, 200, 300, 400, 500], 0.95) == 500


def test_percentile_sorts_its_input_and_leaves_it_alone():
    values = [900, 100, 500, 300, 700]
    assert percentile(values, 0.5) == 500
    assert percentile(values, 0.95) == 900
    assert values == [900, 100, 500, 300, 700]


# --- classify_error ----------------------------------------------------------------------------


def test_a_missing_recording_is_not_a_real_failure():
    error = r"ReplayMissError: no recording for abc123 in C:\app\replay\extraction"
    assert classify_error(error) == ("not_recorded", NOT_RECORDED)


def test_a_used_up_budget_is_not_a_real_failure():
    assert classify_error("LLMBudgetError: spent 5.01 of 5.00 USD today") == ("budget", BUDGET)


def test_the_kind_is_read_from_the_exception_name_only():
    assert classify_error("ReplayMissError")[0] == "not_recorded"  # no detail after the name
    assert classify_error("  LLMBudgetError : padded")[0] == "budget"
    assert classify_error("RuntimeError: ReplayMissError was raised")[0] == "error"
    assert classify_error("NotReplayMissError: x")[0] == "error"


def test_any_other_error_is_kept_but_without_server_paths():
    kind, message = classify_error(r"FileNotFoundError: C:\Users\svc\app\replay\x.json: missing")
    assert kind == "error"
    assert message == "FileNotFoundError: <path>: missing"

    kind, message = classify_error("OSError: cannot read /srv/claimpilot/data/receipt 1.png: busy")
    assert kind == "error"
    assert message == "OSError: cannot read <path>: busy"


def test_every_path_in_an_error_is_scrubbed():
    _, message = classify_error(r"copy C:\in\a.png:/var/out/b.png: denied")
    assert "in\\a" not in message and "var/out" not in message
    assert message.count("<path>") == 2


def test_an_error_without_a_path_is_kept_as_is():
    assert classify_error("TimeoutError: upstream timed out") == (
        "error",
        "TimeoutError: upstream timed out",
    )


def test_a_long_error_is_cut_to_the_message_limit():
    kind, message = classify_error("ValueError: " + "x" * 400)
    assert kind == "error"
    assert len(message) == MESSAGE_CHARS == 160
    assert message.startswith("ValueError: xxx")


def test_a_path_is_scrubbed_before_the_cut():
    """Cutting first could leave half a path behind, past the scrub."""
    _, message = classify_error("E: " + "a" * 150 + " /secret/dir/file.txt")
    assert "secret" not in message and "dir" not in message


def test_an_empty_error_is_an_error_with_no_message():
    assert classify_error("") == ("error", "")


# --- summarise: totals -------------------------------------------------------------------------


def test_an_empty_ledger_summarises_to_zeros():
    totals, routes = summarise([])
    assert totals == OpsTotals(
        calls=0,
        live_calls=0,
        recorded_calls=0,
        errors=0,
        not_recorded=0,
        budget_refusals=0,
        error_rate=0.0,
        live_cost_usd=0.0,
        recorded_cost_usd=0.0,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_read_share=0.0,
    )
    assert routes == []


def test_live_and_recorded_calls_and_cost_are_kept_apart():
    rows = [
        row(mode="live", cost=0.25),
        row(mode="live", cost=0.50),
        row(mode="replay", cost=0.125),
        row(mode="fake", cost=1.0),
    ]
    totals, _ = summarise(rows)
    assert totals.calls == 4
    assert totals.live_calls == 2 and totals.recorded_calls == 2
    assert totals.live_cost_usd == pytest.approx(0.75)
    assert totals.recorded_cost_usd == pytest.approx(1.125)  # the would-be cost, not spend


def test_replayed_calls_never_add_to_the_money_actually_spent():
    totals, routes = summarise([row(mode="replay", cost=9.0), row(mode="replay", cost=1.0)])
    assert totals.live_cost_usd == 0.0 and totals.recorded_cost_usd == pytest.approx(10.0)
    assert routes[0].live_cost_usd == 0.0 and routes[0].recorded_cost_usd == pytest.approx(10.0)


def test_failures_are_counted_by_kind_and_missing_recordings_are_not_errors():
    rows = [
        row(),
        row(error="TimeoutError: slow"),
        row(error="ValueError: bad json"),
        row(mode="replay", error="ReplayMissError: no recording for h"),
        row(mode="replay", error="ReplayMissError: no recording for g"),
        row(error="LLMBudgetError: used up"),
    ]
    totals, routes = summarise(rows)
    assert totals.calls == 6
    assert totals.errors == 2
    assert totals.not_recorded == 2
    assert totals.budget_refusals == 1
    assert totals.error_rate == pytest.approx(2 / 6)  # of all calls, and only real failures
    assert routes[0].errors == 2


def test_error_rate_is_zero_without_calls_and_without_failures():
    assert summarise([])[0].error_rate == 0.0
    assert summarise([row(), row()])[0].error_rate == 0.0


def test_error_rate_can_reach_one():
    totals, _ = summarise([row(error="boom"), row(error="boom")])
    assert totals.error_rate == 1.0


def test_tokens_are_summed():
    rows = [
        row(input_tokens=100, output_tokens=10, cache_read=300),
        row(input_tokens=50, output_tokens=5, cache_read=0),
        row(mode="replay", input_tokens=25, output_tokens=2, cache_read=25),
    ]
    totals, _ = summarise(rows)
    assert totals.input_tokens == 175
    assert totals.output_tokens == 17
    assert totals.cache_read_tokens == 325


def test_cache_read_share_is_cached_over_all_prompt_tokens():
    totals, _ = summarise([row(input_tokens=100, cache_read=300)])
    assert totals.cache_read_share == pytest.approx(0.75)
    totals, _ = summarise([row(input_tokens=100, cache_read=0)])
    assert totals.cache_read_share == 0.0
    totals, _ = summarise([row(input_tokens=0, cache_read=500)])
    assert totals.cache_read_share == 1.0


def test_cache_read_share_is_zero_when_there_are_no_prompt_tokens():
    totals, _ = summarise([row(output_tokens=40), row()])
    assert totals.cache_read_share == 0.0


def test_summarise_accepts_any_iterable_once():
    totals, routes = summarise(r for r in [row(cost=1.0), row(cost=2.0)])
    assert totals.calls == 2 and routes[0].calls == 2


# --- summarise: route x model ------------------------------------------------------------------


def test_routes_are_grouped_by_route_and_model_and_ordered_by_name():
    rows = [
        row("reply", "sonnet"),
        row("extraction", "sonnet"),
        row("extraction", "haiku"),
        row("extraction", "haiku"),
        row("decision", "haiku"),
    ]
    _, routes = summarise(rows)
    assert [(r.route, r.model_key, r.calls) for r in routes] == [
        ("decision", "haiku", 1),
        ("extraction", "haiku", 2),
        ("extraction", "sonnet", 1),
        ("reply", "sonnet", 1),
    ]


def test_a_route_row_splits_live_from_recorded_and_counts_only_real_errors():
    rows = [
        row(mode="live", cost=0.5, error="boom"),
        row(mode="live", cost=0.25),
        row(mode="replay", cost=0.125, error="ReplayMissError: none"),
        row(mode="fake", cost=0.0625),
        row(mode="live", error="LLMBudgetError: used up"),
    ]
    _, routes = summarise(rows)
    assert len(routes) == 1
    assert routes[0] == OpsRoute(
        route="extraction",
        model_key="haiku",
        calls=5,
        live_calls=3,
        errors=1,
        p50_ms=0,
        p95_ms=0,
        live_cost_usd=0.75,
        recorded_cost_usd=0.1875,
    )


def test_each_route_has_its_own_latency_percentiles():
    slow = [row("extraction", "haiku", latency=ms) for ms in range(1, 101)]
    fast = [row("reply", "haiku", latency=ms) for ms in (5, 9, 7)]
    _, routes = summarise([*fast, *slow])
    by_route = {r.route: r for r in routes}
    assert (by_route["extraction"].p50_ms, by_route["extraction"].p95_ms) == (50, 95)
    assert (by_route["reply"].p50_ms, by_route["reply"].p95_ms) == (7, 9)


def test_route_figures_add_up_to_the_totals():
    rows = [
        row("a", "haiku", cost=0.5, input_tokens=10),
        row("a", "sonnet", mode="replay", cost=0.25),
        row("b", "haiku", cost=1.0, error="boom"),
    ]
    totals, routes = summarise(rows)
    assert sum(r.calls for r in routes) == totals.calls
    assert sum(r.live_calls for r in routes) == totals.live_calls
    assert sum(r.errors for r in routes) == totals.errors
    assert sum(r.live_cost_usd for r in routes) == pytest.approx(totals.live_cost_usd)
    assert sum(r.recorded_cost_usd for r in routes) == pytest.approx(totals.recorded_cost_usd)


# --- failure_of / call_of ----------------------------------------------------------------------


def test_a_failure_carries_the_ids_that_find_the_upload():
    failing = row(
        "extraction",
        "sonnet",
        error="ValueError: bad",
        trace_id="t-1",
        batch_id="b-1",
        document_id="d-1",
    )
    failure = failure_of(failing)
    assert failure.at == NOON
    assert (failure.route, failure.model_key) == ("extraction", "sonnet")
    assert failure.kind == "error" and failure.message == "ValueError: bad"
    assert (failure.trace_id, failure.batch_id, failure.document_id) == ("t-1", "b-1", "d-1")


def test_a_failure_without_ids_has_none():
    failure = failure_of(row(error="boom"))
    assert (failure.trace_id, failure.batch_id, failure.document_id) == (None, None, None)


def test_a_failure_message_is_the_safe_one():
    miss = failure_of(row(error=r"ReplayMissError: no recording in C:\srv\replay"))
    assert (miss.kind, miss.message) == ("not_recorded", NOT_RECORDED)
    budget = failure_of(row(error="LLMBudgetError: 5.2 > 5"))
    assert (budget.kind, budget.message) == ("budget", BUDGET)
    path = failure_of(row(error="OSError: /srv/app/data/x.bin: busy"))
    assert path.message == "OSError: <path>: busy"


def test_a_call_maps_its_columns():
    ok = call_of(
        row(
            "reply",
            "haiku",
            mode="replay",
            cost=0.125,
            latency=840,
            document_id="d-1",
            claim_id="clm-1",
        )
    )
    assert ok.at == NOON
    assert (ok.route, ok.model_key, ok.mode) == ("reply", "haiku", "replay")
    assert (ok.latency_ms, ok.cost_usd) == (840, 0.125)
    assert (ok.document_id, ok.claim_id) == ("d-1", "clm-1")
    assert ok.error is None


def test_a_call_shows_the_safe_message_never_the_raw_error():
    raw = r"ReplayMissError: no recording at C:\srv\replay\extraction\abc.json"
    shown = call_of(row(error=raw))
    assert shown.error == NOT_RECORDED
    assert "srv" not in shown.model_dump_json()

    other = call_of(row(error="OSError: /srv/app/secret.key: denied"))
    assert other.error == "OSError: <path>: denied"
    assert "secret" not in other.model_dump_json()


def test_a_call_with_an_empty_error_text_shows_no_error():
    assert call_of(row(error="")).error is None
