import { describe, expect, it } from "vitest";

import type { PipelineEvent } from "@/lib/api/types";
import {
  makeBatchView,
  makeDecisions,
  makeDocument,
  makeDocumentView,
  makeReceipt,
} from "@/test/fixtures";

import {
  batchProgress,
  batchReducer,
  initialBatchState,
  isFinished,
  phaseOf,
  type BatchAction,
  type BatchState,
} from "./batchReducer";

const extracted = (
  id: string,
  position: number,
  extra: Partial<PipelineEvent> = {},
): PipelineEvent =>
  ({
    type: "document_extracted",
    batch_id: "bat-1",
    document_id: id,
    filename: `${id}.png`,
    position,
    doc_type: "cab_receipt",
    merchant: "Raahi Cabs",
    total: 672.74,
    category: "local_conveyance",
    category_confidence: 0.95,
    engine: "jev",
    cached: false,
    cost_usd: 0.0005,
    ...extra,
  }) as PipelineEvent;

const checked = (id: string): PipelineEvent => ({
  type: "document_checked",
  batch_id: "bat-1",
  document_id: id,
  trust_score: 97,
  verdict: "clean",
  findings: 1,
});

function run(actions: BatchAction[], from: BatchState = initialBatchState("bat-1")): BatchState {
  return actions.reduce(batchReducer, from);
}

const event = (e: PipelineEvent, index?: number): BatchAction => ({
  type: "event",
  event: e,
  index,
});

describe("batchReducer", () => {
  it("starts empty and idle", () => {
    const state = initialBatchState("bat-1");
    expect(state).toMatchObject({
      phase: "idle",
      started: false,
      order: [],
      costUsd: 0,
      lastIndex: -1,
    });
    expect(isFinished(state)).toBe(false);
  });

  it("hydrates the documents from the batch snapshot as waiting cards, in upload order", () => {
    const state = run([{ type: "hydrate", batch: makeBatchView({ status: "queued" }) }]);
    expect(state.order).toEqual(["doc-000001", "doc-000002"]);
    expect(state.docs["doc-000001"]).toMatchObject({ filename: "cab.png", phase: "waiting" });
    expect(state.total).toBe(2);
    expect(state.phase).toBe("idle");
    expect(state.createdAt).toBe("2026-10-08T10:00:00Z");
  });

  it("moves waiting cards to reading once the batch has started", () => {
    const state = run([
      { type: "hydrate", batch: makeBatchView() },
      event({ type: "batch_started", batch_id: "bat-1", total: 2 }, 0),
    ]);
    expect(state.started).toBe(true);
    expect(state.phase).toBe("running");
    expect(phaseOf(state, state.docs["doc-000001"])).toBe("reading");
  });

  it("reads, then checks a document and sums the LLM cost", () => {
    const state = run([
      event({ type: "batch_started", batch_id: "bat-1", total: 2 }, 0),
      event(extracted("doc-000001", 0), 1),
      event(extracted("doc-000002", 1, { cost_usd: 0.0007 }), 2),
      event(checked("doc-000001"), 3),
    ]);
    expect(state.docs["doc-000001"]).toMatchObject({
      phase: "checked",
      merchant: "Raahi Cabs",
      total: 672.74,
      category: "local_conveyance",
      categoryConfidence: 0.95,
      engine: "jev",
      trustScore: 97,
      verdict: "clean",
      findings: 1,
    });
    expect(state.docs["doc-000002"].phase).toBe("read");
    expect(state.costUsd).toBeCloseTo(0.0012, 6);
    expect(state.lastIndex).toBe(3);
  });

  it("ignores events whose index was already applied (reconnects never double count)", () => {
    const first = run([event(extracted("doc-000001", 0), 5)]);
    const again = batchReducer(first, event(extracted("doc-000001", 0), 5));
    const older = batchReducer(first, event(extracted("doc-000001", 0), 2));
    expect(again).toBe(first);
    expect(older).toBe(first);
    expect(again.costUsd).toBeCloseTo(0.0005, 6);
  });

  it("never moves a checked document back to read", () => {
    const state = run([
      event(extracted("doc-000001", 0), 0),
      event(checked("doc-000001"), 1),
      event(extracted("doc-000001", 0)),
    ]);
    expect(state.docs["doc-000001"].phase).toBe("checked");
  });

  it("records a failed document with its error", () => {
    const state = run([
      { type: "hydrate", batch: makeBatchView() },
      event({
        type: "document_failed",
        batch_id: "bat-1",
        document_id: "doc-000002",
        filename: "blurry.png",
        error: "We could not read this file.",
      }),
    ]);
    expect(state.docs["doc-000002"]).toMatchObject({
      phase: "failed",
      filename: "blurry.png",
      error: "We could not read this file.",
    });
  });

  it("creates cards for documents it has not heard of (events before the snapshot)", () => {
    const state = run([event(checked("doc-xyz")), event(extracted("doc-abc", 4))]);
    expect(state.order).toEqual(
      ["doc-xyz", "doc-abc"].sort((a, b) => state.docs[a].position - state.docs[b].position),
    );
    expect(state.docs["doc-xyz"].filename).toBe("Receipt");
  });

  it("finishes with claims_ready then batch_done", () => {
    const state = run([
      event({ type: "claims_ready", batch_id: "bat-1", claim_ids: ["c1", "c2"] }, 7),
      event(
        {
          type: "batch_done",
          batch_id: "bat-1",
          processed: 2,
          failed: 0,
          claims: 2,
          cost_usd: 0.0012,
        },
        8,
      ),
    ]);
    expect(state.phase).toBe("done");
    expect(state.claimIds).toEqual(["c1", "c2"]);
    expect(state.summary).toEqual({ processed: 2, failed: 0, claims: 2, costUsd: 0.0012 });
    expect(state.costUsd).toBe(0.0012);
    expect(isFinished(state)).toBe(true);
  });

  it("fails with the server's message on batch_failed", () => {
    const state = run([
      event({ type: "batch_failed", batch_id: "bat-1", error: "worker crashed" }),
    ]);
    expect(state).toMatchObject({ phase: "failed", error: "worker crashed" });
    expect(isFinished(state)).toBe(true);
  });

  it("restores a finished batch from the snapshot alone (reload after it completed)", () => {
    const processed = makeDocumentView({
      id: "doc-000001",
      filename: "cab.png",
      position: 0,
      document: makeDocument({
        id: "doc-000001",
        receipt: makeReceipt({
          merchant_name: "Raahi Cabs",
          total: 672.74,
          doc_type: "cab_receipt",
        }),
        decisions: makeDecisions({ category: "local_conveyance", category_confidence: 0.95 }),
        findings: [],
      }),
      trust_score: 100,
      verdict: "clean",
    });
    const failed = makeDocumentView({
      id: "doc-000002",
      filename: "blurry.png",
      position: 1,
      status: "failed",
      document: null,
      error: "unreadable",
      trust_score: null,
      verdict: null,
    });
    const state = run([
      {
        type: "hydrate",
        batch: makeBatchView({
          status: "done",
          documents: [processed, failed],
          finished_at: "2026-10-08T10:00:20Z",
          claims: [],
        }),
      },
    ]);
    expect(state.phase).toBe("done");
    expect(state.docs["doc-000001"]).toMatchObject({
      phase: "checked",
      merchant: "Raahi Cabs",
      category: "local_conveyance",
      trustScore: 100,
      verdict: "clean",
      findings: 0,
    });
    expect(state.docs["doc-000002"]).toMatchObject({ phase: "failed", error: "unreadable" });
    expect(state.finishedAt).toBe("2026-10-08T10:00:20Z");
  });

  it("never goes backwards: a stale snapshot cannot undo batch_done", () => {
    const state = run([
      event({ type: "batch_started", batch_id: "bat-1", total: 2 }, 0),
      event(
        {
          type: "batch_done",
          batch_id: "bat-1",
          processed: 2,
          failed: 0,
          claims: 1,
          cost_usd: 0.001,
        },
        1,
      ),
      { type: "hydrate", batch: makeBatchView({ status: "processing" }) },
    ]);
    expect(state.phase).toBe("done");
    const failed = run([
      event({ type: "batch_failed", batch_id: "bat-1", error: "boom" }, 0),
      { type: "hydrate", batch: makeBatchView({ status: "queued" }) },
    ]);
    expect(failed.phase).toBe("failed");
  });

  it("takes a terminal status from the snapshot when no event arrived", () => {
    expect(
      run([{ type: "hydrate", batch: makeBatchView({ status: "failed", error: "worker died" }) }]),
    ).toMatchObject({
      phase: "failed",
      error: "worker died",
    });
  });

  it("keeps event progress when a later snapshot still says queued", () => {
    const state = run([
      event(extracted("doc-000001", 0), 0),
      { type: "hydrate", batch: makeBatchView() },
    ]);
    expect(state.docs["doc-000001"].phase).toBe("read");
    expect(state.docs["doc-000001"].merchant).toBe("Raahi Cabs");
  });
});

describe("batchProgress", () => {
  it("counts read, checked and failed documents against the total", () => {
    const state = run([
      {
        type: "hydrate",
        batch: makeBatchView({
          total: 4,
          documents: [
            { id: "a", filename: "a.png", position: 0, status: "queued" },
            { id: "b", filename: "b.png", position: 1, status: "queued" },
            { id: "c", filename: "c.png", position: 2, status: "queued" },
            { id: "d", filename: "d.png", position: 3, status: "queued" },
          ],
        }),
      },
      event({ type: "batch_started", batch_id: "bat-1", total: 4 }),
      event(extracted("a", 0)),
      event(checked("a")),
      event(extracted("b", 1)),
      event({
        type: "document_failed",
        batch_id: "bat-1",
        document_id: "c",
        filename: "c.png",
        error: "x",
      }),
    ]);
    expect(batchProgress(state)).toEqual({
      total: 4,
      finished: 2,
      read: 2,
      failed: 1,
      percent: 50,
    });
  });

  it("is 100% once the batch is done and 0% with nothing known", () => {
    const done = run([
      event({
        type: "batch_done",
        batch_id: "bat-1",
        processed: 3,
        failed: 0,
        claims: 1,
        cost_usd: 0,
      }),
    ]);
    expect(batchProgress(done).percent).toBe(0); // total unknown
    const doneWithTotal = run([
      event({ type: "batch_started", batch_id: "bat-1", total: 3 }),
      event({
        type: "batch_done",
        batch_id: "bat-1",
        processed: 3,
        failed: 0,
        claims: 1,
        cost_usd: 0,
      }),
    ]);
    expect(batchProgress(doneWithTotal)).toMatchObject({ total: 3, finished: 3, percent: 100 });
    expect(batchProgress(initialBatchState("x")).percent).toBe(0);
  });
});
