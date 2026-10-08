import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Home from "./page";

describe("Home page", () => {
  it("shows the product name and an upload region", () => {
    render(<Home />);
    expect(screen.getByRole("heading", { level: 1, name: "ClaimPilot" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Upload receipts" })).toBeInTheDocument();
  });
});
