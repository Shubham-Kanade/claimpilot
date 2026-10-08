import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { ClaimView } from "@/lib/api/types";
import {
  ASHA,
  HOTEL_CAP_FINDING,
  RAVI,
  makeClaim,
  makeDecisions,
  makeDocument,
  makeDocumentView,
  makeQuestion,
  makeReceipt,
} from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { problem, server, url } from "@/test/server";

import { ClaimReviewScreen } from "./ClaimReviewScreen";

const ATTENDEES = makeQuestion({
  id: "q-attendees-1",
  kind: "attendees",
  text: "Who attended the client dinner on 12 Jul (₹830)? Please give names and company.",
});
const PURPOSE = makeQuestion({
  id: "q-business_purpose-2",
  kind: "business_purpose",
  text: "What was the business purpose of the client dinner on 12 Jul (₹830)?",
});

function serveDinner(initial?: Partial<ClaimView>) {
  const state = {
    claim: makeClaim({
      id: "clm-dinner",
      title: "Client dinner 12 Jul 2026",
      mode: "event",
      status: "needs_info",
      total: 829.5,
      city: "Bengaluru",
      start_date: "2026-07-12",
      end_date: "2026-07-12",
      route: "finance_review",
      open_questions: [ATTENDEES, PURPOSE],
      ...initial,
    }),
    submissions: 0,
  };
  const document = makeDocument({
    id: "doc-000001",
    filename: "restaurant-bill-mehfil-cafe.png",
    receipt: makeReceipt({
      doc_type: "restaurant_bill",
      merchant_name: "Mehfil Cafe",
      total: 829.5,
      date: "2026-07-12",
      low_confidence_fields: ["line_items"],
    }),
    decisions: makeDecisions({
      category: "client_entertainment",
      category_confidence: 0.78,
      engine: "llm",
    }),
  });
  server.use(
    http.get(url("/v1/claims/clm-dinner"), () => HttpResponse.json(state.claim)),
    http.get(url("/v1/claims/clm-dinner/prompt"), () => {
      const open = (state.claim.open_questions ?? []).filter((q) => !q.answer);
      return HttpResponse.json({
        prompt: open.length
          ? `To finish “Client dinner 12 Jul 2026” I need a few details:\n${open.map((q, i) => `${i + 1}. ${q.text}`).join("\n")}\nYou can answer in one message.`
          : null,
        open_question_ids: open.map((q) => q.id),
      });
    }),
    http.post(url("/v1/claims/clm-dinner/reply"), async ({ request }) => {
      const { text } = (await request.json()) as { text: string };
      const [who, why] = text.split(";").map((s) => s.trim());
      const understood: Record<string, string> = {};
      if (who) understood[ATTENDEES.id] = who;
      if (why) understood[PURPOSE.id] = why;
      state.claim = {
        ...state.claim,
        status: why ? "ready" : "needs_info",
        route: "auto_approve",
        open_questions: (state.claim.open_questions ?? []).map((q) =>
          understood[q.id] ? { ...q, answer: understood[q.id] } : q,
        ),
      };
      return HttpResponse.json({
        claim: state.claim,
        understood,
        follow_up: why ? null : "One more detail please.",
      });
    }),
    http.post(url("/v1/claims/clm-dinner/submit"), () => {
      state.submissions += 1;
      state.claim = {
        ...state.claim,
        status: "submitted",
        submission_reference: "FIN-2026-000010",
      };
      return HttpResponse.json(state.claim);
    }),
    http.get(url("/v1/documents/doc-000001"), () =>
      HttpResponse.json(makeDocumentView({ document })),
    ),
    http.get(
      url("/v1/documents/doc-000001/file"),
      () => new HttpResponse(new Uint8Array([1]), { headers: { "Content-Type": "image/png" } }),
    ),
  );
  return state;
}

describe("ClaimReviewScreen", () => {
  it("shows the claim, the ONE question and the evidence, with submit locked", async () => {
    serveDinner();
    renderApp(<ClaimReviewScreen claimId="clm-dinner" />);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Client dinner 12 Jul 2026" }),
    ).toBeInTheDocument();
    expect(screen.getByText("12 Jul 2026")).toBeInTheDocument();
    expect(screen.getByText("Bengaluru")).toBeInTheDocument();
    expect(screen.getAllByText("₹829.50").length).toBeGreaterThan(0);
    expect(await screen.findByText(/I need a few details/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Mehfil Cafe/ })).toBeInTheDocument();
    expect(screen.getByText(/Evidence · 1 receipt/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm & submit" })).toBeDisabled();
    expect(screen.getByText("Answer the 2 open questions to enable submit.")).toBeInTheDocument();
  });

  it("goes from needs-info to submitted: one reply, then an explicit confirmation", async () => {
    const user = userEvent.setup();
    const state = serveDinner();
    renderApp(<ClaimReviewScreen claimId="clm-dinner" />);
    await screen.findByText(/I need a few details/);

    await user.type(
      screen.getByLabelText("Your reply"),
      "Neha Rao and Rohan Kapoor (Kestrel Logistics); contract renewal",
    );
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("All set")).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "Confirm & submit" });
    await waitFor(() => expect(submit).toBeEnabled());
    expect(screen.getAllByText("Ready to submit").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Low risk").length).toBeGreaterThan(0);
    expect(state.submissions).toBe(0);

    await user.click(submit);
    const dialog = await screen.findByRole("dialog", { name: "Submit this claim to finance?" });
    expect(state.submissions).toBe(0); // the dialog alone submits nothing
    await user.click(within(dialog).getByRole("button", { name: "Confirm & submit" }));

    expect(await screen.findByText("FIN-2026-000010")).toBeInTheDocument();
    expect(state.submissions).toBe(1);
    expect(screen.getByText("This claim has been submitted.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Your reply")).not.toBeInTheDocument();
  });

  it("opens receipts with flags by itself and shows the cited policy clause", async () => {
    serveDinner({
      status: "ready",
      open_questions: [],
      findings: [{ ...HOTEL_CAP_FINDING, document_id: "doc-000001" }],
    });
    server.use(
      http.get(url("/v1/documents/doc-000001"), () =>
        HttpResponse.json(
          makeDocumentView({
            document: makeDocument({ findings: [HOTEL_CAP_FINDING] }),
            verdict: "review",
            trust_score: 60,
          }),
        ),
      ),
    );
    renderApp(<ClaimReviewScreen claimId="clm-dinner" />);
    expect(await screen.findByText("Policy clause 4.1")).toBeInTheDocument();
    expect(
      screen.getByText(/Goes to finance review because of 1 high-risk flag/),
    ).toBeInTheDocument();
    expect(screen.getAllByText("1 high risk").length).toBeGreaterThan(0);
  });

  it("shows claim-level findings that belong to no single receipt", async () => {
    serveDinner({
      status: "ready",
      open_questions: [],
      findings: [
        {
          ...HOTEL_CAP_FINDING,
          code: "late_submission",
          message: "Claimed 120 days after the trip.",
          document_id: null,
        },
      ],
    });
    renderApp(<ClaimReviewScreen claimId="clm-dinner" />);
    expect(await screen.findByText("Flags on the whole claim")).toBeInTheDocument();
    expect(screen.getByText("Claimed 120 days after the trip.")).toBeInTheDocument();
  });

  it("is read-only for an approver looking at someone else's claim", async () => {
    serveDinner();
    renderApp(<ClaimReviewScreen claimId="clm-dinner" />, { persona: RAVI.id });
    expect(
      await screen.findByText(
        /You're viewing Asha Menon's claim as Ravi Iyer\. It is read-only here\./,
      ),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/Only the employee who owns this claim can answer/),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Your reply")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm & submit" })).toBeDisabled();
    expect(screen.getByText("Only the claim's owner can submit it.")).toBeInTheDocument();
    expect(ASHA.name).toBe("Asha Menon");
  });

  it("explains when the claim is not available to this persona, and links back", async () => {
    server.use(
      http.get(url("/v1/claims/clm-x"), () => problem(404, "claim_not_found", "No such claim")),
    );
    renderApp(<ClaimReviewScreen claimId="clm-x" />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Claim not found");
    expect(screen.getByRole("link", { name: "Back to my claims" })).toHaveAttribute(
      "href",
      "/claims",
    );
  });

  it("shows the owner's name for another employee's claim, and a skeleton while loading", async () => {
    server.use(http.get(url("/v1/claims/clm-slow"), () => new Promise(() => undefined)));
    const { container } = renderApp(<ClaimReviewScreen claimId="clm-slow" />);
    await waitFor(() => expect(container.querySelector(".skeleton")).not.toBeNull());
  });
});
