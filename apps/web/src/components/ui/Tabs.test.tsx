import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { TabPanel, Tabs, panelId, tabId, type TabItem } from "./Tabs";

const TABS: TabItem<"a" | "b" | "c">[] = [
  { id: "a", label: "Submitted", count: 3 },
  { id: "b", label: "Approved", count: 0 },
  { id: "c", label: "Rejected" },
];

function Harness() {
  const [value, setValue] = useState<"a" | "b" | "c">("a");
  return (
    <>
      <Tabs tabs={TABS} value={value} onChange={setValue} label="Claim status" idPrefix="t" />
      <TabPanel idPrefix="t" id={value}>
        Panel {value}
      </TabPanel>
    </>
  );
}

describe("Tabs", () => {
  it("exposes the WAI-ARIA tab pattern with counts", () => {
    render(<Harness />);
    expect(screen.getByRole("tablist", { name: "Claim status" })).toBeInTheDocument();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false", "false"]);
    expect(tabs[0]).toHaveTextContent("Submitted 3");
    expect(tabs[1]).toHaveTextContent("Approved 0");
    expect(tabs[0]).toHaveAttribute("id", tabId("t", "a"));
    expect(tabs[0]).toHaveAttribute("aria-controls", panelId("t", "a"));
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", tabId("t", "a"));
  });

  it("only the selected tab is in the tab order", () => {
    render(<Harness />);
    expect(screen.getAllByRole("tab").map((t) => t.getAttribute("tabindex"))).toEqual([
      "0",
      "-1",
      "-1",
    ]);
  });

  it("selects on click", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("tab", { name: /Rejected/ }));
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Panel c");
  });

  it("moves with the arrow keys, wrapping, plus Home and End", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    screen.getByRole("tab", { name: /Submitted/ }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: /Approved/ })).toHaveFocus();
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Panel b");
    await user.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(screen.getByRole("tab", { name: /Rejected/ })).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: /Submitted/ })).toHaveFocus();
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: /Rejected/ })).toHaveFocus();
    await user.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: /Submitted/ })).toHaveFocus();
    await user.keyboard("x"); // other keys do nothing
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Panel a");
  });
});
