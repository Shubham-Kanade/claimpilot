import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { ClaimView } from "@/lib/api/types";
import {
  ADVIKA,
  ASHA,
  HOTEL_CAP_FINDING,
  RAVI,
  makeClaim,
  makeDocument,
  makeDocumentView,
  makeFinding,
} from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { problem, server, url } from "@/test/server";

import { ApprovalsScreen } from "./ApprovalsScreen";

const clean = makeClaim({
  id: "c-clean",
  title: "Hyderabad trip 7 Aug 2026",
  employee_id: ADVIKA.id,
  status: "submitted",
  route: "auto_approve",
  submission_reference: "FIN-2026-000001",
  total: 8875.95,
});
const warned = makeClaim({
  id: "c-warn",
  title: "Learning 18 Jul 2026",
  employee_id: ADVIKA.id,
  status: "submitted",
  findings: [makeFinding({ severity: "warn", code: "preapproval_required" })],
  submission_reference: "FIN-2026-000003",
});
const risky = makeClaim({
  id: "c-risky",
  title: "Chandigarh trip 15–18 Aug 2026",
  employee_id: ASHA.id,
  status: "submitted",
  findings: [HOTEL_CAP_FINDING],
  submission_reference: "FIN-2026-000002",
});

function serve(lists: Partial<Record<"submitted" | "approved" | "rejected", ClaimView[]>> = {}) {
  server.use(
    http.get(url("/v1/approvals"), ({ request }) => {
      const status = new URL(request.url).searchParams.get("status") as "submitted";
      return HttpResponse.json(lists[status] ?? []);
    }),
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

describe("ApprovalsScreen", () => {
  it("tells non-approvers politely, and offers to switch to an approver", async () => {
    const user = userEvent.setup();
    serve();
    renderApp(<ApprovalsScreen />, { persona: ASHA.id });
    expect(await screen.findByRole("heading", { name: "Approvers only" })).toBeInTheDocument();
    expect(screen.getByText(/Asha Menon can't review other people's claims/)).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Switch to Ravi Iyer" }));
    expect(window.localStorage.getItem("claimpilot.persona")).toBe(RAVI.id);
    // now the queue is shown
    expect(await screen.findByRole("heading", { level: 1, name: "Approvals" })).toBeInTheDocument();
  });

  it("lists submitted claims with the riskiest first", async () => {
    serve({ submitted: [clean, warned, risky] });
    renderApp(<ApprovalsScreen />, { persona: RAVI.id });
    const rows = await screen.findAllByTestId("approval-row");
    expect(
      rows.map((r) => within(r).getAllByRole("heading", { level: 2 })[0].textContent?.trim()),
    ).toEqual([
      expect.stringContaining("Chandigarh trip"),
      expect.stringContaining("Learning 18 Jul 2026"),
      expect.stringContaining("Hyderabad trip"),
    ]);
    expect(rows[0]).toHaveTextContent("Asha Menon");
    expect(rows[0]).toHaveTextContent("1 high risk");
  });

  it("has Submitted, Approved and Rejected tabs with counts", async () => {
    const user = userEvent.setup();
    serve({
      submitted: [clean, risky],
      approved: [{ ...clean, id: "c-ok", title: "Fuel Jul 2026", status: "approved" }],
      rejected: [],
    });
    renderApp(<ApprovalsScreen />, { persona: RAVI.id });
    const tabs = await screen.findAllByRole("tab");
    await waitFor(() =>
      expect(tabs.map((t) => t.textContent)).toEqual(["Submitted 2", "Approved 1", "Rejected 0"]),
    );
    await user.click(screen.getByRole("tab", { name: /Approved/ }));
    expect(await screen.findByText("Fuel Jul 2026")).toBeInTheDocument();
    // decided claims have no approve/reject buttons
    await user.click(screen.getByRole("button", { name: /Fuel Jul 2026/ }));
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /Rejected/ }));
    expect(await screen.findByText("Nothing rejected")).toBeInTheDocument();
  });

  it("shows an 'all caught up' state when nothing is waiting", async () => {
    serve({ submitted: [] });
    renderApp(<ApprovalsScreen />, { persona: RAVI.id });
    expect(await screen.findByText("All caught up")).toBeInTheDocument();
  });

  it("approves a claim, tells the approver, and refreshes the queue", async () => {
    const user = userEvent.setup();
    let remaining: ClaimView[] = [risky, clean];
    serve({ submitted: remaining });
    server.use(
      http.get(url("/v1/approvals"), ({ request }) => {
        const status = new URL(request.url).searchParams.get("status");
        return HttpResponse.json(status === "submitted" ? remaining : []);
      }),
      http.post(url("/v1/claims/c-risky/decision"), () => {
        remaining = remaining.filter((c) => c.id !== "c-risky");
        return HttpResponse.json({ ...risky, status: "approved" });
      }),
    );
    renderApp(<ApprovalsScreen />, { persona: RAVI.id });
    await user.click(await screen.findByRole("button", { name: /Chandigarh trip/ }));
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    const banner = await screen.findByText(/Chandigarh trip 15–18 Aug 2026/, {
      selector: "span span",
    });
    expect(banner.closest("[role=status]")).toHaveTextContent("Approved");
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Chandigarh trip/ })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /Hyderabad trip/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument();
  });

  it("shows a friendly error with retry when the queue cannot be loaded", async () => {
    server.use(http.get(url("/v1/approvals"), () => problem(503, "http_503", "down")));
    renderApp(<ApprovalsScreen />, { persona: RAVI.id });
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on our side");
  });

  it("reports an unreachable API", async () => {
    renderApp(<ApprovalsScreen />, {
      persona: RAVI.id,
      handlers: [http.get(url("/v1/employees"), () => problem(503, "http_503", "down"))],
    });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
