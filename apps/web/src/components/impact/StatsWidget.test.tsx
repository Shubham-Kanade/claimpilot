import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { META, makeStats } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { problem, url } from "@/test/server";

import { ImpactScreen } from "./ImpactScreen";
import { autoApproveShare, StatsPanel, StatsWidget } from "./StatsWidget";

describe("autoApproveShare", () => {
  it("is a whole percentage and never divides by zero", () => {
    expect(autoApproveShare({ claims: 9, auto_approvable_claims: 3 })).toBe(33);
    expect(autoApproveShare({ claims: 0, auto_approvable_claims: 0 })).toBe(0);
    expect(autoApproveShare({ claims: 3, auto_approvable_claims: 3 })).toBe(100);
  });
});

describe("StatsPanel", () => {
  it("labels time saved as an ESTIMATE and states the assumption behind it", () => {
    render(<StatsPanel stats={makeStats()} variant="compact" />);
    expect(screen.getByText("Time saved (estimated)")).toBeInTheDocument();
    expect(screen.getByText("3 h 8 min")).toBeInTheDocument();
    expect(
      screen.getByText(/Estimate: assumes 4 min of manual work per receipt\./),
    ).toBeInTheDocument();
  });

  it("shows receipts processed, cost per receipt and the low-risk share", () => {
    render(<StatsPanel stats={makeStats()} variant="compact" />);
    expect(screen.getByText("Receipts processed")).toBeInTheDocument();
    expect(screen.getByText("47")).toBeInTheDocument();
    expect(screen.getByText("2 files could not be read")).toBeInTheDocument();
    expect(screen.getByText("LLM cost per receipt")).toBeInTheDocument();
    expect(screen.getByText("$0.0005")).toBeInTheDocument();
    expect(screen.getByText(/\$0\.02 in total over 49 LLM calls\./)).toBeInTheDocument();
    expect(screen.getByText("33%")).toBeInTheDocument();
    expect(screen.getByText("Low-risk claims")).toBeInTheDocument();
    expect(
      screen.getByText("3 of 9 claims are small, clean and complete: one click to approve."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Average processing time")).not.toBeInTheDocument();
  });

  it("says the cost is the RECORDED one when the answers are replayed, not money spent now", () => {
    render(<StatsPanel stats={makeStats()} variant="compact" profile="recorded" />);
    expect(screen.getByText("LLM cost per receipt (as recorded)")).toBeInTheDocument();
    expect(
      screen.getByText(
        "$0.02 in total over 49 LLM calls, as recorded with the replayed answers. Nothing is spent when you try the demo.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("LLM cost per receipt")).not.toBeInTheDocument();
  });

  it("the full variant adds processing time and claims by status", () => {
    render(<StatsPanel stats={makeStats()} variant="full" />);
    expect(screen.getByText("Average processing time")).toBeInTheDocument();
    expect(screen.getByText("9.4 s")).toBeInTheDocument();
    const byStatus = screen.getByRole("heading", { name: "Claims by status" }).nextElementSibling!;
    expect(byStatus).toHaveTextContent("Submitted");
    expect(byStatus).toHaveTextContent("4");
    expect(byStatus).toHaveTextContent("Approved");
  });

  it("is honest about missing data instead of showing zeros", () => {
    render(
      <StatsPanel
        stats={makeStats({
          documents_processed: 0,
          documents_failed: 0,
          claims: 0,
          claims_by_status: {},
          auto_approvable_claims: 0,
          llm_calls: 0,
          llm_cost_usd: 0,
          llm_cost_per_document_usd: null,
          avg_batch_seconds: null,
          estimated_minutes_saved: 0,
        })}
        variant="full"
      />,
    );
    expect(screen.getByText("Every file read successfully")).toBeInTheDocument();
    expect(screen.getAllByText("—")).toHaveLength(2); // cost per receipt, processing time
    expect(screen.getByText("0%")).toBeInTheDocument();
    expect(screen.getByText("0 min")).toBeInTheDocument();
  });
});

describe("StatsWidget", () => {
  it("loads the numbers from GET /v1/stats", async () => {
    renderApp(<StatsWidget />, {
      handlers: [
        http.get(url("/v1/stats"), () =>
          HttpResponse.json(makeStats({ documents_processed: 321 })),
        ),
      ],
    });
    expect(screen.getByLabelText("Loading impact numbers")).toHaveAttribute("aria-busy", "true");
    expect(await screen.findByText("321")).toBeInTheDocument();
  });

  it("shows a friendly error with retry", async () => {
    renderApp(<StatsWidget />, {
      handlers: [http.get(url("/v1/stats"), () => problem(503, "http_503", "down"))],
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on our side");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("StatsWidget and the LLM profile", () => {
  it("says recorded AND live in the hybrid profile", async () => {
    renderApp(<StatsWidget />, {
      handlers: [
        http.get(url("/v1/meta"), () =>
          HttpResponse.json({ ...META, llm_mode: "live", llm_record: true }),
        ),
      ],
    });
    expect(
      await screen.findByText("LLM cost per receipt (as recorded or live)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/partly recorded with the replayed sample answers, partly spent live/),
    ).toBeInTheDocument();
  });

  it("labels the costs as recorded once the API says the answers are replayed", async () => {
    renderApp(<StatsWidget />); // the default test API runs in replay mode
    expect(await screen.findByText("LLM cost per receipt (as recorded)")).toBeInTheDocument();
  });

  it("keeps the plain wording when the model is really called (live mode)", async () => {
    renderApp(<StatsWidget />, {
      handlers: [http.get(url("/v1/meta"), () => HttpResponse.json({ ...META, llm_mode: "live" }))],
    });
    expect(await screen.findByText("LLM cost per receipt")).toBeInTheDocument();
    expect(screen.queryByText(/as recorded/)).not.toBeInTheDocument();
  });
});

describe("ImpactScreen", () => {
  it("never claims anything skips human review: low-risk claims need the approver's click", async () => {
    renderApp(<ImpactScreen />);
    const method = (await screen.findByText("How these numbers are worked out")).closest(
      "section",
    )!;
    expect(method).toHaveTextContent(
      "Low-risk claims are small, have no flags and no open questions, so an approver can approve them in one click. Nothing is approved without that click.",
    );
    expect(method).not.toHaveTextContent(/skip a human review|without a manual review/i);
  });

  it("says in replay mode that the costs are the recorded ones", async () => {
    renderApp(<ImpactScreen />);
    expect(
      await screen.findByText(
        /the answers are replayed from recordings, so these are the costs recorded with them, not money spent now/,
      ),
    ).toBeInTheDocument();
  });

  it("explains recorded and live costs in the hybrid profile", async () => {
    renderApp(<ImpactScreen />, {
      handlers: [
        http.get(url("/v1/meta"), () =>
          HttpResponse.json({ ...META, llm_mode: "live", llm_record: true }),
        ),
      ],
    });
    expect(
      await screen.findByText(
        /receipts you upload yourself are read live and cost real money, within a shared daily budget/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/not money spent now/)).not.toBeInTheDocument();
  });

  it("does not say that when the model is really called", async () => {
    renderApp(<ImpactScreen />, {
      handlers: [http.get(url("/v1/meta"), () => HttpResponse.json({ ...META, llm_mode: "live" }))],
    });
    expect(await screen.findByText("How these numbers are worked out")).toBeInTheDocument();
    await screen.findByText("Average processing time");
    expect(screen.queryByText(/replayed from recordings/)).not.toBeInTheDocument();
  });

  it("explains how every number is worked out", async () => {
    renderApp(<ImpactScreen />);
    expect(screen.getByRole("heading", { level: 1, name: "Impact" })).toBeInTheDocument();
    expect(
      screen.getByText(/time saved is an estimate based on a stated assumption/),
    ).toBeInTheDocument();
    expect(await screen.findByText("Average processing time")).toBeInTheDocument();
    expect(screen.getByText("How these numbers are worked out")).toBeInTheDocument();
  });
});
