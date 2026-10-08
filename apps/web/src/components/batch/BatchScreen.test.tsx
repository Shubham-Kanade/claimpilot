import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { PipelineEvent } from "@/lib/api/types";
import { META, makeBatchView, makeClaim } from "@/test/fixtures";
import { router } from "@/test/navigation";
import { renderApp } from "@/test/render";
import { problem, server, url } from "@/test/server";
import { frame, streamOf } from "@/test/streams";

import { BatchScreen, elapsedSeconds } from "./BatchScreen";
import { batchReducer, initialBatchState } from "@/lib/batch/batchReducer";

const started: PipelineEvent = { type: "batch_started", batch_id: "bat-1", total: 2 };
const extracted = (id: string, position: number, merchant: string): PipelineEvent => ({
  type: "document_extracted",
  batch_id: "bat-1",
  document_id: id,
  filename: position === 0 ? "cab.png" : "hotel.png",
  position,
  doc_type: position === 0 ? "cab_receipt" : "hotel_folio",
  merchant,
  total: position === 0 ? 672.74 : 22680,
  category: position === 0 ? "local_conveyance" : "accommodation",
  category_confidence: 0.95,
  engine: "jev",
  cached: false,
  cost_usd: 0.0005,
});
const checked = (id: string, score = 100, verdict = "clean"): PipelineEvent => ({
  type: "document_checked",
  batch_id: "bat-1",
  document_id: id,
  trust_score: score,
  verdict,
  findings: score === 100 ? 0 : 2,
});
const done = (claims = 1, failed = 0): PipelineEvent => ({
  type: "batch_done",
  batch_id: "bat-1",
  processed: 2,
  failed,
  claims,
  cost_usd: 0.001,
});
const ready = (...ids: string[]): PipelineEvent => ({
  type: "claims_ready",
  batch_id: "bat-1",
  claim_ids: ids,
});

function serve(options: {
  batch?: ReturnType<typeof makeBatchView>;
  history?: PipelineEvent[];
  chunks?: string[];
  onEvents?: (lastEventId: string | null) => void;
}) {
  server.use(
    http.get(url("/v1/batches/bat-1"), () => HttpResponse.json(options.batch ?? makeBatchView())),
    http.get(url("/v1/batches/bat-1/history"), () => HttpResponse.json(options.history ?? [])),
    http.get(url("/v1/batches/bat-1/events"), ({ request }) => {
      options.onEvents?.(request.headers.get("Last-Event-ID"));
      return new HttpResponse(streamOf(...(options.chunks ?? [])), {
        headers: { "Content-Type": "text/event-stream" },
      });
    }),
  );
}

describe("BatchScreen", () => {
  it("shows each receipt moving from waiting to read to checked, then 'claims ready'", async () => {
    serve({
      chunks: [
        frame(0, started),
        frame(1, extracted("doc-000001", 0, "Raahi Cabs")),
        frame(2, checked("doc-000001")),
        frame(3, extracted("doc-000002", 1, "Lotus Bay Suites")),
        frame(4, checked("doc-000002", 60, "block")),
        frame(5, ready("clm-1")),
        frame(6, done(1)),
      ],
    });
    renderApp(<BatchScreen batchId="bat-1" autoOpenSeconds={600} />);

    expect(
      await screen.findByRole("heading", { level: 1, name: /1 claim ready/ }),
    ).toBeInTheDocument();
    const bar = screen.getByRole("progressbar", { name: "Overall progress" });
    expect(bar).toHaveAttribute("aria-valuenow", "100");

    const cards = Array.from(
      screen.getByRole("list", { name: "Receipts" }).querySelectorAll<HTMLElement>(":scope > li"),
    );
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByText("Raahi Cabs")).toBeInTheDocument();
    expect(within(cards[0]).getByText(/Looks genuine/)).toBeInTheDocument();
    expect(within(cards[1]).getByText("Lotus Bay Suites")).toBeInTheDocument();
    expect(within(cards[1]).getByText(/Blocked/)).toBeInTheDocument();
    expect(within(cards[1]).getByText("2 flags")).toBeInTheDocument();

    // running cost and elapsed time
    expect(await screen.findByText("LLM cost (recorded)")).toBeInTheDocument();
    expect(screen.getByText("$0.0010")).toBeInTheDocument();
    expect(screen.getByText("Elapsed")).toBeInTheDocument();

    // a single claim links straight to it; nothing is submitted
    const link = screen.getByRole("link", { name: /Review the claim/ });
    expect(link).toHaveAttribute("href", "/claims/clm-1");
    // the live region announces progress politely
    expect(
      screen.getAllByRole("status").some((el) => /1 claim ready/.test(el.textContent ?? "")),
    ).toBe(true);
  });

  it("opens the claims by itself after a short countdown, which can be cancelled", async () => {
    const user = userEvent.setup();
    serve({ chunks: [frame(0, started), frame(1, ready("c1", "c2")), frame(2, done(2))] });
    renderApp(<BatchScreen batchId="bat-1" autoOpenSeconds={60} />);
    expect(await screen.findByText(/Opening your claims in \d+s/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Review claims/ })).toHaveAttribute("href", "/claims");
    await user.click(screen.getByRole("button", { name: "Stay here" }));
    expect(screen.queryByText(/Opening your claims/)).not.toBeInTheDocument();
    expect(screen.getByText(/Your claims are ready to review/)).toBeInTheDocument();
    expect(router.push).not.toHaveBeenCalled();
  });

  it("navigates to the claims when the countdown ends", async () => {
    serve({ chunks: [frame(0, started), frame(1, ready("clm-9")), frame(2, done(1))] });
    renderApp(<BatchScreen batchId="bat-1" autoOpenSeconds={1} />);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/claims/clm-9"), {
      timeout: 4000,
    });
  });

  it("is reload-safe: resumes the stream after the last event already seen", async () => {
    let lastEventId: string | null = "unset";
    serve({
      batch: makeBatchView({
        status: "processing",
        documents: [
          { id: "doc-000001", filename: "cab.png", position: 0, status: "queued" },
          { id: "doc-000002", filename: "hotel.png", position: 1, status: "queued" },
        ],
      }),
      history: [started, extracted("doc-000001", 0, "Raahi Cabs"), checked("doc-000001")],
      chunks: [
        frame(3, extracted("doc-000002", 1, "Lotus Bay Suites")),
        frame(4, checked("doc-000002")),
        frame(5, ready("c1")),
        frame(6, done(1)),
      ],
      onEvents: (id) => (lastEventId = id),
    });
    renderApp(<BatchScreen batchId="bat-1" autoOpenSeconds={600} />);
    expect(
      await screen.findByRole("heading", { level: 1, name: /1 claim ready/ }),
    ).toBeInTheDocument();
    expect(lastEventId).toBe("2"); // history held events 0..2
    expect(screen.getByText("Raahi Cabs")).toBeInTheDocument();
    expect(screen.getByText("Lotus Bay Suites")).toBeInTheDocument();
  });

  it("does not stream or redirect for a batch that already finished", async () => {
    let streamed = false;
    serve({
      batch: makeBatchView({
        status: "done",
        finished_at: "2026-10-08T10:00:09Z",
        claims: [makeClaim({ id: "c1" }), makeClaim({ id: "c2" })],
      }),
      history: [started, ready("c1", "c2"), done(2)],
      onEvents: () => (streamed = true),
    });
    renderApp(<BatchScreen batchId="bat-1" autoOpenSeconds={1} />);
    expect(
      await screen.findByRole("heading", { level: 1, name: /2 claims ready/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("0:09")).toBeInTheDocument(); // created 10:00:00 -> finished 10:00:09
    await new Promise((resolve) => setTimeout(resolve, 1500));
    expect(streamed).toBe(false);
    expect(router.push).not.toHaveBeenCalled();
  });

  it("explains a receipt that could not be read and how many made it", async () => {
    serve({
      batch: makeBatchView({
        documents: [
          { id: "doc-000001", filename: "cab.png", position: 0, status: "queued" },
          { id: "doc-000002", filename: "blurry.png", position: 1, status: "queued" },
        ],
      }),
      chunks: [
        frame(0, started),
        frame(1, extracted("doc-000001", 0, "Raahi Cabs")),
        frame(2, checked("doc-000001")),
        frame(3, {
          type: "document_failed",
          batch_id: "bat-1",
          document_id: "doc-000002",
          filename: "blurry.png",
          error: "We could not read this file. It looks blurry or corrupted.",
        }),
        frame(4, ready("c1")),
        frame(5, done(1, 1)),
      ],
    });
    renderApp(<BatchScreen batchId="bat-1" autoOpenSeconds={600} />);
    expect(await screen.findByText(/1 receipt processed, 1 could not be read/)).toBeInTheDocument();
    expect(screen.getByText("blurry.png")).toBeInTheDocument();
    expect(screen.getByText(/It looks blurry or corrupted/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "upload it again" })).toHaveAttribute("href", "/");
  });

  it("reports a batch that failed", async () => {
    const user = userEvent.setup();
    serve({
      chunks: [
        frame(0, started),
        frame(1, { type: "batch_failed", batch_id: "bat-1", error: "the worker stopped" }),
      ],
    });
    renderApp(<BatchScreen batchId="bat-1" autoOpenSeconds={600} />);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Processing stopped" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("the worker stopped").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Back to upload" })).toHaveAttribute("href", "/");
    await user.click(screen.getByRole("button", { name: "Check again" }));
  });

  it("shows a friendly error when the batch is not yours", async () => {
    server.use(
      http.get(url("/v1/batches/bat-1"), () => problem(404, "batch_not_found", "No such batch")),
    );
    renderApp(<BatchScreen batchId="bat-1" />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Upload not found");
    expect(screen.getByRole("link", { name: "Back to upload" })).toBeInTheDocument();
  });
});

describe("BatchScreen: what the cost means", () => {
  const chunks = [
    frame(0, started),
    frame(1, extracted("doc-000001", 0, "Raahi Cabs")),
    frame(2, checked("doc-000001")),
  ];

  it("labels the cost as recorded when the answers are replayed, on the header and the cards", async () => {
    serve({ chunks });
    renderApp(<BatchScreen batchId="bat-1" autoOpenSeconds={600} />);
    expect(await screen.findByText("LLM cost (recorded)")).toBeInTheDocument();
    expect(await screen.findByText("Cab receipt · $0.0005 (recorded)")).toBeInTheDocument();
    expect(screen.queryByText("LLM cost so far")).not.toBeInTheDocument();
  });

  it("keeps the plain wording when the model is really called", async () => {
    serve({ chunks });
    renderApp(<BatchScreen batchId="bat-1" autoOpenSeconds={600} />, {
      handlers: [http.get(url("/v1/meta"), () => HttpResponse.json({ ...META, llm_mode: "live" }))],
    });
    expect(await screen.findByText("Cab receipt · $0.0005")).toBeInTheDocument();
    expect(screen.getByText("LLM cost so far")).toBeInTheDocument();
    expect(screen.queryByText(/recorded/)).not.toBeInTheDocument();
  });
});

describe("elapsedSeconds", () => {
  const base = initialBatchState("b");
  it("is null until the server's creation time is known", () => {
    expect(elapsedSeconds(base, 1_000)).toBeNull();
  });

  it("measures from creation to now while running and to the finish time when done", () => {
    const created = batchReducer(base, {
      type: "hydrate",
      batch: makeBatchView({ created_at: "2026-10-08T10:00:00Z" }),
    });
    const now = Date.parse("2026-10-08T10:00:07Z");
    expect(elapsedSeconds(created, now)).toBe(7);
    expect(elapsedSeconds(created, null)).toBeNull();
    const finished = batchReducer(base, {
      type: "hydrate",
      batch: makeBatchView({
        created_at: "2026-10-08T10:00:00Z",
        finished_at: "2026-10-08T10:00:20Z",
        status: "done",
      }),
    });
    expect(elapsedSeconds(finished, now)).toBe(20);
  });

  it("never goes negative when clocks disagree", () => {
    const created = batchReducer(base, {
      type: "hydrate",
      batch: makeBatchView({ created_at: "2026-10-08T10:00:10Z" }),
    });
    expect(elapsedSeconds(created, Date.parse("2026-10-08T10:00:00Z"))).toBe(0);
  });

  it("reads the API's zone-less timestamps as UTC, not as the viewer's local time", () => {
    // What the real backend sends: naive UTC with microseconds.
    const running = batchReducer(base, {
      type: "hydrate",
      batch: makeBatchView({ created_at: "2026-10-08T12:54:16.303656" }),
    });
    expect(elapsedSeconds(running, Date.parse("2026-10-08T12:54:19.303Z"))).toBe(3);
    const finished = batchReducer(base, {
      type: "hydrate",
      batch: makeBatchView({
        created_at: "2026-10-08T12:54:16.303656",
        finished_at: "2026-10-08T12:54:19.467811",
        status: "done",
      }),
    });
    expect(elapsedSeconds(finished, null)).toBeCloseTo(3.164, 2);
  });

  it("shows nothing rather than a nonsense figure when a running batch looks hours old", () => {
    const created = batchReducer(base, {
      type: "hydrate",
      batch: makeBatchView({ created_at: "2026-10-08T06:00:00Z" }),
    });
    expect(elapsedSeconds(created, Date.parse("2026-10-08T11:30:00Z"))).toBeNull();
    expect(elapsedSeconds(created, Date.parse("2026-10-08T06:59:00Z"))).toBe(3540);
  });
});
