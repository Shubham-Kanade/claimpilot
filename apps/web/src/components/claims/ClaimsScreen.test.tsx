import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { HOTEL_CAP_FINDING, makeClaim, makeFinding, makeQuestion } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { problem, server, url } from "@/test/server";

import { ClaimCard } from "./ClaimCard";
import { ClaimsScreen, countByStatus, filterClaims } from "./ClaimsScreen";

const claims = [
  makeClaim({
    id: "c1",
    title: "Chandigarh trip 15–18 Aug 2026",
    status: "needs_info",
    findings: [
      HOTEL_CAP_FINDING,
      makeFinding({ severity: "warn", code: "w" }),
      makeFinding({ severity: "info", code: "i" }),
    ],
    open_questions: [makeQuestion()],
    route: "finance_review",
  }),
  makeClaim({
    id: "c2",
    title: "Fuel Jul 2026",
    mode: "period",
    status: "ready",
    city: "Hyderabad",
    start_date: "2026-07-04",
    end_date: "2026-07-04",
    total: 2665.3,
    route: "auto_approve",
    document_ids: ["d1", "d2"],
  }),
  makeClaim({
    id: "c3",
    title: "Learning 18 Jul 2026",
    mode: "event",
    status: "submitted",
    route: "finance_review",
  }),
  makeClaim({ id: "c4", title: "Local conveyance Jul 2026", status: "draft", route: null }),
];

describe("ClaimCard", () => {
  it("shows title, mode, dates, city, total, status, flags by severity and the route", () => {
    renderApp(<ClaimCard claim={claims[0]} />);
    const card = screen.getByTestId("claim-card");
    const link = within(card).getByRole("link", { name: "Chandigarh trip 15–18 Aug 2026" });
    expect(link).toHaveAttribute("href", "/claims/c1");
    expect(card).toHaveTextContent("Trip");
    expect(card).toHaveTextContent("15–18 Aug 2026");
    expect(card).toHaveTextContent("Chandigarh");
    expect(card).toHaveTextContent("₹22,680.00");
    expect(card).toHaveTextContent("Needs your input");
    expect(card).toHaveTextContent("1 high risk");
    expect(card).toHaveTextContent("1 warning");
    expect(card).toHaveTextContent("1 note");
    expect(card).toHaveTextContent("Finance review");
    expect(card).toHaveTextContent("1 receipt");
  });

  it("says 'No flags' and 'Low risk' for a clean, small claim", () => {
    renderApp(<ClaimCard claim={claims[1]} />);
    const card = screen.getByTestId("claim-card");
    expect(card).toHaveTextContent("No flags");
    expect(card).toHaveTextContent("Low risk");
    expect(card).toHaveTextContent("Monthly");
    expect(card).toHaveTextContent("4 Jul 2026");
    expect(card).toHaveTextContent("2 receipts");
  });

  it("tolerates a claim without dates, city or route", () => {
    renderApp(
      <ClaimCard
        claim={makeClaim({ start_date: null, end_date: null, city: null, route: null })}
      />,
    );
    const card = screen.getByTestId("claim-card");
    expect(card).toHaveTextContent("Dates to be confirmed");
    expect(card).toHaveTextContent("Routing pending");
  });
});

describe("ClaimCard: why it needs attention", () => {
  it("says why in words, right on the card, with the most serious flag", () => {
    renderApp(<ClaimCard claim={claims[0]} />);
    const flag = within(screen.getByTestId("claim-card")).getByTestId("top-flag");
    expect(flag).toHaveTextContent(HOTEL_CAP_FINDING.message);
    // screen readers also hear how serious it is (the icon is decoration)
    expect(flag).toHaveTextContent("High risk: ");
  });

  it("falls back to a warning, and shows nothing for a claim with only notes or no flags", () => {
    const warned = makeClaim({
      findings: [makeFinding({ severity: "warn", code: "w", message: "Looks personal." })],
    });
    const { unmount } = renderApp(<ClaimCard claim={warned} />);
    expect(screen.getByTestId("top-flag")).toHaveTextContent("Warning: Looks personal.");
    unmount();
    renderApp(<ClaimCard claim={makeClaim({ findings: [makeFinding({ severity: "info" })] })} />);
    expect(screen.queryByTestId("top-flag")).not.toBeInTheDocument();
  });

  it("never shows a receipt id on the card: another receipt is named in words", () => {
    const id = "e9cf5624e1434f30b53b0e3bdd528ee0";
    renderApp(
      <ClaimCard
        claim={makeClaim({
          findings: [
            makeFinding({
              message: `This picture is almost identical to document ${id}, uploaded earlier.`,
            }),
          ],
        })}
      />,
    );
    const flag = screen.getByTestId("top-flag");
    expect(flag).toHaveTextContent("almost identical to another receipt, uploaded earlier.");
    expect(flag).not.toHaveTextContent(id);
  });
});

describe("filter helpers", () => {
  it("counts claims by status and filters them (drafts count as needing input)", () => {
    const counts = countByStatus(claims);
    expect(counts).toMatchObject({ all: 4, needs_info: 1, ready: 1, submitted: 1, draft: 1 });
    expect(filterClaims(claims, "all")).toHaveLength(4);
    expect(filterClaims(claims, "needs_info").map((c) => c.id)).toEqual(["c1", "c4"]);
    expect(filterClaims(claims, "ready").map((c) => c.id)).toEqual(["c2"]);
    expect(filterClaims(claims, "approved")).toEqual([]);
  });
});

describe("ClaimsScreen", () => {
  it("lists claims as cards with an upload shortcut", async () => {
    server.use(http.get(url("/v1/claims"), () => HttpResponse.json(claims)));
    renderApp(<ClaimsScreen />);
    expect(await screen.findAllByTestId("claim-card")).toHaveLength(4);
    expect(screen.getByRole("heading", { level: 1, name: "My claims" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "4 claims. The ones that need you come first. Open one to review and submit.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Upload more/ })).toHaveAttribute("href", "/");
  });

  it("puts what needs you first: questions to answer, then ready claims, then those with finance", async () => {
    // the API order is arbitrary for claims created together: the list must not depend on it
    server.use(http.get(url("/v1/claims"), () => HttpResponse.json([...claims].reverse())));
    renderApp(<ClaimsScreen />);
    await screen.findAllByTestId("claim-card");
    const titles = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(titles).toEqual([
      "Chandigarh trip 15–18 Aug 2026", // needs input, with the most serious flags
      "Local conveyance Jul 2026", // a draft is waiting for you too
      "Fuel Jul 2026", // ready to submit
      "Learning 18 Jul 2026", // already submitted
    ]);
  });

  it("filters by status with counts, and announces the result", async () => {
    const user = userEvent.setup();
    server.use(http.get(url("/v1/claims"), () => HttpResponse.json(claims)));
    renderApp(<ClaimsScreen />);
    await screen.findAllByTestId("claim-card");
    const group = screen.getByRole("group", { name: "Filter claims by status" });
    expect(within(group).getByRole("button", { name: "All 4" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(group).getByRole("button", { name: "Needs your input 2" })).toBeInTheDocument();

    await user.click(within(group).getByRole("button", { name: "Ready to submit 1" }));
    expect(screen.getAllByTestId("claim-card")).toHaveLength(1);
    expect(screen.getByText("Fuel Jul 2026")).toBeInTheDocument();
    expect(screen.getByText("Showing 1 claim")).toBeInTheDocument();

    await user.click(within(group).getByRole("button", { name: "Approved 0" }));
    expect(screen.getByText("No claims with this status")).toBeInTheDocument();
  });

  it("has a friendly empty state that points to the upload", async () => {
    server.use(http.get(url("/v1/claims"), () => HttpResponse.json([])));
    renderApp(<ClaimsScreen />);
    expect(await screen.findByText("No claims yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload receipts" })).toHaveAttribute("href", "/");
  });

  it("shows skeletons while loading", () => {
    server.use(http.get(url("/v1/claims"), async () => new Promise(() => undefined)));
    renderApp(<ClaimsScreen />);
    expect(screen.getByLabelText("Loading claims")).toHaveAttribute("aria-busy", "true");
  });

  it("explains a failure and lets people retry", async () => {
    const user = userEvent.setup();
    let calls = 0;
    server.use(
      http.get(url("/v1/claims"), () => {
        calls += 1;
        return calls === 1 ? problem(503, "http_503", "down") : HttpResponse.json(claims);
      }),
    );
    renderApp(<ClaimsScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on our side");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findAllByTestId("claim-card")).toHaveLength(4);
  });
});
