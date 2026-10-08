import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { ClaimView } from "@/lib/api/types";
import { useClaim } from "@/lib/hooks/queries";
import { HOTEL_CAP_FINDING, makeClaim, makeFinding, makeQuestion } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { problem, server, url } from "@/test/server";

import { describeFlags, routeReasons, SubmitBar } from "./SubmitBar";

/** The bar reads the claim from the cache, exactly like the real screen. */
function setup(initial: ClaimView, options: { owner?: boolean; persona?: string } = {}) {
  const state = { claim: initial };
  server.use(http.get(url(`/v1/claims/${initial.id}`), () => HttpResponse.json(state.claim)));
  function Harness() {
    const query = useClaim(initial.id);
    return query.data ? <SubmitBar claim={query.data} isOwner={options.owner ?? true} /> : null;
  }
  renderApp(<Harness />, { persona: options.persona });
  return state;
}

const ready = (overrides: Partial<ClaimView> = {}) =>
  makeClaim({ id: "clm-1", status: "ready", route: "finance_review", ...overrides });

describe("SubmitBar", () => {
  it("shows status, total and route, and keeps the button disabled until the claim is ready", async () => {
    setup(
      makeClaim({
        id: "clm-1",
        status: "needs_info",
        open_questions: [makeQuestion({ id: "q1" }), makeQuestion({ id: "q2" })],
      }),
    );
    const button = await screen.findByRole("button", { name: "Confirm & submit" });
    expect(button).toBeDisabled();
    expect(screen.getByText("₹22,680.00")).toBeInTheDocument();
    expect(screen.getByText("Needs your input")).toBeInTheDocument();
    expect(screen.getByText("Answer the 2 open questions to enable submit.")).toBeInTheDocument();
  });

  it("uses singular wording for one open question", async () => {
    setup(makeClaim({ id: "clm-1", status: "needs_info", open_questions: [makeQuestion()] }));
    expect(
      await screen.findByText("Answer the open question to enable submit."),
    ).toBeInTheDocument();
  });

  it("enables submit for a ready claim, but never submits without the confirmation dialog", async () => {
    const user = userEvent.setup();
    let submitted = false;
    server.use(
      http.post(url("/v1/claims/clm-1/submit"), () => ((submitted = true), HttpResponse.json({}))),
    );
    setup(ready());
    const button = await screen.findByRole("button", { name: "Confirm & submit" });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    const dialog = await screen.findByRole("dialog", { name: "Submit this claim to finance?" });
    expect(submitted).toBe(false);
    expect(within(dialog).getByText("Chandigarh trip 15–18 Aug 2026")).toBeInTheDocument();
    expect(within(dialog).getByText("₹22,680.00")).toBeInTheDocument();
    expect(within(dialog).getByText("1")).toBeInTheDocument(); // receipts
    expect(within(dialog).getByText("None")).toBeInTheDocument(); // flags
    expect(within(dialog).getByText("Finance review")).toBeInTheDocument();
    // safest control first: Cancel has focus, so Enter cannot submit by accident
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("closes with Escape or Cancel and nothing is sent", async () => {
    const user = userEvent.setup();
    let submitted = false;
    server.use(
      http.post(url("/v1/claims/clm-1/submit"), () => ((submitted = true), HttpResponse.json({}))),
    );
    setup(ready());
    const open = await screen.findByRole("button", { name: "Confirm & submit" });
    await waitFor(() => expect(open).toBeEnabled());
    await user.click(open);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(open);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(submitted).toBe(false);
  });

  it("summarises the flags and why the claim goes to finance", async () => {
    const user = userEvent.setup();
    setup(
      ready({
        findings: [
          HOTEL_CAP_FINDING,
          makeFinding({ severity: "warn", code: "w1" }),
          makeFinding({ severity: "warn", code: "w2" }),
        ],
      }),
    );
    const open = await screen.findByRole("button", { name: "Confirm & submit" });
    await waitFor(() => expect(open).toBeEnabled());
    await user.click(open);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("1 high risk, 2 warnings")).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        /goes to finance for review because of 1 high-risk flag and 2 warnings/,
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(/Once submitted it can't be changed/)).toBeInTheDocument();
  });

  it("explains an auto-approvable claim", async () => {
    const user = userEvent.setup();
    setup(ready({ route: "auto_approve", total: 120 }));
    const open = await screen.findByRole("button", { name: "Confirm & submit" });
    await waitFor(() => expect(open).toBeEnabled());
    await user.click(open);
    expect(await screen.findByText(/small, clean and complete/)).toBeInTheDocument();
  });

  it("submits with {confirmed: true} and an Idempotency-Key, then shows the reference", async () => {
    const user = userEvent.setup();
    let headers: Headers | null = null;
    let body: unknown = null;
    const state = setup(ready());
    server.use(
      http.post(url("/v1/claims/clm-1/submit"), async ({ request }) => {
        headers = request.headers;
        body = await request.json();
        state.claim = {
          ...state.claim,
          status: "submitted",
          submission_reference: "FIN-2026-000123",
        };
        return HttpResponse.json(state.claim);
      }),
    );
    const open = await screen.findByRole("button", { name: "Confirm & submit" });
    await waitFor(() => expect(open).toBeEnabled());
    await user.click(open);
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm & submit" }),
    );

    expect(await screen.findByText("FIN-2026-000123")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Submitted · FIN-2026-000123");
    expect(body).toEqual({ confirmed: true });
    expect(headers!.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(headers!.get("X-Persona")).toBe("DEMO-ASHA");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm & submit" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All claims" })).toHaveAttribute("href", "/claims");
  });

  it("reuses the SAME Idempotency-Key when the user retries after a failure", async () => {
    const user = userEvent.setup();
    const keys: Array<string | null> = [];
    const state = setup(ready());
    server.use(
      http.post(url("/v1/claims/clm-1/submit"), ({ request }) => {
        keys.push(request.headers.get("Idempotency-Key"));
        if (keys.length === 1) return problem(500, "http_500", "boom");
        state.claim = {
          ...state.claim,
          status: "submitted",
          submission_reference: "FIN-2026-000124",
        };
        return HttpResponse.json(state.claim);
      }),
    );
    const open = await screen.findByRole("button", { name: "Confirm & submit" });
    await waitFor(() => expect(open).toBeEnabled());
    await user.click(open);
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Confirm & submit" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Something went wrong on our side",
    );
    await user.click(within(dialog).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("FIN-2026-000124")).toBeInTheDocument();
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
  });

  it("maps submit problems to friendly copy and keeps the dialog open", async () => {
    const user = userEvent.setup();
    setup(ready());
    server.use(
      http.post(url("/v1/claims/clm-1/submit"), () =>
        problem(409, "claim_not_ready", "The claim is not ready to submit", {
          status: "needs_info",
          unanswered: ["q1"],
        }),
      ),
    );
    const open = await screen.findByRole("button", { name: "Confirm & submit" });
    await waitFor(() => expect(open).toBeEnabled());
    await user.click(open);
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Confirm & submit" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("Not ready to submit yet");
    expect(alert).toHaveTextContent("One question is still open");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("only the owner can submit: others get an explanation and a disabled button", async () => {
    setup(ready(), { owner: false });
    expect(await screen.findByRole("button", { name: "Confirm & submit" })).toBeDisabled();
    expect(screen.getByText("Only the claim's owner can submit it.")).toBeInTheDocument();
  });

  it("shows the outcome of finished claims instead of a submit button", async () => {
    setup(makeClaim({ id: "clm-1", status: "approved", submission_reference: "FIN-2026-000007" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Approved · FIN-2026-000007");
  });

  it("shows a rejected claim as rejected", async () => {
    setup(makeClaim({ id: "clm-1", status: "rejected", submission_reference: "FIN-2026-000008" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Rejected · FIN-2026-000008");
  });
});

describe("helpers", () => {
  it("describes flags in words", () => {
    expect(describeFlags(makeClaim({ findings: [] }))).toBe("None");
    expect(describeFlags(makeClaim({ findings: [makeFinding({ severity: "info" })] }))).toBe(
      "1 note",
    );
    expect(
      describeFlags(
        makeClaim({ findings: [makeFinding(), makeFinding(), makeFinding({ severity: "warn" })] }),
      ),
    ).toBe("2 high risk, 1 warning");
  });

  it("explains routing using only what the API tells us", () => {
    expect(routeReasons(makeClaim({ route: "auto_approve" }))).toEqual([]);
    expect(routeReasons(makeClaim({ route: "finance_review", findings: [] }))).toEqual([
      "the claim total",
    ]);
    expect(
      routeReasons(
        makeClaim({
          route: "finance_review",
          findings: [makeFinding(), makeFinding({ severity: "warn" })],
          open_questions: [makeQuestion()],
        }),
      ),
    ).toEqual(["1 high-risk flag", "1 warning", "1 open question"]);
  });
});
