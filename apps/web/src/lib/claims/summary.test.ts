import { describe, expect, it } from "vitest";

import { makeClaim, makeFinding, makeQuestion } from "@/test/fixtures";

import {
  answeredQuestions,
  comparisonLabels,
  countBySeverity,
  findingFields,
  formatFindingValue,
  groupBySeverity,
  hasComparison,
  parseAnswer,
  parsePrompt,
  plainFindingMessage,
  orderReceipts,
  sortByRisk,
  sortForReview,
  splitDocumentReferences,
  topFinding,
  unansweredQuestions,
} from "./summary";

const high = makeFinding({ severity: "high" });
const warn = makeFinding({ severity: "warn", code: "preapproval_required" });
const info = makeFinding({ severity: "info", code: "exif_date_mismatch" });

describe("severity helpers", () => {
  it("counts findings by severity", () => {
    expect(countBySeverity([high, high, warn, info])).toEqual({ high: 2, warn: 1, info: 1 });
    expect(countBySeverity([])).toEqual({ high: 0, warn: 0, info: 0 });
    expect(countBySeverity(undefined)).toEqual({ high: 0, warn: 0, info: 0 });
  });

  it("groups non-empty severities, highest first", () => {
    const groups = groupBySeverity([info, high, warn, high]);
    expect(groups.map((g) => [g.severity, g.findings.length])).toEqual([
      ["high", 2],
      ["warn", 1],
      ["info", 1],
    ]);
    expect(groupBySeverity([warn]).map((g) => g.severity)).toEqual(["warn"]);
    expect(groupBySeverity(undefined)).toEqual([]);
  });
});

describe("sortByRisk", () => {
  it("puts claims with high findings first, then warnings, keeping the API order for ties", () => {
    const clean = makeClaim({ id: "clean", findings: [] });
    const warned = makeClaim({ id: "warned", findings: [warn, warn] });
    const risky = makeClaim({ id: "risky", findings: [high] });
    const riskier = makeClaim({ id: "riskier", findings: [high, high] });
    const cleanToo = makeClaim({ id: "clean-too", findings: [info] });
    expect(sortByRisk([clean, warned, risky, cleanToo, riskier]).map((c) => c.id)).toEqual([
      "riskier",
      "risky",
      "warned",
      "clean",
      "clean-too",
    ]);
  });

  it("does not mutate its input", () => {
    const claims = [makeClaim({ id: "a" }), makeClaim({ id: "b", findings: [high] })];
    sortByRisk(claims);
    expect(claims.map((c) => c.id)).toEqual(["a", "b"]);
  });
});

describe("sortForReview", () => {
  it("puts claims waiting for an answer first, then ready ones, then those with finance", () => {
    const done = makeClaim({ id: "done", status: "approved", findings: [high, high] });
    const submitted = makeClaim({ id: "submitted", status: "submitted", findings: [high] });
    const readyClean = makeClaim({ id: "ready-clean", status: "ready", findings: [] });
    const readyRisky = makeClaim({ id: "ready-risky", status: "ready", findings: [high] });
    const asking = makeClaim({ id: "asking", status: "needs_info", findings: [] });
    const askingRisky = makeClaim({ id: "asking-risky", status: "needs_info", findings: [warn] });
    const draft = makeClaim({ id: "draft", status: "draft", findings: [] });
    const rejected = makeClaim({ id: "rejected", status: "rejected", findings: [] });
    const sorted = sortForReview([
      done,
      submitted,
      readyClean,
      asking,
      readyRisky,
      draft,
      askingRisky,
      rejected,
    ]);
    expect(sorted.map((c) => c.id)).toEqual([
      "asking-risky", // waiting for an answer, riskiest first
      "asking", // ... then the others, in the API order
      "draft",
      "ready-risky", // ready to submit, riskiest first
      "ready-clean",
      "submitted",
      "done", // finished claims last, whatever their flags
      "rejected",
    ]);
  });

  it("keeps the API order for equals and never mutates its input", () => {
    const claims = [
      makeClaim({ id: "a", status: "ready" }),
      makeClaim({ id: "b", status: "ready" }),
      makeClaim({ id: "c", status: "ready" }),
    ];
    expect(sortForReview(claims).map((c) => c.id)).toEqual(["a", "b", "c"]);
    expect(claims.map((c) => c.id)).toEqual(["a", "b", "c"]);
  });
});

describe("orderReceipts", () => {
  const flag = (document_id: string | null, severity: "high" | "warn" | "info") =>
    makeFinding({ document_id, severity });

  it("puts receipts with flags first, the most serious first, keeping the API order otherwise", () => {
    const claim = {
      document_ids: ["a", "b", "c", "d", "e"],
      findings: [flag("d", "warn"), flag("c", "high"), flag("c", "warn"), flag("e", "info")],
    };
    expect(orderReceipts(claim)).toEqual(["c", "d", "e", "a", "b"]);
  });

  it("ignores findings about the whole claim and leaves clean claims in the API order", () => {
    expect(orderReceipts({ document_ids: ["x", "y"], findings: [flag(null, "high")] })).toEqual([
      "x",
      "y",
    ]);
    expect(orderReceipts({ document_ids: ["x", "y"], findings: undefined })).toEqual(["x", "y"]);
  });

  it("does not mutate the claim", () => {
    const claim = { document_ids: ["a", "b"], findings: [flag("b", "high")] };
    expect(orderReceipts(claim)).toEqual(["b", "a"]);
    expect(claim.document_ids).toEqual(["a", "b"]);
  });
});

describe("topFinding", () => {
  it("picks the most serious flag that explains a claim, never a mere note", () => {
    const second = makeFinding({ severity: "high", code: "second" });
    expect(topFinding([info, warn, high, second])).toBe(high);
    expect(topFinding([info, warn])).toBe(warn);
    expect(topFinding([info])).toBeNull();
    expect(topFinding([])).toBeNull();
    expect(topFinding(undefined)).toBeNull();
  });
});

describe("references to other receipts in a finding", () => {
  const id = "e9cf5624e1434f30b53b0e3bdd528ee0";
  const message = `This picture is almost identical to document ${id}, uploaded earlier (a copy).`;

  it("splits the message around the receipt id, keeping the words around it", () => {
    expect(splitDocumentReferences(message)).toEqual([
      { text: "This picture is almost identical to " },
      { documentId: id },
      { text: ", uploaded earlier (a copy)." },
    ]);
    expect(splitDocumentReferences("Nothing to resolve here.")).toEqual([
      { text: "Nothing to resolve here." },
    ]);
    // a bare id (no "document" before it) is found too
    expect(splitDocumentReferences(`Seen as ${id}`)).toEqual([
      { text: "Seen as " },
      { documentId: id },
    ]);
  });

  it("says 'another receipt' where the id was, for places that cannot look the name up", () => {
    expect(plainFindingMessage(message)).toBe(
      "This picture is almost identical to another receipt, uploaded earlier (a copy).",
    );
    expect(plainFindingMessage("The total is wrong.")).toBe("The total is wrong.");
    // ids shorter or longer than 32 hex characters are not receipt ids
    expect(plainFindingMessage("Invoice 20260619abc stays")).toBe("Invoice 20260619abc stays");
  });
});

describe("comparisonLabels", () => {
  it("names the two numbers of a finding by what they are", () => {
    const code = (c: string) => comparisonLabels(makeFinding({ code: c }));
    expect(code("alcohol_not_reimbursable")).toEqual({
      expected: "Alcohol to take out",
      actual: "Bill total",
    });
    expect(code("meals_over_limit")).toEqual({ expected: "Daily limit", actual: "Meals that day" });
    expect(code("total_mismatch").actual).toBe("Printed total");
    expect(code("hotel_over_cap").expected).toBe("Nightly cap");
  });

  it("falls back to expected and actual for any other finding", () => {
    expect(comparisonLabels(makeFinding({ code: "gst_rate_mismatch" }))).toEqual({
      expected: "Expected",
      actual: "Actual",
    });
  });
});

describe("questions", () => {
  it("splits answered and unanswered questions (blank answers are not answers)", () => {
    const claim = makeClaim({
      open_questions: [
        makeQuestion({ id: "q1", answer: null }),
        makeQuestion({ id: "q2", answer: "  " }),
        makeQuestion({ id: "q3", answer: "Client meeting" }),
      ],
    });
    expect(unansweredQuestions(claim).map((q) => q.id)).toEqual(["q1", "q2"]);
    expect(answeredQuestions(claim).map((q) => q.id)).toEqual(["q3"]);
    expect(unansweredQuestions(makeClaim({ open_questions: undefined }))).toEqual([]);
  });

  it("recognises where an answer came from", () => {
    expect(parseAnswer("from calendar: Dinner with Kestrel")).toEqual({
      text: "Dinner with Kestrel",
      source: "calendar",
    });
    expect(parseAnswer("From receipt: Guests: Neha")).toEqual({
      text: "Guests: Neha",
      source: "receipt",
    });
    expect(parseAnswer("Client visit")).toEqual({ text: "Client visit", source: "you" });
  });
});

describe("parsePrompt", () => {
  it("splits the combined message into intro, numbered questions and outro", () => {
    const parsed = parsePrompt(
      "To finish “Client dinner 12 Jul 2026” I need a few details:\n1. Who attended? Please give names.\n2. What was the purpose?\nYou can answer in one message.",
    );
    expect(parsed).toEqual({
      intro: "To finish “Client dinner 12 Jul 2026” I need a few details:",
      items: ["Who attended? Please give names.", "What was the purpose?"],
      outro: "You can answer in one message.",
    });
  });

  it("handles a single question and the 'couldn't match' prefix", () => {
    const parsed = parsePrompt(
      "I couldn't match that to my questions, so I'll ask again.\nTo finish “Mobile & internet Sep 2026” I need one detail:\n1. What is the date?",
    );
    expect(parsed.intro).toContain("I couldn't match that");
    expect(parsed.intro).toContain("I need one detail:");
    expect(parsed.items).toEqual(["What is the date?"]);
    expect(parsed.outro).toBeNull();
  });

  it("keeps a message without numbered lines as plain intro", () => {
    expect(parsePrompt("All good")).toEqual({ intro: "All good", items: [], outro: null });
  });
});

describe("finding values", () => {
  it("formats money, percentages and plain text for people", () => {
    const money = makeFinding({ code: "hotel_over_cap", fields: ["line_items"] });
    expect(formatFindingValue(money, 7500)).toBe("₹7,500.00");
    const rate = makeFinding({ code: "gst_rate_mismatch", fields: ["taxes"] });
    expect(formatFindingValue(rate, 36)).toBe("36%");
    expect(formatFindingValue(rate, 18.123)).toBe("18.12%");
    const doc = makeFinding({ code: "duplicate_exact", fields: [] });
    expect(formatFindingValue(doc, "doc-000003")).toBe("doc-000003");
    const plain = makeFinding({ code: "other", fields: [] });
    expect(formatFindingValue(plain, 42)).toBe("42");
    expect(formatFindingValue(plain, null)).toBe("—");
    expect(formatFindingValue(plain, undefined)).toBe("—");
    expect(formatFindingValue(plain, "")).toBe("—");
  });

  it("knows when there is something to compare", () => {
    expect(hasComparison(makeFinding({ expected: 1, actual: 2 }))).toBe(true);
    expect(hasComparison(makeFinding({ expected: 0, actual: 0 }))).toBe(true);
    expect(hasComparison(makeFinding({ expected: 1, actual: null }))).toBe(false);
    expect(hasComparison(makeFinding({}))).toBe(false);
  });

  it("lists the fields a finding is about", () => {
    expect(findingFields(makeFinding({ fields: ["total", "subtotal"] }))).toEqual([
      "total",
      "subtotal",
    ]);
  });
});
