import { describe, expect, it } from "vitest";

import { confidenceLabel, formatINR } from "./format";

describe("formatINR", () => {
  it("uses Indian digit grouping", () => {
    expect(formatINR(123456.5)).toBe("₹1,23,456.50");
  });

  it("always shows two decimals", () => {
    expect(formatINR(80)).toBe("₹80.00");
  });
});

describe("confidenceLabel", () => {
  it.each([
    [0.95, "high"],
    [0.9, "high"],
    [0.75, "medium"],
    [0.3, "low"],
  ] as const)("%s -> %s", (value, label) => {
    expect(confidenceLabel(value)).toBe(label);
  });
});
