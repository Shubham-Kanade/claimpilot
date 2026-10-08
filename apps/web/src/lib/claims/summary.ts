import type { ClaimView, Finding, OpenQuestion, Severity } from "../api/types";
import { formatINR } from "../format";
import { SEVERITY_ORDER } from "../labels";

export type SeverityCounts = Record<Severity, number>;

/**
 * NOTE on `?? []` throughout the UI: openapi-typescript marks Pydantic `default_factory` fields
 * (claim.findings, claim.open_questions, receipt.line_items, ...) as optional although the API
 * always sends them, so every read is defensive.
 */
export function countBySeverity(findings: readonly Finding[] | null | undefined): SeverityCounts {
  const counts: SeverityCounts = { high: 0, warn: 0, info: 0 };
  for (const finding of findings ?? []) counts[finding.severity] += 1;
  return counts;
}

export interface FindingGroup {
  severity: Severity;
  findings: Finding[];
}

/** Non-empty groups, highest severity first. */
export function groupBySeverity(findings: readonly Finding[] | null | undefined): FindingGroup[] {
  return SEVERITY_ORDER.map((severity) => ({
    severity,
    findings: (findings ?? []).filter((f) => f.severity === severity),
  })).filter((group) => group.findings.length > 0);
}

function riskScore(claim: ClaimView): number {
  const counts = countBySeverity(claim.findings);
  return counts.high * 1000 + counts.warn;
}

/** Claims with the most serious findings first; ties keep the API's order. */
export function sortByRisk(claims: readonly ClaimView[]): ClaimView[] {
  return claims
    .map((claim, index) => ({ claim, index }))
    .sort((a, b) => riskScore(b.claim) - riskScore(a.claim) || a.index - b.index)
    .map(({ claim }) => claim);
}

/** How soon a claim needs its owner: waiting for an answer, then ready to submit, then done. */
const WORK_RANK: Partial<Record<ClaimView["status"], number>> = {
  draft: 0,
  needs_info: 0,
  ready: 1,
  submitted: 2,
  approved: 3,
  rejected: 3,
};

/**
 * The order to work through your own claims in: those waiting for you (questions to answer),
 * then those ready to submit, then the ones already with finance; within each group the most
 * serious flags first. Ties keep the API's order, so the list never shuffles on its own.
 */
export function sortForReview(claims: readonly ClaimView[]): ClaimView[] {
  const rank = (claim: ClaimView) => WORK_RANK[claim.status] ?? 1;
  return claims
    .map((claim, index) => ({ claim, index }))
    .sort(
      (a, b) =>
        rank(a.claim) - rank(b.claim) ||
        riskScore(b.claim) - riskScore(a.claim) ||
        a.index - b.index,
    )
    .map(({ claim }) => claim);
}

/**
 * A claim's receipts in the order to look at them: those with flags first (the most serious
 * first), then the rest in the API's order. Findings about the claim as a whole name no receipt.
 */
export function orderReceipts(claim: Pick<ClaimView, "document_ids" | "findings">): string[] {
  const rank = new Map<string, number>();
  for (const finding of claim.findings ?? []) {
    if (!finding.document_id) continue;
    const level = finding.severity === "high" ? 3 : finding.severity === "warn" ? 2 : 1;
    rank.set(finding.document_id, Math.max(rank.get(finding.document_id) ?? 0, level));
  }
  return claim.document_ids
    .map((id, index) => ({ id, index }))
    .sort((a, b) => (rank.get(b.id) ?? 0) - (rank.get(a.id) ?? 0) || a.index - b.index)
    .map(({ id }) => id);
}

/** The finding that best explains why a claim needs attention (high, else warning; never a note). */
export function topFinding(findings: readonly Finding[] | null | undefined): Finding | null {
  for (const severity of ["high", "warn"] as const) {
    const found = (findings ?? []).find((finding) => finding.severity === severity);
    if (found) return found;
  }
  return null;
}

/** "document 4b0077be…" or a bare 32-character id: how the backend points at another receipt. */
const DOCUMENT_REFERENCE = /(\b(?:document\s+)?[0-9a-f]{32}\b)/gi;

export type MessagePart = { text: string } | { documentId: string };

/** Split a finding's message into plain text and references to other receipts (by id). */
export function splitDocumentReferences(message: string): MessagePart[] {
  return message
    .split(DOCUMENT_REFERENCE)
    .filter((part) => part !== "")
    .map((part) =>
      /^(?:document\s+)?[0-9a-f]{32}$/i.test(part)
        ? { documentId: part.slice(-32) }
        : { text: part },
    );
}

/** The message without ids people cannot read: a reference to another receipt says so in words. */
export function plainFindingMessage(message: string): string {
  return splitDocumentReferences(message)
    .map((part) => ("text" in part ? part.text : "another receipt"))
    .join("");
}

/**
 * What the two numbers of a finding are called. "Expected / actual" suits a printed total against
 * its arithmetic, but not a limit against a spend, or the alcohol to take out of a bill.
 */
const COMPARISON_LABELS: Record<string, { expected: string; actual: string }> = {
  total_mismatch: { expected: "Worked out from the items", actual: "Printed total" },
  items_subtotal_mismatch: { expected: "Items add up to", actual: "Printed subtotal" },
  hotel_over_cap: { expected: "Nightly cap", actual: "Room per night" },
  meals_over_limit: { expected: "Daily limit", actual: "Meals that day" },
  entertainment_over_cap: { expected: "Cap per head", actual: "Per head" },
  alcohol_not_reimbursable: { expected: "Alcohol to take out", actual: "Bill total" },
  trip_conveyance_over_limit: { expected: "Daily limit", actual: "Spent that day" },
  mobile_over_cap: { expected: "Monthly cap", actual: "This bill" },
};

export function comparisonLabels(finding: Finding): { expected: string; actual: string } {
  return COMPARISON_LABELS[finding.code] ?? { expected: "Expected", actual: "Actual" };
}

/**
 * Every finding about one document: the ones on the document itself plus the ones the claim
 * carries for it (tagged with `document_id`), without duplicates. The pipeline may attach a
 * policy finding to either place, and the card must show it either way.
 */
export function findingsForDocument(
  documentFindings: readonly Finding[] | null | undefined,
  claimFindings: readonly Finding[] | null | undefined,
  documentId: string,
): Finding[] {
  const seen = new Set<string>();
  const merged: Finding[] = [];
  const candidates = [
    ...(documentFindings ?? []),
    ...(claimFindings ?? []).filter((f) => f.document_id === documentId),
  ];
  for (const finding of candidates) {
    const key = `${finding.code}|${finding.message}`;
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(finding);
  }
  return merged;
}

export function unansweredQuestions(claim: ClaimView): OpenQuestion[] {
  return (claim.open_questions ?? []).filter((q) => !q.answer || q.answer.trim() === "");
}

export function answeredQuestions(claim: ClaimView): OpenQuestion[] {
  return (claim.open_questions ?? []).filter((q) => q.answer && q.answer.trim() !== "");
}

export type AnswerSource = "calendar" | "receipt" | "you";

/** Answers carry their origin as a prefix: "from calendar: ..." / "from receipt: ...". */
export function parseAnswer(answer: string): { text: string; source: AnswerSource } {
  const match = /^from (calendar|receipt):\s*/i.exec(answer);
  if (!match) return { text: answer, source: "you" };
  return {
    text: answer.slice(match[0].length),
    source: match[1].toLowerCase() === "calendar" ? "calendar" : "receipt",
  };
}

export interface ParsedPrompt {
  intro: string;
  items: string[];
  outro: string | null;
}

/**
 * The assistant's single combined message is plain text: an intro line, numbered questions
 * ("1. ..."), and sometimes a closing hint. Split it so it can be shown as a proper list; a
 * message without numbered lines comes back as just an intro.
 */
export function parsePrompt(prompt: string): ParsedPrompt {
  const lines = prompt
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const items: string[] = [];
  const intro: string[] = [];
  const outro: string[] = [];
  for (const line of lines) {
    const numbered = /^\d+[.)]\s+(.*)$/.exec(line);
    if (numbered) items.push(numbered[1]);
    else if (items.length === 0) intro.push(line);
    else outro.push(line);
  }
  return {
    intro: intro.join(" "),
    items,
    outro: outro.length ? outro.join(" ") : null,
  };
}

const MONEY_FIELDS = new Set([
  "total",
  "subtotal",
  "line_items",
  "service_charge",
  "discount",
  "taxes",
  "cgst",
  "sgst",
  "igst",
  "cess",
]);

/** Render a finding's `expected` / `actual` (number | string | null) for people. */
export function formatFindingValue(
  finding: Finding,
  value: number | string | null | undefined,
): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  if (/rate|percent/.test(finding.code)) return `${Number(value.toFixed(2))}%`;
  const isMoney =
    (finding.fields ?? []).some((field) => MONEY_FIELDS.has(field)) ||
    /cap|limit|total|amount|mismatch|preapproval/.test(finding.code);
  return isMoney ? formatINR(value) : String(value);
}

/** Does this finding carry the evidence people need to judge it (expected vs actual)? */
export function hasComparison(finding: Finding): boolean {
  return (
    finding.expected !== null &&
    finding.expected !== undefined &&
    finding.actual !== null &&
    finding.actual !== undefined
  );
}

/** The receipt-field names to look for when highlighting where a finding comes from. */
export function findingFields(finding: Finding): string[] {
  return finding.fields ?? [];
}
