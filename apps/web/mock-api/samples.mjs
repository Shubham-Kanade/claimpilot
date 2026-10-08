// @ts-check
/**
 * The ten KNOWN sample documents (apps/web/mock-api/fixtures) and what the simulated pipeline
 * reads from each of them. Uploads are recognised by the sha256 of their bytes, whatever the
 * file is called, so a renamed copy is still the same bill.
 *
 * Scenario summary (the contract test asserts all of it):
 *
 *   key          category (confidence, engine)         trust findings                 claim
 *   fuel         fuel_vehicle (0.97 jev)               exif_date_mismatch (info)      Fuel Jul 2026
 *   handwritten  misc (0.58 llm), personal 0.62        none (policy: 10.1 warn)       Miscellaneous Jul 2026
 *   cab          local_conveyance (0.95 jev)           none                           Bengaluru trip 12 Jul 2026
 *   restaurant   client_entertainment (0.78 llm)       none                           Client dinner 12 Jul 2026
 *   learning     learning (0.93 jev)                   none (policy: 9.1 warn)        Learning 18 Jul 2026
 *   upi          local_conveyance (0.91 jev)           none                           Local conveyance Jul 2026
 *   flight       travel_domestic (0.98 jev)            none                           Hyderabad trip 7 Aug 2026
 *   hotel        accommodation (0.99 jev)              items_subtotal_mismatch (high) Chandigarh trip 15-18 Aug 2026
 *                                                      (policy: 4.1 by the acting persona's grade)
 *   mobile       mobile_internet (0.96 jev)            prompt_injection (high)        Mobile & internet Sep 2026
 *   wfh          wfh_supplies (0.88 jev)               total_mismatch (high),         WFH supplies Aug 2026
 *                                                      gst_rate_mismatch (warn)
 */

import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { boxesFor } from "./boxes.mjs";
import { EN_DASH } from "./format.mjs";
import { TRUTH_RECEIPTS } from "./receipts.mjs";
import { checkGst, exifDateMismatch, promptInjection, sortBySeverity } from "./trust.mjs";

/** @typedef {import("./types.mjs").Decisions} Decisions */
/** @typedef {import("./types.mjs").DocPlan} DocPlan */
/** @typedef {import("./types.mjs").ExpenseCategory} ExpenseCategory */
/** @typedef {import("./types.mjs").Finding} Finding */
/** @typedef {import("./types.mjs").GroupHint} GroupHint */
/** @typedef {import("./types.mjs").Receipt} Receipt */

/**
 * @typedef {Object} Sample
 * @property {string} key
 * @property {string} file
 * @property {Receipt} receipt
 * @property {Decisions} decisions
 * @property {Finding[]} trustFindings
 * @property {GroupHint} hint
 */

/**
 * @param {ExpenseCategory} category
 * @param {number} confidence
 * @param {number} alcohol
 * @param {number} personal
 * @param {string} [engine]
 * @returns {Decisions}
 */
function decisions(category, confidence, alcohol, personal, engine = "jev") {
  return {
    category,
    category_confidence: confidence,
    alcohol_present: alcohol,
    personal_expense: personal,
    engine,
  };
}

/**
 * What the trust layer finds on a sample: the arithmetic, GST and GSTIN checks the backend runs on
 * every receipt, then the findings only the file itself can reveal (a photo taken days after the
 * date on the bill, text addressed to an AI reviewer), most severe first.
 * @param {Receipt} receipt
 * @param {Finding[]} [fromTheFile]
 */
function trust(receipt, fromTheFile = []) {
  return sortBySeverity([...checkGst(receipt), ...fromTheFile]);
}

/** The handwritten bill: the extractor was unsure of the date and the items. */
const HANDWRITTEN = {
  ...TRUTH_RECEIPTS.handwritten,
  low_confidence_fields: ["date", "line_items"],
};
/** The restaurant bill: the extractor was unsure of the items (Hindi and English mixed). */
const RESTAURANT = { ...TRUTH_RECEIPTS.restaurant, low_confidence_fields: ["line_items"] };

/** @type {Sample[]} In the order of the demo upload. */
export const SAMPLES = [
  {
    key: "fuel",
    file: "fuel-slip-agni-petroleum.png",
    receipt: TRUTH_RECEIPTS.fuel,
    decisions: decisions("fuel_vehicle", 0.97, 0.01, 0.03),
    trustFindings: trust(TRUTH_RECEIPTS.fuel, [exifDateMismatch("2026-07-06", "2026-07-04")]),
    hint: { mode: "period", title: "Fuel Jul 2026", city: "Hyderabad" },
  },
  {
    key: "handwritten",
    file: "handwritten-bill-gupta-provision.png",
    receipt: HANDWRITTEN,
    decisions: decisions("misc", 0.58, 0.01, 0.62, "llm"),
    trustFindings: trust(HANDWRITTEN),
    hint: { mode: "period", title: "Miscellaneous Jul 2026", city: "Hyderabad" },
  },
  {
    key: "cab",
    file: "cab-receipt-raahi-cabs.png",
    receipt: TRUTH_RECEIPTS.cab,
    decisions: decisions("local_conveyance", 0.95, 0.01, 0.04),
    trustFindings: trust(TRUTH_RECEIPTS.cab),
    hint: { mode: "trip", title: "Bengaluru trip 12 Jul 2026", city: "Bengaluru" },
  },
  {
    key: "restaurant",
    file: "restaurant-bill-mehfil-cafe.png",
    // Classified as a client dinner (not plain meals): System One was unsure, the LLM decided.
    receipt: RESTAURANT,
    decisions: decisions("client_entertainment", 0.78, 0.04, 0.05, "llm"),
    trustFindings: trust(RESTAURANT),
    hint: { mode: "event", title: "Client dinner 12 Jul 2026", city: "Bengaluru" },
  },
  {
    key: "learning",
    file: "invoice-learnsphere-course.pdf",
    receipt: TRUTH_RECEIPTS.learning,
    decisions: decisions("learning", 0.93, 0.01, 0.02),
    trustFindings: trust(TRUTH_RECEIPTS.learning),
    hint: { mode: "event", title: "Learning 18 Jul 2026", city: null },
  },
  {
    key: "upi",
    file: "upi-payment-auto-fare.png",
    receipt: TRUTH_RECEIPTS.upi,
    decisions: decisions("local_conveyance", 0.91, 0.01, 0.06),
    trustFindings: trust(TRUTH_RECEIPTS.upi),
    hint: { mode: "period", title: "Local conveyance Jul 2026", city: null },
  },
  {
    key: "flight",
    file: "flight-ticket-suryoday-air.png",
    receipt: TRUTH_RECEIPTS.flight,
    decisions: decisions("travel_domestic", 0.98, 0.01, 0.02),
    trustFindings: trust(TRUTH_RECEIPTS.flight),
    hint: { mode: "trip", title: "Hyderabad trip 7 Aug 2026", city: "Hyderabad" },
  },
  {
    key: "hotel",
    file: "hotel-folio-lotus-bay.png",
    // The folio is tampered: its room lines add up to 22,100 but the printed subtotal is 21,600.
    receipt: TRUTH_RECEIPTS.hotel,
    decisions: decisions("accommodation", 0.99, 0.01, 0.02),
    trustFindings: trust(TRUTH_RECEIPTS.hotel),
    hint: { mode: "trip", title: `Chandigarh trip 15${EN_DASH}18 Aug 2026`, city: "Chandigarh" },
  },
  {
    key: "mobile",
    file: "mobile-bill-nakshatra.pdf",
    receipt: TRUTH_RECEIPTS.mobile,
    decisions: decisions("mobile_internet", 0.96, 0.01, 0.03),
    // The extractor flagged text addressed to an AI reviewer; no fixed rule matched a field.
    trustFindings: trust(TRUTH_RECEIPTS.mobile, [promptInjection([], "flagged by the extractor")]),
    hint: { mode: "period", title: "Mobile & internet Sep 2026", city: "Mumbai" },
  },
  {
    key: "wfh",
    file: "invoice-prakash-computer-world.pdf",
    // Tampered GST: CGST and SGST are 1,700.28 each but the total adds only one of them.
    receipt: TRUTH_RECEIPTS.wfh,
    decisions: decisions("wfh_supplies", 0.88, 0.01, 0.07),
    trustFindings: trust(TRUTH_RECEIPTS.wfh),
    hint: { mode: "period", title: "WFH supplies Aug 2026", city: "Panaji" },
  },
];

/** @type {Map<string, Sample>} */
const BY_KEY = new Map(SAMPLES.map((s) => [s.key, s]));

/**
 * @param {string} key
 * @returns {Sample}
 */
export function sampleByKey(key) {
  const sample = BY_KEY.get(key);
  if (!sample) throw new Error(`unknown sample ${key}`);
  return sample;
}

// --- loading -------------------------------------------------------------------------------------

/**
 * @param {Buffer | Uint8Array} bytes
 */
export function sha256Hex(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

/**
 * What reading one file costs: about $0.0004 to $0.0009 (a Claude Haiku 5.5 read), derived from the
 * bytes so the same file always costs the same.
 * @param {string} sha256
 */
export function costFromSha(sha256) {
  const unit = Number.parseInt(sha256.slice(0, 8), 16) / 0xffffffff;
  return Math.round((0.0004 + unit * 0.0005) * 1e6) / 1e6;
}

/**
 * @typedef {Object} SampleLibrary
 * @property {string} dir
 * @property {Map<string, Sample>} bySha  sha256 of the file -> its scenario.
 * @property {Map<string, Buffer>} bytes  sample key -> the file.
 * @property {Map<string, string>} sha  sample key -> sha256.
 * @property {string[]} missing  Sample files that were not found in `dir`.
 */

/**
 * Reads every sample file and remembers its sha256.
 * @param {string} dir
 * @returns {SampleLibrary}
 */
export function loadSamples(dir) {
  /** @type {SampleLibrary} */
  const library = { dir, bySha: new Map(), bytes: new Map(), sha: new Map(), missing: [] };
  for (const sample of SAMPLES) {
    try {
      const bytes = fs.readFileSync(path.join(dir, sample.file));
      const digest = sha256Hex(bytes);
      library.bySha.set(digest, sample);
      library.bytes.set(sample.key, bytes);
      library.sha.set(sample.key, digest);
    } catch {
      library.missing.push(sample.file);
    }
  }
  return library;
}

/**
 * The plan for a recognised sample file.
 * @param {Sample} sample
 * @param {string} sha256
 * @returns {DocPlan}
 */
export function planForSample(sample, sha256) {
  return {
    sample: sample.key,
    receipt: sample.receipt,
    decisions: sample.decisions,
    trustFindings: sample.trustFindings,
    boxes: boxesFor(sample.key),
    hint: sample.hint,
    costUsd: costFromSha(sha256),
    failure: null,
  };
}
