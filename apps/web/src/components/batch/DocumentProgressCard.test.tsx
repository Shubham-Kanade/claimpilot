import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  batchReducer,
  initialBatchState,
  type BatchState,
  type DocProgress,
} from "@/lib/batch/batchReducer";
import { makeBatchView } from "@/test/fixtures";

import { DocumentProgressCard } from "./DocumentProgressCard";

function stateWith(
  doc: Partial<DocProgress>,
  started = true,
): { state: BatchState; doc: DocProgress } {
  // A queued batch has not started: its documents are waiting, not reading.
  let state = batchReducer(initialBatchState("bat-1"), {
    type: "hydrate",
    batch: makeBatchView({ status: started ? "processing" : "queued" }),
  });
  if (started) {
    state = batchReducer(state, {
      type: "event",
      event: { type: "batch_started", batch_id: "bat-1", total: 2 },
    });
  }
  const merged: DocProgress = { ...state.docs["doc-000001"], ...doc };
  return { state: { ...state, docs: { ...state.docs, "doc-000001": merged } }, doc: merged };
}

/** The card itself (its progress steps are list items too, so address it by data-phase). */
function card(): HTMLElement {
  return document.querySelector<HTMLElement>("li[data-phase]")!;
}

function renderCard(
  doc: Partial<DocProgress>,
  started = true,
  profile: "recorded" | "recorded-and-live" | "live" = "live",
) {
  const { state, doc: full } = stateWith(doc, started);
  return render(
    <ul>
      <DocumentProgressCard state={state} doc={full} profile={profile} />
    </ul>,
  );
}

const READ: Partial<DocProgress> = {
  phase: "read",
  merchant: "Raahi Cabs",
  total: 672.74,
  docType: "cab_receipt",
  category: "local_conveyance",
  categoryConfidence: 0.95,
  engine: "jev",
  costUsd: 0.0005,
};

describe("DocumentProgressCard", () => {
  it("waits in the queue before the batch starts", () => {
    renderCard({ phase: "waiting" }, false);
    expect(card()).toHaveAttribute("data-phase", "waiting");
    expect(screen.getByText("Waiting in the queue")).toBeInTheDocument();
    expect(screen.getAllByText("Waiting").length).toBeGreaterThanOrEqual(1); // badge + first step
  });

  it("shows 'reading' as soon as the batch has started", () => {
    renderCard({ phase: "waiting" });
    expect(card()).toHaveAttribute("data-phase", "reading");
    expect(screen.getByText("Reading the receipt…")).toBeInTheDocument();
  });

  it("shows what was read: merchant, amount, category with confidence and the engine", () => {
    renderCard(READ);
    const c = within(card());
    expect(card()).toHaveAttribute("data-phase", "read");
    expect(c.getByText("Raahi Cabs")).toBeInTheDocument();
    expect(c.getByText("₹672.74")).toBeInTheDocument();
    expect(c.getByText("Local conveyance")).toBeInTheDocument();
    expect(c.getByText(/High 95%/)).toBeInTheDocument();
    expect(c.getByText("Jev")).toBeInTheDocument();
    expect(c.getByText(/Cab receipt · \$0\.0005/)).toBeInTheDocument();
    expect(c.getByText("Read · checking policy and trust…")).toBeInTheDocument();
    expect(c.queryByText(/Looks genuine/)).not.toBeInTheDocument();
  });

  it("calls the cost recorded when the answers are replayed (it is not spent now)", () => {
    const { unmount } = renderCard(READ, true, "recorded");
    expect(within(card()).getByText("Cab receipt · $0.0005 (recorded)")).toBeInTheDocument();
    unmount();
    const hybrid = renderCard(READ, true, "recorded-and-live");
    expect(
      within(card()).getByText("Cab receipt · $0.0005 (recorded or live)"),
    ).toBeInTheDocument();
    hybrid.unmount();
    renderCard(READ, true, "live");
    expect(within(card()).getByText("Cab receipt · $0.0005")).toBeInTheDocument();
  });

  it("shows the trust verdict, score and number of flags once checked", () => {
    renderCard({ ...READ, phase: "checked", trustScore: 60, verdict: "block", findings: 2 });
    const c = within(card());
    expect(c.getByText(/Blocked/)).toBeInTheDocument();
    expect(c.getByText(/out of 100 trust score/)).toBeInTheDocument();
    expect(c.getByText("2 flags")).toBeInTheDocument();
  });

  it("says 'No flags' / '1 flag' and notes cached results as free", () => {
    const { unmount } = renderCard({
      ...READ,
      phase: "checked",
      trustScore: 100,
      verdict: "clean",
      findings: 0,
      cached: true,
    });
    expect(screen.getByText("No flags")).toBeInTheDocument();
    expect(screen.getByText(/cached \(no LLM cost\)/)).toBeInTheDocument();
    unmount();
    renderCard({ ...READ, phase: "checked", trustScore: 97, verdict: "clean", findings: 1 });
    expect(screen.getByText("1 flag")).toBeInTheDocument();
  });

  it("copes with a receipt where nothing could be read", () => {
    renderCard({
      phase: "checked",
      merchant: null,
      total: null,
      trustScore: 50,
      verdict: "review",
      findings: null,
    });
    expect(screen.getByText("Merchant not found")).toBeInTheDocument();
    expect(screen.getByText("No total")).toBeInTheDocument();
  });

  it("explains a failure and what to do next, without blaming the other receipts", () => {
    renderCard({ phase: "failed", error: "We could not read this file. It looks blurry." });
    const c = within(card());
    expect(c.getByText("We could not read this file. It looks blurry.")).toBeInTheDocument();
    expect(c.getByRole("link", { name: "upload it again" })).toHaveAttribute("href", "/");
    expect(c.getByText(/The other receipts are not affected/)).toBeInTheDocument();
    expect(c.getByText("Failed")).toBeInTheDocument();
  });

  it("in the public demo the advice is to use the samples, not to retake the photo", () => {
    const { state, doc } = stateWith({
      phase: "failed",
      error:
        "This demo reads only its recorded sample receipts. To read your own, run ClaimPilot with your own API key (see the README).",
    });
    render(
      <ul>
        <DocumentProgressCard state={state} doc={doc} demo />
      </ul>,
    );
    const c = within(card());
    // the server's own sentence, shown as is
    expect(c.getByText(/This demo reads only its recorded sample receipts/)).toBeInTheDocument();
    expect(c.getByRole("link", { name: "back to upload" })).toHaveAttribute("href", "/");
    expect(c.getByText(/Try with sample receipts/)).toBeInTheDocument();
    expect(c.queryByText(/Retake the photo/)).not.toBeInTheDocument();
  });

  it("marks the current step for assistive technology", () => {
    renderCard(READ);
    const steps = within(screen.getByRole("list", { name: "Progress" })).getAllByRole("listitem");
    expect(steps).toHaveLength(4);
    expect(steps[2]).toHaveAttribute("aria-current", "step");
    expect(steps[0]).toHaveTextContent("Waiting (done)");
  });

  it("uses the PDF icon for PDFs", () => {
    renderCard({ filename: "invoice.pdf", phase: "waiting" });
    expect(screen.getByText("invoice.pdf")).toBeInTheDocument();
  });
});
