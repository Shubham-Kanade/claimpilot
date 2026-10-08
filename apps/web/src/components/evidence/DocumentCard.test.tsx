import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import {
  HOTEL_CAP_FINDING,
  makeDecisions,
  makeDocument,
  makeDocumentView,
  makeFinding,
  makeReceipt,
} from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { problem, server, url } from "@/test/server";

import { DocumentCard } from "./DocumentCard";

const box = { page: 0, x: 0.36, y: 0.05, w: 0.28, h: 0.05 };

/** The expand/collapse button of the card (field buttons also contain the merchant name). */
async function toggleButton() {
  const buttons = await screen.findAllByRole("button", { name: /Lotus Bay Suites/ });
  return buttons.find((b) => b.hasAttribute("aria-expanded"))!;
}

function serve(view = makeDocumentView()) {
  server.use(
    http.get(url(`/v1/documents/${view.id}`), () => HttpResponse.json(view)),
    http.get(
      url(`/v1/documents/${view.id}/file`),
      () => new HttpResponse(new Uint8Array([1]), { headers: { "Content-Type": "image/png" } }),
    ),
  );
}

describe("DocumentCard", () => {
  it("summarises a clean receipt when collapsed: merchant, amount, category, engine, trust", async () => {
    serve(
      makeDocumentView({
        document: makeDocument({ findings: [] }),
        trust_score: 100,
        verdict: "clean",
      }),
    );
    renderApp(<DocumentCard documentId="doc-000001" />);
    const toggle = await toggleButton();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveTextContent("Hotel folio · 18 Aug 2026");
    expect(toggle).toHaveTextContent("₹22,680.00");
    const card = screen.getByTestId("document-card");
    expect(within(card).getByText("Accommodation")).toBeInTheDocument();
    expect(within(card).getByText(/High 99%/)).toBeInTheDocument();
    expect(within(card).getByText("Jev")).toBeInTheDocument();
    expect(within(card).getByText(/Looks genuine/)).toBeInTheDocument();
    expect(within(card).getByText("No flags")).toBeInTheDocument();
    // collapsed: the original file is not fetched yet
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("expands to the original file, the extracted fields, the decisions and the findings", async () => {
    const user = userEvent.setup();
    serve(makeDocumentView({ document: makeDocument({ findings: [] }) }));
    renderApp(<DocumentCard documentId="doc-000001" />);
    const toggle = await toggleButton();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByRole("img", { name: /Original receipt/ })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Extracted fields" })).toBeInTheDocument();
    expect(screen.getByText("How it was decided")).toBeInTheDocument();
    expect(screen.getByText(/Category decided by/)).toHaveTextContent("Jev");
    expect(screen.getByText("claude-haiku-5-5")).toBeInTheDocument(); // reader model from /v1/meta
    expect(screen.getByText("No flags on this document.")).toBeInTheDocument();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("opens by itself when there is something serious to explain, citing the policy", async () => {
    serve(
      makeDocumentView({
        document: makeDocument({ findings: [HOTEL_CAP_FINDING] }),
        trust_score: 60,
        verdict: "block",
      }),
    );
    renderApp(<DocumentCard documentId="doc-000001" />);
    const toggle = await toggleButton();
    await waitFor(() => expect(toggle).toHaveAttribute("aria-expanded", "true"));
    expect(screen.getByText(/exceeds the ₹7,500.00 cap for grade L3/)).toBeInTheDocument();
    expect(screen.getByText("Policy clause 4.1")).toBeInTheDocument();
    expect(screen.getByText(/Blocked/)).toBeInTheDocument();
  });

  it("selecting a field marks it and highlights its location on the receipt", async () => {
    const user = userEvent.setup();
    serve(makeDocumentView({ document: makeDocument({ boxes: { merchant_name: box } }) }));
    renderApp(<DocumentCard documentId="doc-000001" />);
    await user.click(await toggleButton());
    const field = await screen.findByRole("button", { name: /Merchant/ });
    await user.click(field);
    expect(field).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByTestId("verify-highlight")).toHaveStyle({
      left: "36%",
      width: "28%",
    });
  });

  it("without any locations the values are plain text and nothing offers to point at the receipt", async () => {
    serve(
      makeDocumentView({
        document: makeDocument({
          boxes: {},
          findings: [makeFinding({ fields: ["total"], message: "The total looks off." })],
        }),
      }),
    );
    renderApp(<DocumentCard documentId="doc-000001" />);
    // a high-risk finding opens the card by itself
    expect(await toggleButton()).toHaveAttribute("aria-expanded", "true");
    expect(
      await screen.findByText("Check these values against the original receipt."),
    ).toBeVisible();
    expect(screen.queryByText(/Select a field to see where it is printed/)).not.toBeInTheDocument();
    // no dead buttons: the fields are text, and findings do not offer "Show on the receipt"
    expect(screen.queryByRole("button", { name: /^Total/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show on the receipt" })).not.toBeInTheDocument();
    expect(screen.getByText("The total looks off.")).toBeInTheDocument();
    expect(screen.queryByTestId("verify-highlight")).not.toBeInTheDocument();
    // the viewer says why nothing is highlighted, and the receipt can still be zoomed
    expect(
      await screen.findByText(/Field locations aren't available for this receipt/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zoom in" })).toBeInTheDocument();
  });

  it("'Show on the receipt' jumps from a finding to the field it is about", async () => {
    const user = userEvent.setup();
    serve(
      makeDocumentView({
        document: makeDocument({
          findings: [
            makeFinding({
              fields: ["line_items", "subtotal"],
              message: "Line items do not add up.",
            }),
          ],
          boxes: { subtotal: { ...box, y: 0.62 } },
        }),
      }),
    );
    renderApp(<DocumentCard documentId="doc-000001" />);
    await user.click(await screen.findByRole("button", { name: "Show on the receipt" }));
    expect(await screen.findByTestId("verify-highlight")).toHaveStyle({ top: "62%" });
    expect(screen.getByRole("button", { name: /Subtotal/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("marks low-confidence reads and flags a handwritten, low-confidence category", async () => {
    const user = userEvent.setup();
    serve(
      makeDocumentView({
        document: makeDocument({
          receipt: makeReceipt({ low_confidence_fields: ["date"], handwritten: true }),
          decisions: makeDecisions({
            category: "misc",
            category_confidence: 0.58,
            engine: "llm",
            personal_expense: 0.62,
          }),
        }),
      }),
    );
    renderApp(<DocumentCard documentId="doc-000001" />);
    const card = await screen.findByTestId("document-card");
    expect(within(card).getByText(/Low 58%/)).toBeInTheDocument();
    expect(within(card).getByText("LLM")).toBeInTheDocument();
    await user.click(await toggleButton());
    expect(await screen.findByText("Low confidence")).toBeInTheDocument();
    const personal = screen.getByText("Looks personal").closest("div")!;
    expect(personal).toHaveTextContent("62%");
    expect(
      within(personal.parentElement!).getByLabelText("above the policy threshold"),
    ).toBeInTheDocument();
  });

  it("explains a receipt that could not be read", async () => {
    const user = userEvent.setup();
    serve(
      makeDocumentView({
        id: "doc-000009",
        filename: "blurry.png",
        status: "failed",
        document: null,
        error: "We could not read this file.",
        trust_score: null,
        verdict: null,
      }),
    );
    renderApp(<DocumentCard documentId="doc-000009" />);
    const toggle = await screen.findByRole("button", { name: /blurry.png/ });
    expect(screen.getByText("Could not be read")).toBeInTheDocument();
    await user.click(toggle);
    expect(screen.getByText("We could not read this file.")).toBeInTheDocument();
  });

  it("shows a friendly error (with retry) when the document cannot be loaded", async () => {
    server.use(
      http.get(url("/v1/documents/doc-000001"), () =>
        problem(404, "document_not_found", "No such document"),
      ),
    );
    renderApp(<DocumentCard documentId="doc-000001" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Receipt not found");
  });
});
