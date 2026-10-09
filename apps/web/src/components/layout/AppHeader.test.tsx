import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { navigationState } from "@/test/navigation";
import { renderApp } from "@/test/render";

import { AppHeader } from "./AppHeader";

describe("AppHeader navigation", () => {
  it("offers AI ops to every persona, after Impact", async () => {
    renderApp(<AppHeader />);
    const nav = screen.getByRole("navigation", { name: "Main" });
    const labels = within(nav)
      .getAllByRole("link")
      .map((link) => link.textContent);
    expect(labels).toEqual(["Upload", "My claims", "Impact", "AI ops"]);
    expect(within(nav).getByRole("link", { name: "AI ops" })).toHaveAttribute(
      "href",
      "/operations",
    );
  });

  it("marks AI ops as the current page on /operations", () => {
    navigationState.pathname = "/operations";
    renderApp(<AppHeader />);
    expect(screen.getByRole("link", { name: "AI ops" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Impact" })).not.toHaveAttribute("aria-current");
  });
});
