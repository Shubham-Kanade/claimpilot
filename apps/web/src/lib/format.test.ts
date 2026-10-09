import { describe, expect, it } from "vitest";

import {
  confidenceLabel,
  formatBytes,
  formatClock,
  formatDate,
  formatDateRange,
  formatINR,
  formatMinutes,
  formatPercent,
  formatSeconds,
  formatUSD,
  humanize,
  parseApiTimestamp,
  pluralize,
} from "./format";

describe("parseApiTimestamp", () => {
  const utc = Date.UTC(2026, 9, 8, 12, 54, 16, 303);

  it("reads the API's zone-less date-times as UTC, whatever the viewer's time zone", () => {
    // Python writes naive UTC with microseconds: JavaScript alone would read it as local time.
    expect(parseApiTimestamp("2026-10-08T12:54:16.303656")).toBe(utc);
    expect(parseApiTimestamp("2026-10-08T12:54:16.303")).toBe(utc);
    expect(parseApiTimestamp("2026-10-08T12:54:16")).toBe(utc - 303);
  });

  it("leaves a stated zone alone", () => {
    expect(parseApiTimestamp("2026-10-08T12:54:16.303Z")).toBe(utc);
    expect(parseApiTimestamp("2026-10-08T12:54:16.303656Z")).toBe(utc);
    expect(parseApiTimestamp("2026-10-08T18:24:16.303+05:30")).toBe(utc);
    expect(parseApiTimestamp("2026-10-08T07:54:16.303-0500")).toBe(utc);
  });

  it("reads a plain date as UTC midnight, and gives NaN for nothing or nonsense", () => {
    expect(parseApiTimestamp("2026-10-08")).toBe(Date.UTC(2026, 9, 8));
    expect(parseApiTimestamp(null)).toBeNaN();
    expect(parseApiTimestamp(undefined)).toBeNaN();
    expect(parseApiTimestamp("")).toBeNaN();
    expect(parseApiTimestamp("yesterday")).toBeNaN();
  });
});

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
    [0.7, "medium"],
    [0.3, "low"],
  ] as const)("%s -> %s", (value, label) => {
    expect(confidenceLabel(value)).toBe(label);
  });
});

describe("formatPercent", () => {
  it("rounds a fraction to a whole percentage", () => {
    expect(formatPercent(0.934)).toBe("93%");
    expect(formatPercent(1)).toBe("100%");
    expect(formatPercent(0)).toBe("0%");
  });
});

describe("formatDate", () => {
  it("formats ISO dates for India: 14 Aug 2026", () => {
    expect(formatDate("2026-08-14")).toBe("14 Aug 2026");
    expect(formatDate("2026-09-01")).toBe("1 Sep 2026");
  });

  it("never shifts the day with the time zone (dates are parsed, not converted)", () => {
    expect(formatDate("2026-01-01")).toBe("1 Jan 2026");
    expect(formatDate("2026-12-31T23:30:00Z")).toBe("31 Dec 2026");
  });

  it("returns an em dash for missing values and the raw text for garbage", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate(undefined)).toBe("—");
    expect(formatDate("")).toBe("—");
    expect(formatDate("yesterday")).toBe("yesterday");
    expect(formatDate("2026-13-40")).toBe("2026-13-40");
  });
});

describe("formatDateRange", () => {
  it("collapses a range inside one month", () => {
    expect(formatDateRange("2026-08-12", "2026-08-14")).toBe("12–14 Aug 2026");
  });

  it("keeps both months inside one year", () => {
    expect(formatDateRange("2026-07-30", "2026-08-02")).toBe("30 Jul–2 Aug 2026");
  });

  it("shows both full dates across years", () => {
    expect(formatDateRange("2026-12-30", "2027-01-02")).toBe("30 Dec 2026–2 Jan 2027");
  });

  it("shows a single date when start and end are the same day", () => {
    expect(formatDateRange("2026-08-12", "2026-08-12")).toBe("12 Aug 2026");
  });

  it("degrades gracefully when a bound is missing", () => {
    expect(formatDateRange("2026-08-12", null)).toBe("12 Aug 2026");
    expect(formatDateRange(null, "2026-08-14")).toBe("14 Aug 2026");
    expect(formatDateRange(null, null)).toBe("Dates to be confirmed");
  });
});

describe("formatUSD", () => {
  it("shows sub-cent LLM costs with four decimals", () => {
    expect(formatUSD(0.0042)).toBe("$0.0042");
    expect(formatUSD(0.00045)).toBe("$0.0005");
  });

  it("uses two decimals from one cent up and handles zero", () => {
    expect(formatUSD(0.0211)).toBe("$0.02");
    expect(formatUSD(12.5)).toBe("$12.50");
    expect(formatUSD(0)).toBe("$0.00");
  });
});

describe("durations", () => {
  it("formats a running clock", () => {
    expect(formatClock(7)).toBe("0:07");
    expect(formatClock(65.9)).toBe("1:05");
    expect(formatClock(-3)).toBe("0:00");
  });

  it("formats seconds and minutes for people", () => {
    expect(formatSeconds(9.44)).toBe("9.4 s");
    expect(formatSeconds(72)).toBe("1 min 12 s");
    expect(formatMinutes(42)).toBe("42 min");
    expect(formatMinutes(188)).toBe("3 h 8 min");
    expect(formatMinutes(120)).toBe("2 h");
  });
});

describe("formatBytes", () => {
  it("scales to B, KB and MB", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(5 * 1024 * 1024 + 300000)).toBe("5.3 MB");
  });
});

describe("humanize and pluralize", () => {
  it("turns codes into sentences", () => {
    expect(humanize("client_entertainment")).toBe("Client entertainment");
  });

  it("pluralizes with a count", () => {
    expect(pluralize(1, "receipt")).toBe("1 receipt");
    expect(pluralize(3, "receipt")).toBe("3 receipts");
    expect(pluralize(2, "flag", "flags!")).toBe("2 flags!");
  });
});

describe("operations formatters", () => {
  it("formats latency, rates and times for people", async () => {
    const { formatDateTime, formatLatency, formatRate } = await import("./format");
    expect(formatLatency(850)).toBe("850 ms");
    expect(formatLatency(2400)).toBe("2.4 s");
    expect(formatRate(0.0123)).toBe("1.2%");
    expect(formatRate(0)).toBe("0.0%");
    expect(formatDateTime("2026-10-09T09:15:00")).toMatch(/^\d{1,2} Oct, \d{2}:\d{2}$/);
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime("nonsense")).toBe("—");
  });
});
