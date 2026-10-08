import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ConfidenceBadge,
  EngineBadge,
  ModeBadge,
  RouteChip,
  SeverityBadge,
  StatusChip,
  TrustBadge,
} from "./badges";

describe("ConfidenceBadge", () => {
  it.each([
    [0.97, "High 97%"],
    [0.9, "High 90%"],
    [0.78, "Medium 78%"],
    [0.4, "Low 40%"],
  ])("shows the level word and the percentage for %s", (value, text) => {
    render(<ConfidenceBadge value={value} />);
    expect(screen.getByText(text, { exact: false })).toBeInTheDocument();
  });

  it("names what the confidence is about for screen readers", () => {
    render(<ConfidenceBadge value={0.95} what="category confidence" />);
    expect(screen.getByText("category confidence")).toHaveClass("sr-only");
  });
});

describe("StatusChip", () => {
  it.each([
    ["draft", "Draft"],
    ["needs_info", "Needs your input"],
    ["ready", "Ready to submit"],
    ["submitted", "Submitted"],
    ["approved", "Approved"],
    ["rejected", "Rejected"],
  ])("labels %s as '%s'", (status, label) => {
    render(<StatusChip status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("humanises unknown statuses instead of failing", () => {
    render(<StatusChip status="on_hold" />);
    expect(screen.getByText("On hold")).toBeInTheDocument();
  });
});

describe("RouteChip", () => {
  it("distinguishes auto-approvable claims from finance review", () => {
    const { rerender } = render(<RouteChip route="auto_approve" />);
    expect(screen.getByText("Low risk")).toBeInTheDocument();
    rerender(<RouteChip route="finance_review" />);
    expect(screen.getByText("Finance review")).toBeInTheDocument();
    rerender(<RouteChip route={null} />);
    expect(screen.getByText("Routing pending")).toBeInTheDocument();
  });
});

describe("SeverityBadge", () => {
  it("is always icon + words", () => {
    const { container, rerender } = render(<SeverityBadge severity="high" />);
    expect(screen.getByText("High risk")).toBeInTheDocument();
    expect(container.querySelector("svg")).not.toBeNull();
    rerender(<SeverityBadge severity="warn" count={2} />);
    expect(screen.getByText("2 warning")).toBeInTheDocument();
    rerender(<SeverityBadge severity="info" />);
    expect(screen.getByText("Note")).toBeInTheDocument();
  });
});

describe("EngineBadge and ModeBadge", () => {
  it("is honest about which engine decided", () => {
    const { rerender } = render(<EngineBadge engine="jev" />);
    expect(screen.getByText("Jev")).toBeInTheDocument();
    expect(screen.getByText(/System One/)).toBeInTheDocument();
    rerender(<EngineBadge engine="llm" />);
    expect(screen.getByText("LLM")).toBeInTheDocument();
    rerender(<EngineBadge engine="jev+llm" />);
    expect(screen.getByText("Jev + LLM")).toBeInTheDocument();
    rerender(<EngineBadge engine="fake" />);
    expect(screen.getByText("Test engine")).toBeInTheDocument();
    rerender(<EngineBadge engine="truth" />);
    expect(screen.getByText("Ground truth")).toBeInTheDocument();
    rerender(<EngineBadge engine="new_engine" />);
    expect(screen.getByText("New engine")).toBeInTheDocument();
  });

  it("names the claim mode", () => {
    const { rerender } = render(<ModeBadge mode="trip" />);
    expect(screen.getByText("Trip")).toBeInTheDocument();
    rerender(<ModeBadge mode="period" />);
    expect(screen.getByText("Monthly")).toBeInTheDocument();
    rerender(<ModeBadge mode="event" />);
    expect(screen.getByText("Event")).toBeInTheDocument();
    rerender(<ModeBadge mode="allowance" />);
    expect(screen.getByText("Allowance")).toBeInTheDocument();
  });
});

describe("TrustBadge", () => {
  it.each([
    ["clean", "Looks genuine"],
    ["review", "Needs review"],
    ["block", "Blocked"],
    [null, "Not checked"],
  ])("verdict %s reads '%s'", (verdict, label) => {
    render(<TrustBadge verdict={verdict} score={80} />);
    expect(screen.getByText(label, { exact: false })).toBeInTheDocument();
  });

  it("shows the score with its scale for screen readers, and an optional meter", () => {
    const { container } = render(<TrustBadge verdict="review" score={60} showMeter />);
    expect(screen.getByText(/· 60/)).toBeInTheDocument();
    expect(screen.getByText("out of 100 trust score", { exact: false })).toHaveClass("sr-only");
    expect(container.querySelector('[style*="width: 60%"]')).not.toBeNull();
  });

  it("omits the score when there is none", () => {
    render(<TrustBadge verdict="clean" />);
    expect(screen.queryByText(/trust score/)).not.toBeInTheDocument();
  });
});
