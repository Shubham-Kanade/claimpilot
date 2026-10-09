import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { makeOps } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { problem, server, url } from "@/test/server";

import { OperationsScreen } from "./OperationsScreen";

const FAILURES = [
  {
    at: "2026-10-09T09:15:00",
    route: "extraction",
    model_key: "haiku",
    kind: "not_recorded" as const,
    message: "This demo reads only its recorded sample receipts.",
    trace_id: "trace-aaaaaaaaaaaa-1",
    batch_id: "b1",
    document_id: "d1",
  },
  {
    at: "2026-10-09T09:16:00",
    route: "extraction",
    model_key: "haiku",
    kind: "budget" as const,
    message: "The daily AI budget is used up.",
    trace_id: null,
    batch_id: null,
    document_id: null,
  },
  {
    at: "2026-10-09T09:17:00",
    route: "agent_chat",
    model_key: "sonnet",
    kind: "error" as const,
    message: "Upstream timeout",
    trace_id: "trace-bbbbbbbbbbbb-2",
    batch_id: "b2",
    document_id: "d2",
  },
];

/** Serves /v1/ops/llm and records the query strings it was asked with. */
function serveOps(make: (params: URLSearchParams) => ReturnType<typeof makeOps>) {
  const asked: string[] = [];
  server.use(
    http.get(url("/v1/ops/llm"), ({ request }) => {
      const params = new URL(request.url).searchParams;
      asked.push(params.toString());
      return HttpResponse.json(make(params));
    }),
  );
  return asked;
}

describe("OperationsScreen", () => {
  it("shows the heading, the explainer, a window selector (24 h by default) and the KPI tiles", async () => {
    const asked = serveOps(() => makeOps());
    renderApp(<OperationsScreen />);
    expect(screen.getByRole("heading", { level: 1, name: "AI operations" })).toBeInTheDocument();
    expect(
      screen.getByText("How the AI calls are going: system-wide, all visitors."),
    ).toBeInTheDocument();
    const group = screen.getByRole("group", { name: "Time window" });
    expect(within(group).getByRole("button", { name: "24 h" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    expect(await screen.findByTestId("kpi-calls")).toHaveTextContent("200");
    expect(screen.getByTestId("kpi-live")).toHaveTextContent("25% live");
    expect(screen.getByTestId("kpi-live")).toHaveTextContent("50 live, 150 recorded (replayed)");
    expect(screen.getByTestId("kpi-errors")).toHaveTextContent("3");
    expect(screen.getByTestId("kpi-errors")).toHaveTextContent("Error rate 1.5%");
    expect(screen.getByTestId("kpi-errors")).toHaveTextContent("2 not recorded, 1 refused");
    // live money is the money; the recorded cost is explicitly "as recorded, not spent"
    expect(screen.getByTestId("kpi-cost")).toHaveTextContent("$0.12");
    expect(screen.getByTestId("kpi-cost")).toHaveTextContent(
      "Recorded replays would have cost $0.57: as recorded, not spent.",
    );
    expect(screen.getByTestId("kpi-cache")).toHaveTextContent("42.0%");
    expect(asked[0]).toBe("hours=24");
  });

  it("asks for the window that was picked", async () => {
    const user = userEvent.setup();
    const asked = serveOps((params) => makeOps({ hours: Number(params.get("hours")) }));
    renderApp(<OperationsScreen />);
    await screen.findByTestId("kpi-calls");
    await user.click(screen.getByRole("button", { name: "1 h" }));
    await waitFor(() => expect(asked).toContain("hours=1"));
    expect(screen.getByRole("button", { name: "1 h" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "7 days" }));
    await waitFor(() => expect(asked).toContain("hours=168"));
  });

  it("tells live columns from recorded ones in the route table, which scrolls in a focusable region", async () => {
    serveOps(() => makeOps());
    renderApp(<OperationsScreen />);
    const table = await screen.findByRole("table", { name: /AI calls per route and model/ });
    const headers = within(table)
      .getAllByRole("columnheader")
      .map((h) => h.textContent);
    expect(headers).toEqual([
      "Route",
      "Model",
      "Calls",
      "Live",
      "Recorded",
      "Errors",
      "p50",
      "p95",
      "Live cost",
      "Recorded cost (as recorded)",
    ]);
    const row = within(table).getByRole("row", { name: /Extraction/ });
    const cells = within(row)
      .getAllByRole("cell")
      .map((c) => c.textContent);
    expect(cells).toEqual(["haiku", "120", "30", "90", "2", "2.4 s", "5.2 s", "$0.09", "$0.40"]);
    const region = screen.getByRole("group", {
      name: "AI calls by route and model, scrolls sideways if needed",
    });
    expect(region).toHaveAttribute("tabindex", "0");
  });

  it("lists recent failures with a kind badge in words, the message and the time", async () => {
    serveOps(() => makeOps({ failures: FAILURES }));
    renderApp(<OperationsScreen />);
    const list = await screen.findByRole("list", { name: "Recent failures" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Not recorded");
    expect(items[0]).toHaveTextContent("This demo reads only its recorded sample receipts.");
    expect(items[0]).toHaveTextContent(/\d{1,2} Oct/);
    expect(items[1]).toHaveTextContent("Budget used up");
    expect(items[2]).toHaveTextContent("Error");
    // a failure without a trace has no trace button
    expect(within(items[1]).queryByRole("button")).not.toBeInTheDocument();
  });

  it("says so when nothing failed", async () => {
    serveOps(() => makeOps());
    renderApp(<OperationsScreen />);
    expect(await screen.findByText(/No failures in this window for your session/)).toBeVisible();
  });

  it("fills the trace field from a failure and shows that trace's calls (live vs recorded)", async () => {
    const user = userEvent.setup();
    const asked = serveOps((params) =>
      makeOps({
        failures: FAILURES,
        trace_calls: params.get("trace_id")
          ? [
              {
                at: "2026-10-09T09:15:00",
                route: "extraction",
                model_key: "haiku",
                mode: "recorded",
                latency_ms: 2300,
                cost_usd: 0.0012,
                error: null,
                document_id: "d1",
                claim_id: null,
              },
              {
                at: "2026-10-09T09:15:03",
                route: "decision_fallback",
                model_key: "haiku",
                mode: "live",
                latency_ms: 800,
                cost_usd: 0.0003,
                error: "Upstream timeout",
                document_id: "d1",
                claim_id: null,
              },
            ]
          : [],
      }),
    );
    renderApp(<OperationsScreen />);
    await user.click(await screen.findByRole("button", { name: /Show trace trace-aaaaaa/ }));
    expect(screen.getByLabelText("Trace id")).toHaveValue("trace-aaaaaaaaaaaa-1");
    await waitFor(() => expect(asked).toContain("hours=24&trace_id=trace-aaaaaaaaaaaa-1"));

    const table = await screen.findByRole("table", { name: /calls of one trace/i });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Recorded");
    expect(rows[0]).toHaveTextContent("2.3 s (as recorded)");
    expect(rows[0]).toHaveTextContent("$0.0012 (as recorded)");
    expect(rows[1]).toHaveTextContent("Live");
    expect(rows[1]).not.toHaveTextContent("as recorded");
    expect(rows[1]).toHaveTextContent("Upstream timeout");
    expect(screen.getByText(/2 calls in trace/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByLabelText("Trace id")).toHaveValue("");
    expect(screen.queryByRole("table", { name: /calls of one trace/i })).not.toBeInTheDocument();
  });

  it("looks a typed trace id up, and says when it has no calls in this session", async () => {
    const user = userEvent.setup();
    const asked = serveOps(() => makeOps());
    renderApp(<OperationsScreen />);
    await screen.findByTestId("kpi-calls");
    await user.type(screen.getByLabelText("Trace id"), "  nothing-here  ");
    await user.click(screen.getByRole("button", { name: "Show trace" }));
    await waitFor(() => expect(asked).toContain("hours=24&trace_id=nothing-here"));
    expect(await screen.findByText("No calls found for this trace in your session.")).toBeVisible();
  });

  it("notes since when it counts, and warns when only a sample is counted", async () => {
    serveOps(() => makeOps({ sampled: true }));
    renderApp(<OperationsScreen />);
    expect(await screen.findByText(/Counting since \d{1,2} Oct/)).toBeInTheDocument();
    expect(screen.getByText(/as recorded, never money spent/)).toBeInTheDocument();
    expect(screen.getByTestId("sampled-notice")).toHaveTextContent(
      "Sampled: only the newest 5,000 calls in this window are counted",
    );
  });

  it("does not show the sampling notice when everything is counted", async () => {
    serveOps(() => makeOps({ since: null }));
    renderApp(<OperationsScreen />);
    expect(await screen.findByText(/No calls counted yet\./)).toBeInTheDocument();
    expect(screen.queryByTestId("sampled-notice")).not.toBeInTheDocument();
  });

  it("has an empty state when no AI call happened in the window", async () => {
    serveOps(() =>
      makeOps({
        routes: [],
        totals: { ...makeOps().totals, calls: 0, live_calls: 0, recorded_calls: 0, errors: 0 },
      }),
    );
    renderApp(<OperationsScreen />);
    expect(await screen.findByText("No AI calls in this window yet")).toBeInTheDocument();
    expect(screen.queryByTestId("kpi-calls")).not.toBeInTheDocument();
  });

  it("shows a loading skeleton first, then a friendly error with retry", async () => {
    const user = userEvent.setup();
    let fail = true;
    server.use(
      http.get(url("/v1/ops/llm"), () =>
        fail ? problem(503, "http_503", "down") : HttpResponse.json(makeOps()),
      ),
    );
    renderApp(<OperationsScreen />);
    expect(screen.getByLabelText("Loading AI operations")).toHaveAttribute("aria-busy", "true");
    expect(await screen.findByRole("alert", {}, { timeout: 8000 })).toHaveTextContent(
      "Something went wrong on our side",
    );
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("kpi-calls")).toBeInTheDocument();
  }, 15_000);
});
