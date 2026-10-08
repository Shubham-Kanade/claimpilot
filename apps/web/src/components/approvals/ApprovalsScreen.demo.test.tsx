import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { ADVIKA, META_DEMO, RAVI, makeClaim } from "@/test/fixtures";
import { router } from "@/test/navigation";
import { renderApp } from "@/test/render";
import { url } from "@/test/server";

import { ApprovalsScreen } from "./ApprovalsScreen";

const queue = makeClaim({
  id: "c-1",
  title: "Hyderabad trip 7 Aug 2026",
  employee_id: ADVIKA.id,
  status: "submitted",
  submission_reference: "FIN-2026-000001",
});

const approvals = http.get(url("/v1/approvals"), ({ request }) => {
  const status = new URL(request.url).searchParams.get("status");
  return HttpResponse.json(status === "submitted" ? [queue] : []);
});

describe("ApprovalsScreen: Start over (public demo)", () => {
  it("lets the approver clear everyone's demo data, after a warning, then returns to the upload screen", async () => {
    const user = userEvent.setup();
    let resets = 0;
    renderApp(<ApprovalsScreen />, {
      persona: RAVI.id,
      handlers: [
        approvals,
        http.get(url("/v1/meta"), () => HttpResponse.json(META_DEMO)),
        http.post(url("/v1/demo/reset"), () => {
          resets += 1;
          return HttpResponse.json({ batches: 3, documents: 14, claims: 12 });
        }),
      ],
    });
    await user.click(await screen.findByRole("button", { name: "Start over" }));
    const dialog = await screen.findByRole("dialog", { name: "Start over?" });
    await waitFor(() =>
      expect(dialog).toHaveAccessibleDescription(/clears everyone's uploaded receipts/),
    );
    expect(resets).toBe(0); // asking first
    await user.click(within(dialog).getByRole("button", { name: "Delete and start over" }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/"));
    expect(resets).toBe(1);
  });

  it("is not offered outside the demo", async () => {
    renderApp(<ApprovalsScreen />, { persona: RAVI.id, handlers: [approvals] });
    await screen.findByRole("heading", { level: 1, name: "Approvals" });
    expect(screen.queryByRole("button", { name: "Start over" })).not.toBeInTheDocument();
  });
});
