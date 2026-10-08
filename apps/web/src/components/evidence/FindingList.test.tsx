import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { HOTEL_CAP_FINDING, makeFinding } from "@/test/fixtures";

import { FindingList, PolicyCitation } from "./FindingList";

describe("FindingList", () => {
  it("says so when there is nothing to flag", () => {
    render(<FindingList findings={[]} />);
    expect(screen.getByText("No flags on this document.")).toBeInTheDocument();
    render(<FindingList findings={[]} emptyText="Nothing here." />);
    expect(screen.getByText("Nothing here.")).toBeInTheDocument();
  });

  it("groups findings by severity, highest first, with icon + word (never colour alone)", () => {
    render(
      <FindingList
        findings={[
          makeFinding({ severity: "info", code: "exif", message: "Photo date differs by 2 days." }),
          makeFinding({ severity: "high", message: "Totals do not add up." }),
          makeFinding({ severity: "warn", code: "preapproval", message: "Needs approval." }),
        ]}
      />,
    );
    const headings = screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent);
    expect(headings).toEqual(["High risk (1)", "Warnings (1)", "Notes (1)"]);
    const high = screen.getByText("High risk (1)").parentElement!;
    expect(within(high).getByText("High risk", { selector: "span span" })).toBeInTheDocument();
    expect(within(high).getByText("Totals do not add up.")).toBeInTheDocument();
    expect(high.querySelector("svg")).not.toBeNull(); // the icon
    expect(screen.getByText("Warnings (1)").parentElement).toHaveTextContent("Needs approval.");
    expect(screen.getByText("Notes (1)").parentElement).toHaveTextContent("Photo date differs");
  });

  it("shows the limit against the amount so people can judge the flag themselves", () => {
    render(<FindingList findings={[HOTEL_CAP_FINDING]} />);
    const item = screen.getByRole("listitem");
    // named for what they are (a cap and a room rate), not just "expected" and "actual"
    expect(within(item).getByText("Nightly cap").nextElementSibling).toHaveTextContent("₹7,500.00");
    expect(within(item).getByText("Room per night").nextElementSibling).toHaveTextContent(
      "₹7,700.00",
    );
    expect(within(item).getByText("Policy check")).toBeInTheDocument();
  });

  it("falls back to expected and actual when the finding has no names for its numbers", () => {
    render(
      <FindingList
        findings={[makeFinding({ code: "gst_rate_mismatch", expected: 5, actual: 18, fields: [] })]}
      />,
    );
    const item = screen.getByRole("listitem");
    expect(within(item).getByText("Expected").nextElementSibling).toHaveTextContent("5%");
    expect(within(item).getByText("Actual").nextElementSibling).toHaveTextContent("18%");
  });

  it("explains an alcohol flag as what to take out of which total", () => {
    render(
      <FindingList
        findings={[
          makeFinding({
            code: "alcohol_not_reimbursable",
            source: "policy",
            fields: ["line_items"],
            expected: 1540,
            actual: 3178,
            message: "This bill includes alcohol (2 lines, ₹1,540).",
          }),
        ]}
      />,
    );
    const item = screen.getByRole("listitem");
    expect(within(item).getByText("Alcohol to take out").nextElementSibling).toHaveTextContent(
      "₹1,540.00",
    );
    expect(within(item).getByText("Bill total").nextElementSibling).toHaveTextContent("₹3,178.00");
    expect(within(item).queryByText("Expected")).not.toBeInTheDocument();
  });

  it("quotes the policy clause as a citation with its id", () => {
    render(<FindingList findings={[HOTEL_CAP_FINDING]} />);
    const citation = screen.getByRole("figure");
    expect(
      within(citation).getByText(/Hotel rooms are reimbursed up to a nightly limit/),
    ).toBeInTheDocument();
    expect(within(citation).getByText("Policy clause 4.1")).toBeInTheDocument();
    expect(citation.querySelector("blockquote")).not.toBeNull();
  });

  it("omits the comparison and the citation when the finding has neither", () => {
    render(<FindingList findings={[makeFinding({ message: "Plain finding." })]} />);
    expect(screen.queryByText("Expected")).not.toBeInTheDocument();
    expect(screen.queryByRole("figure")).not.toBeInTheDocument();
  });

  it("renders a citation without clause text (id only)", () => {
    render(<PolicyCitation clauseId="9.1" />);
    expect(screen.getByText("Policy clause 9.1")).toBeInTheDocument();
    expect(screen.queryByRole("blockquote")).not.toBeInTheDocument();
  });

  it("lets people jump to the evidence on the receipt for findings that name fields", async () => {
    const user = userEvent.setup();
    const onShowField = vi.fn();
    render(
      <FindingList
        findings={[
          HOTEL_CAP_FINDING,
          makeFinding({ code: "no-fields", fields: [], message: "No fields." }),
        ]}
        onShowField={onShowField}
      />,
    );
    const buttons = screen.getAllByRole("button", { name: "Show on the receipt" });
    expect(buttons).toHaveLength(1); // only the finding that names receipt fields
    await user.click(buttons[0]);
    expect(onShowField).toHaveBeenCalledWith(HOTEL_CAP_FINDING);
  });

  it("labels where each finding came from", () => {
    render(
      <FindingList
        findings={[
          makeFinding({ source: "trust", message: "a" }),
          makeFinding({ source: "decision", message: "b", code: "b" }),
          makeFinding({ source: "system", message: "c", code: "c" }),
        ]}
      />,
    );
    expect(screen.getByText("Trust check")).toBeInTheDocument();
    expect(screen.getByText("System One")).toBeInTheDocument();
    expect(screen.getByText("System note")).toBeInTheDocument();
  });
});
