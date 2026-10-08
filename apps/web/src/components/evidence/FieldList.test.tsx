import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { makeReceipt } from "@/test/fixtures";

import { FieldList } from "./FieldList";

const box = { page: 0, x: 0.4, y: 0.05, w: 0.28, h: 0.05 };

/** Some receipt has locations: the rows are buttons that highlight where a value is printed. */
const LOCATED = { merchant_name: box };

function setup(
  receipt = makeReceipt(),
  boxes: Record<string, typeof box> = LOCATED,
  selected: string | null = null,
) {
  const onSelect = vi.fn();
  render(<FieldList receipt={receipt} boxes={boxes} selected={selected} onSelect={onSelect} />);
  return { onSelect };
}

describe("FieldList", () => {
  it("lists what was read, with INR amounts and Indian dates", () => {
    setup();
    const list = screen.getByRole("list", { name: "Extracted fields" });
    expect(
      within(list).getByRole("button", { name: /Merchant\s*Lotus Bay Suites/ }),
    ).toBeInTheDocument();
    expect(within(list).getByRole("button", { name: /Date\s*18 Aug 2026/ })).toBeInTheDocument();
    expect(within(list).getByRole("button", { name: /Total\s*₹22,680.00/ })).toBeInTheDocument();
    expect(
      within(list).getByRole("button", { name: /GSTIN\s*04ZPWCN7743D8ZE/ }),
    ).toBeInTheDocument();
  });

  it("marks low-confidence fields with icon + words, and others as read clearly", () => {
    setup(makeReceipt({ low_confidence_fields: ["date"] }));
    const date = screen.getByRole("button", { name: /Date/ });
    expect(within(date).getByText("Low confidence")).toBeInTheDocument();
    const total = screen.getByRole("button", { name: /Total/ });
    expect(within(total).queryByText("Low confidence")).not.toBeInTheDocument();
    expect(within(total).getByText("Read clearly")).toBeInTheDocument();
  });

  it("reports required fields that could not be found", () => {
    setup(makeReceipt({ merchant_name: null, total: null }));
    expect(
      within(screen.getByRole("button", { name: /Merchant/ })).getByText("Not found", {
        selector: "span span",
      }),
    ).toBeInTheDocument();
  });

  it("selects a field when clicked and unselects it when clicked again", async () => {
    const user = userEvent.setup();
    const { onSelect } = setup();
    await user.click(screen.getByRole("button", { name: /Total/ }));
    expect(onSelect).toHaveBeenLastCalledWith("total");
  });

  it("shows the selection (aria-pressed), also for a field that has no location of its own", () => {
    setup(makeReceipt(), LOCATED, "total");
    expect(screen.getByRole("button", { name: /Total/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /Date/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("clears the selection when the selected field is clicked again", async () => {
    const user = userEvent.setup();
    const { onSelect } = setup(makeReceipt(), LOCATED, "total");
    await user.click(screen.getByRole("button", { name: /Total/ }));
    expect(onSelect).toHaveBeenLastCalledWith(null);
  });

  describe("when the API gave no locations at all (recorded replays)", () => {
    it("shows plain rows, not buttons that would do nothing, and still reads everything out", () => {
      setup(makeReceipt({ low_confidence_fields: ["date"] }), {});
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
      const list = screen.getByRole("list", { name: "Extracted fields" });
      const items = within(list).getAllByRole("listitem");
      expect(items.length).toBeGreaterThan(5);
      expect(within(list).getByText("Lotus Bay Suites")).toBeInTheDocument();
      expect(within(list).getByText("₹22,680.00")).toBeInTheDocument();
      // the confidence markers are still words, not just colour
      expect(within(list).getByText("Low confidence")).toBeInTheDocument();
      expect(within(list).getAllByText("Read clearly").length).toBeGreaterThan(0);
    });

    it("keeps the line items table, with a plain heading", () => {
      setup(makeReceipt({ low_confidence_fields: ["line_items"] }), {});
      expect(screen.getByRole("table", { name: "Line items read from the receipt" })).toBeVisible();
      expect(screen.getByText("Line items (3)")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Line items/ })).not.toBeInTheDocument();
      expect(screen.getByText("Low confidence")).toBeInTheDocument();
    });
  });

  it("only advertises a location for fields that have a box", () => {
    setup(makeReceipt(), { merchant_name: box });
    const merchant = screen.getByRole("button", { name: /Merchant/ });
    expect(
      within(merchant).getByLabelText("Location on the receipt available"),
    ).toBeInTheDocument();
    expect(merchant).toHaveAttribute("title", "Show where this is printed");
    const total = screen.getByRole("button", { name: /Total/ });
    expect(
      within(total).queryByLabelText("Location on the receipt available"),
    ).not.toBeInTheDocument();
    expect(total).not.toHaveAttribute("title");
  });

  it("tabulates line items and lets the whole table be selected", async () => {
    const user = userEvent.setup();
    const { onSelect } = setup(makeReceipt({ low_confidence_fields: ["line_items"] }));
    const table = screen.getByRole("table", { name: "Line items read from the receipt" });
    expect(within(table).getAllByRole("row")).toHaveLength(4); // header + 3 nights
    expect(within(table).getByText("Room Charges 15-Aug")).toBeInTheDocument();
    expect(within(table).getByText("₹7,700.00")).toBeInTheDocument();
    const heading = screen.getByRole("button", { name: /Line items \(3\)/ });
    expect(within(heading).getByText("Low confidence")).toBeInTheDocument();
    await user.click(heading);
    expect(onSelect).toHaveBeenCalledWith("line_items");
  });

  it("omits the table when there are no line items", () => {
    setup(makeReceipt({ line_items: [] }));
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
