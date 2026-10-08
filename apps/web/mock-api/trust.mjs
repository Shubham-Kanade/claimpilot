// @ts-check
/**
 * The trust layer, as far as the mock needs it: how findings turn into a score and a verdict
 * (trust/assess.py) and the findings themselves, worded exactly like the backend's
 * (trust/gst.py, trust/duplicates.py, trust/forensics.py, trust/injection.py).
 */

import { validateGstin } from "./gstin.mjs";
import { dayLabelPadded, daysBetween, formatG, money, round2, sumOf } from "./format.mjs";

/** @typedef {import("./types.mjs").Finding} Finding */
/** @typedef {import("./types.mjs").Receipt} Receipt */
/** @typedef {import("./types.mjs").Severity} Severity */

/** Points lost per finding of each severity. */
export const PENALTY = /** @type {const} */ ({ high: 40, warn: 15, info: 3 });
/** Findings that are conclusive on their own: the verdict is `block` (trust/assess.py). */
export const BLOCKING_CODES = new Set([
  "prompt_injection",
  "ai_generated_c2pa",
  "ai_generated_metadata",
  "total_mismatch",
  "items_subtotal_mismatch",
]);
const BLOCK_BELOW = 30;
const REVIEW_BELOW = 80;
const TOTAL_TOLERANCE = 1.0;
const LINE_TOLERANCE = 0.05;
const RATE_TOLERANCE_PP = 0.6;
const STANDARD_GST_RATES = [0, 0.25, 3, 5, 12, 18, 28, 40];
const HARD_TO_READ =
  " The image was hard to read at this point, so please compare it with the original.";

/**
 * A finding with every field present, in the order the backend serialises them.
 * @param {{
 *   code: string;
 *   severity: Severity;
 *   message: string;
 *   fields?: string[];
 *   expected?: number | string | null;
 *   actual?: number | string | null;
 *   source?: Finding["source"];
 *   clause_id?: string | null;
 *   clause_text?: string | null;
 *   document_id?: string | null;
 * }} f
 * @returns {Finding}
 */
export function makeFinding(f) {
  return {
    code: f.code,
    severity: f.severity,
    message: f.message,
    fields: f.fields ?? [],
    expected: f.expected ?? null,
    actual: f.actual ?? null,
    source: f.source ?? "trust",
    clause_id: f.clause_id ?? null,
    clause_text: f.clause_text ?? null,
    document_id: f.document_id ?? null,
  };
}

// --- score and verdict ---------------------------------------------------------------------------

/**
 * 100 minus 40 per high, 15 per warn and 3 per info finding; never below 0.
 * @param {Finding[]} findings
 */
export function scoreFindings(findings) {
  const penalty = findings.reduce((sum, f) => sum + (PENALTY[f.severity] ?? 0), 0);
  return Math.max(0, 100 - penalty);
}

/**
 * `block` for a conclusive (high) finding or a score under 30, `review` under 80 or with any high
 * finding, else `clean`.
 * @param {Finding[]} findings
 * @param {number} score
 * @returns {"clean" | "review" | "block"}
 */
export function verdictFor(findings, score) {
  const conclusive = findings.some((f) => BLOCKING_CODES.has(f.code) && f.severity === "high");
  if (score < BLOCK_BELOW || conclusive) return "block";
  if (score < REVIEW_BELOW || findings.some((f) => f.severity === "high")) return "review";
  return "clean";
}

/** @type {Record<Severity, number>} */
const SEVERITY_ORDER = { high: 0, warn: 1, info: 2 };

/**
 * High first, then warn, then info; otherwise keeping the order the checks produced them in.
 * @param {Finding[]} findings
 */
export function sortBySeverity(findings) {
  return [...findings].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
}

// --- arithmetic and GST (trust/gst.py) -----------------------------------------------------------

/**
 * @param {Receipt} r
 * @returns {number | null}
 */
function expectedTotal(r) {
  let base = r.subtotal ?? null;
  if (base == null && r.line_items.length) base = r.line_items.reduce((s, i) => s + i.amount, 0);
  if (base == null) return null;
  const t = r.taxes;
  const taxes = sumOf([t.cgst, t.sgst, t.igst, t.cess]);
  return round2(base + taxes + (r.service_charge ?? 0) - (r.discount ?? 0));
}

/**
 * Line items against the printed subtotal: more in the rows than the subtotal admits means a
 * figure was changed (`items_subtotal_mismatch`, high); fewer means a row may be missing
 * (`items_incomplete`, warn).
 * @param {Receipt} r
 * @returns {Finding | null}
 */
export function itemsSubtotalMismatch(r) {
  if (r.subtotal == null || !r.line_items.length) return null;
  const items = round2(r.line_items.reduce((s, i) => s + i.amount, 0));
  if (Math.abs(items - r.subtotal) <= LINE_TOLERANCE) return null;
  if (items < r.subtotal) {
    return makeFinding({
      code: "items_incomplete",
      severity: "warn",
      message:
        `The items read add up to ${money(items)}, ${money(r.subtotal - items)} less than the ` +
        `subtotal of ${money(r.subtotal)}; a line may be missing.`,
      fields: ["line_items", "subtotal"],
      expected: r.subtotal,
      actual: items,
    });
  }
  return makeFinding({
    code: "items_subtotal_mismatch",
    severity: "high",
    message: `Line items add up to ${money(items)} but the subtotal says ${money(r.subtotal)}.`,
    fields: ["line_items", "subtotal"],
    expected: items,
    actual: r.subtotal,
  });
}

/**
 * `total_mismatch` (high) when the printed total is not the bill's own items plus taxes and charges.
 * @param {Receipt} r
 * @returns {Finding | null}
 */
export function totalMismatch(r) {
  const expected = expectedTotal(r);
  if (expected == null || r.total == null || Math.abs(expected - r.total) <= TOTAL_TOLERANCE) {
    return null;
  }
  return makeFinding({
    code: "total_mismatch",
    severity: "high",
    message:
      `The printed total ${money(r.total)} doesn't match the bill's own ` +
      `items, taxes and charges (${money(expected)}).`,
    fields: ["total"],
    expected,
    actual: r.total,
  });
}

/**
 * Both halves of CGST/SGST, never together with IGST.
 * @param {Receipt} r
 * @returns {Finding[]}
 */
function taxStructure(r) {
  const t = r.taxes;
  /** @type {Finding[]} */
  const findings = [];
  if ((t.cgst || t.sgst) && t.igst) {
    findings.push(
      makeFinding({
        code: "gst_mixed_intra_inter",
        severity: "high",
        message:
          "The bill charges both CGST/SGST (same-state) and IGST (inter-state), " +
          "which a genuine GST invoice never does.",
        fields: ["taxes"],
      }),
    );
  }
  if (t.cgst != null && t.sgst != null && Math.abs(t.cgst - t.sgst) > LINE_TOLERANCE) {
    findings.push(
      makeFinding({
        code: "cgst_sgst_unequal",
        severity: "warn",
        message: `CGST (${money(t.cgst)}) and SGST (${money(t.sgst)}) should be equal.`,
        fields: ["taxes"],
        expected: t.cgst,
        actual: t.sgst,
      }),
    );
  }
  if (Boolean(t.cgst) !== Boolean(t.sgst) && !t.igst) {
    findings.push(
      makeFinding({
        code: "gst_half_missing",
        severity: "warn",
        message: "Only one of CGST/SGST is printed; same-state bills charge both halves.",
        fields: ["taxes"],
      }),
    );
  }
  return findings;
}

/**
 * Amounts GST can be charged on when the whole bill is taxed at one rate.
 * @param {Receipt} r
 * @returns {number[]}
 */
function aggregateBases(r) {
  if (!r.subtotal) return [];
  const discount = r.discount ?? 0;
  const service = r.service_charge ?? 0;
  const bases = [
    r.subtotal,
    r.subtotal - discount,
    r.subtotal + service,
    r.subtotal - discount + service,
  ];
  return [...new Set(bases.map(round2).filter((b) => b > 0))].sort((a, b) => a - b);
}

/**
 * Everything the printed tax may have been charged on: the whole bill, or any single row (a
 * flight ticket taxes the base fare but not the fees).
 * @param {Receipt} r
 * @returns {number[]}
 */
function taxBases(r) {
  return [...aggregateBases(r), ...r.line_items.map((i) => i.amount).filter((a) => a > 0)];
}

/**
 * `gst_rate_mismatch` (warn) when the tax charged is not the stated rate of ANY plausible base.
 * @param {Receipt} r
 * @returns {Finding | null}
 */
export function gstRateMismatch(r) {
  const t = r.taxes;
  const tax = sumOf([t.cgst, t.sgst, t.igst]);
  if (!tax || !r.subtotal) return null;
  if (t.gst_rate_percent == null) return null;
  const stated = t.gst_rate_percent;
  if (!taxBases(r).every((base) => Math.abs((100 * tax) / base - stated) > RATE_TOLERANCE_PP)) {
    return null;
  }
  const effective = (100 * tax) / r.subtotal;
  return makeFinding({
    code: "gst_rate_mismatch",
    severity: "warn",
    message:
      `Tax charged is ${effective.toFixed(1)}% of the subtotal, but the bill states ` +
      `${formatG(stated)}% GST.`,
    fields: ["taxes"],
    expected: stated,
    actual: round2(effective),
  });
}

/**
 * `gst_rate_nonstandard` (info) when no rate is printed and the tax is no standard rate of the bill.
 * @param {Receipt} r
 * @returns {Finding | null}
 */
function gstRateNonstandard(r) {
  const t = r.taxes;
  const tax = sumOf([t.cgst, t.sgst, t.igst]);
  if (!tax || !r.subtotal || t.gst_rate_percent != null) return null;
  const rates = aggregateBases(r).map((base) => (100 * tax) / base);
  const standard = rates.some((rate) =>
    STANDARD_GST_RATES.some((std) => Math.abs(rate - std) <= RATE_TOLERANCE_PP),
  );
  if (standard) return null;
  const effective = (100 * tax) / r.subtotal;
  return makeFinding({
    code: "gst_rate_nonstandard",
    severity: "info",
    message: `Tax works out to ${effective.toFixed(1)}%, which is not a standard GST rate.`,
    fields: ["taxes"],
    actual: round2(effective),
  });
}

/**
 * GSTIN printed and well-formed, or missing while GST is charged.
 * @param {Receipt} r
 * @returns {Finding[]}
 */
function gstinFindings(r) {
  if (!r.merchant_gstin) {
    const t = r.taxes;
    if (![t.cgst, t.sgst, t.igst].some(Boolean)) return [];
    return [
      makeFinding({
        code: "gstin_missing",
        severity: "warn",
        message: "GST is charged but no GSTIN is printed, so input credit can't be claimed.",
        fields: ["merchant_gstin"],
      }),
    ];
  }
  const { valid, reason } = validateGstin(r.merchant_gstin);
  if (valid) return [];
  /** @type {Record<string, string>} */
  const detail = {
    format: "is not in the 15-character GSTIN format",
    state_code: "starts with an unknown state code",
    checksum: "fails the GSTIN checksum (likely mistyped or invented)",
  };
  return [
    makeFinding({
      code: `gstin_invalid_${reason}`,
      severity: "high",
      message: `The GSTIN ${r.merchant_gstin} ${detail[reason ?? "format"]}.`,
      fields: ["merchant_gstin"],
      actual: r.merchant_gstin,
    }),
  ];
}

/**
 * A finding that rests on a figure the reader itself marked unsure asks for a check instead of
 * accusing: it drops from high to warn.
 * @param {Finding[]} findings
 * @param {string[]} unsure
 * @returns {Finding[]}
 */
function softenUnsure(findings, unsure) {
  if (!unsure.length) return findings;
  return findings.map((f) =>
    f.severity === "high" && f.fields.some((field) => unsure.includes(field))
      ? { ...f, severity: "warn", message: f.message + HARD_TO_READ }
      : f,
  );
}

/**
 * The arithmetic, GST and GSTIN checks the backend runs on every receipt (`check_gst`).
 * @param {Receipt} r
 * @returns {Finding[]}
 */
export function checkGst(r) {
  const findings = [
    itemsSubtotalMismatch(r),
    totalMismatch(r),
    ...taxStructure(r),
    gstRateMismatch(r),
    gstRateNonstandard(r),
    ...gstinFindings(r),
  ].filter((f) => f !== null);
  return softenUnsure(findings, r.low_confidence_fields);
}

// --- forensics, injection, duplicates -------------------------------------------------------------

/**
 * `exif_date_mismatch`: the photo was taken long after the date printed on the bill.
 * @param {string} captured  ISO date the photo was taken.
 * @param {string} printed  ISO date printed on the bill.
 * @param {Severity} [severity]
 * @returns {Finding}
 */
export function exifDateMismatch(captured, printed, severity = "info") {
  const days = daysBetween(printed, captured);
  return makeFinding({
    code: "exif_date_mismatch",
    severity,
    message:
      `The photo was taken on ${dayLabelPadded(captured)}, ${days} days after the date ` +
      `printed on the bill (${dayLabelPadded(printed)}).`,
    fields: ["date"],
    expected: printed,
    actual: captured,
  });
}

/**
 * `prompt_injection` (high, blocking): text on the document addresses an AI reviewer. The message
 * never quotes the attacker's text.
 * @param {string[]} fields
 * @param {string} actual
 * @returns {Finding}
 */
export function promptInjection(fields, actual) {
  return makeFinding({
    code: "prompt_injection",
    severity: "high",
    message:
      "The document contains text addressed to an AI reviewer or automated system " +
      "(for example a request to approve the claim or skip checks). It was treated as part " +
      "of the document and ignored, and the bill needs a human review.",
    fields,
    actual,
  });
}

/**
 * `duplicate_exact` (high): the same file was uploaded before.
 * @param {string} documentId  The earlier document.
 * @returns {Finding}
 */
export function duplicateExact(documentId) {
  return makeFinding({
    code: "duplicate_exact",
    severity: "high",
    message: `This exact file was already uploaded as document ${documentId}.`,
    actual: documentId,
  });
}

/**
 * `edited_software` (warn): an image whose metadata names an editing tool.
 * @param {string} editor
 * @returns {Finding}
 */
export function editedSoftware(editor) {
  return makeFinding({
    code: "edited_software",
    severity: "warn",
    message:
      `The image's metadata says it was processed with ${editor}, ` +
      "so it may have been edited after it was captured.",
    actual: editor,
  });
}

/**
 * `pdf_editor_producer` (warn): a PDF last written by an editing tool.
 * @param {string} editor
 * @returns {Finding}
 */
export function pdfEditorProducer(editor) {
  return makeFinding({
    code: "pdf_editor_producer",
    severity: "warn",
    message:
      `The PDF was last written by ${editor}, a tool for editing documents rather than ` +
      "the billing system that issued it.",
    actual: editor,
  });
}
