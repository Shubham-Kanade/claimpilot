import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import {
  ADVIKA,
  HOTEL_CAP_FINDING,
  makeClaim,
  makeDocument,
  makeDocumentView,
  makeFinding,
} from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { problem, server, url } from "@/test/server";

import { ApprovalRow, canReject } from "./ApprovalRow";

const claim = makeClaim({
  id: "clm-9",
  employee_id: "P001",
  title: "Chandigarh trip 15–18 Aug 2026",
  status: "submitted",
  submission_reference: "FIN-2026-000002",
  // The pipeline puts every document's findings on the claim, tagged with the document.
  findings: [
    { ...HOTEL_CAP_FINDING, document_id: "doc-000001" },
    makeFinding({ severity: "warn", code: "w", document_id: "doc-000001" }),
  ],
});

function serveEvidence() {
  server.use(
    http.get(url("/v1/documents/doc-000001"), () =>
      HttpResponse.json(
        makeDocumentView({ document: makeDocument({ findings: [HOTEL_CAP_FINDING] }) }),
      ),
    ),
    http.get(
      url("/v1/documents/doc-000001/file"),
      () => new HttpResponse(new Uint8Array([1]), { headers: { "Content-Type": "image/png" } }),
    ),
  );
}

function renderRow(props: Partial<React.ComponentProps<typeof ApprovalRow>> = {}) {
  serveEvidence();
  return renderApp(<ApprovalRow claim={claim} employee={ADVIKA} {...props} />, {
    persona: "DEMO-RAVI",
  });
}

describe("ApprovalRow", () => {
  it("summarises who, what, how much and how risky", () => {
    renderRow();
    const row = screen.getByTestId("approval-row");
    expect(within(row).getByText("Chandigarh trip 15–18 Aug 2026")).toBeInTheDocument();
    expect(within(row).getByText("₹22,680.00")).toBeInTheDocument();
    expect(row).toHaveTextContent("Advika Hayer · L4");
    expect(row).toHaveTextContent("FIN-2026-000002");
    expect(within(row).getByText("1 high risk")).toBeInTheDocument();
    expect(within(row).getByText("1 warning")).toBeInTheDocument();
    expect(within(row).getByText("Finance review")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Chandigarh trip/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("says why it was flagged even before it is opened (the most serious flag, in words)", () => {
    renderRow();
    const flag = within(screen.getByTestId("approval-row")).getByTestId("top-flag");
    expect(flag).toHaveTextContent("High risk: ");
    expect(flag).toHaveTextContent(/exceeds the ₹7,500.00 cap/);
  });

  it("expands to the same read-only evidence as the employee's review, with the cited clause", async () => {
    const user = userEvent.setup();
    renderRow();
    await user.click(screen.getByRole("button", { name: /Chandigarh trip/ }));
    expect(await screen.findByText(/Evidence · 1 receipt/)).toBeInTheDocument();
    expect(await screen.findByText("Policy clause 4.1")).toBeInTheDocument();
    // once in the row's summary and once in the evidence
    expect(screen.getAllByText(/exceeds the ₹7,500.00 cap/).length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByLabelText("Your reply")).not.toBeInTheDocument(); // no way to edit answers
  });

  it("will not let anyone reject without a reason, and says why", async () => {
    const user = userEvent.setup();
    renderRow({ defaultOpen: true });
    const reject = await screen.findByRole("button", { name: "Reject" });
    const comment = screen.getByLabelText(/Comment/);
    expect(reject).toBeDisabled();
    expect(
      screen.getByText("To reject, say why. Finance keeps the reason on record."),
    ).toBeInTheDocument();
    await user.type(comment, "   ");
    expect(reject).toBeDisabled(); // whitespace is not a reason
    await user.type(comment, "Invoice does not match the folio");
    expect(reject).toBeEnabled();
    expect(screen.getByText("Ready to send with your decision.")).toBeInTheDocument();
    expect(screen.getByText(/\d+\/500/)).toBeInTheDocument();
  });

  it("approves without a comment", async () => {
    const user = userEvent.setup();
    const onDecided = vi.fn();
    let body: unknown;
    renderRow({ defaultOpen: true, onDecided });
    server.use(
      http.post(url("/v1/claims/clm-9/decision"), async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...claim, status: "approved" });
      }),
    );
    const approve = await screen.findByRole("button", { name: "Approve" });
    expect(approve).toBeEnabled();
    await user.click(approve);
    await waitFor(() => expect(onDecided).toHaveBeenCalled());
    expect(body).toEqual({ approved: true, comment: "" });
    expect(onDecided.mock.calls[0][1]).toBe(true);
    expect(onDecided.mock.calls[0][0]).toMatchObject({ status: "approved" });
  });

  it("rejects with the trimmed comment", async () => {
    const user = userEvent.setup();
    const onDecided = vi.fn();
    let body: unknown;
    renderRow({ defaultOpen: true, onDecided });
    server.use(
      http.post(url("/v1/claims/clm-9/decision"), async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...claim, status: "rejected" });
      }),
    );
    await user.type(await screen.findByLabelText(/Comment/), "  Hotel night over the cap  ");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(onDecided).toHaveBeenCalled());
    expect(body).toEqual({ approved: false, comment: "Hotel night over the cap" });
    expect(onDecided.mock.calls[0][1]).toBe(false);
  });

  it("shows the friendly copy if the server still insists on a reason (comment_required)", async () => {
    const user = userEvent.setup();
    renderRow({ defaultOpen: true });
    server.use(
      http.post(url("/v1/claims/clm-9/decision"), () =>
        problem(422, "comment_required", "Say why the claim is rejected"),
      ),
    );
    await user.type(await screen.findByLabelText(/Comment/), "no");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Add a reason");
  });

  it("maps other decision problems, e.g. a claim that was not submitted", async () => {
    const user = userEvent.setup();
    renderRow({ defaultOpen: true });
    server.use(
      http.post(url("/v1/claims/clm-9/decision"), () =>
        problem(409, "claim_not_submitted", "Only a submitted claim can be approved or rejected"),
      ),
    );
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not submitted yet");
  });

  it("offers no decision once the claim has been decided", async () => {
    renderRow({ claim: { ...claim, status: "approved" }, defaultOpen: true });
    expect(await screen.findByText(/Evidence · 1 receipt/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
});

describe("canReject", () => {
  it("requires a non-blank comment", () => {
    expect(canReject("")).toBe(false);
    expect(canReject("   \n ")).toBe(false);
    expect(canReject(" why ")).toBe(true);
  });
});
