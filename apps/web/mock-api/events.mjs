// @ts-check
/**
 * Pipeline events (pipeline/events.py). Fields are in the backend's serialisation order
 * (`batch_id`, `type`, then the event's own fields).
 */

/** @typedef {import("./types.mjs").Schemas} Schemas */

/** The events after which a stream ends. */
export const TERMINAL_TYPES = new Set(["batch_done", "batch_failed"]);

/**
 * @param {string} batchId
 * @param {number} total
 * @returns {Schemas["BatchStarted"]}
 */
export function batchStarted(batchId, total) {
  return { batch_id: batchId, type: "batch_started", total };
}

/**
 * @param {string} batchId
 * @param {{ id: string; filename: string; position: number }} doc
 * @param {import("./types.mjs").DocPlan} plan
 * @param {boolean} cached
 * @param {number} cost
 * @returns {Schemas["DocumentExtracted"]}
 */
export function documentExtracted(batchId, doc, plan, cached, cost) {
  return {
    batch_id: batchId,
    type: "document_extracted",
    document_id: doc.id,
    filename: doc.filename,
    position: doc.position,
    doc_type: plan.receipt.doc_type,
    merchant: plan.receipt.merchant_name ?? null,
    total: plan.receipt.total ?? null,
    category: plan.decisions.category,
    category_confidence: plan.decisions.category_confidence,
    engine: plan.decisions.engine,
    cached,
    cost_usd: round6(cost),
  };
}

/**
 * @param {string} batchId
 * @param {string} documentId
 * @param {number} trustScore
 * @param {string} verdict
 * @param {number} findings
 * @returns {Schemas["DocumentChecked"]}
 */
export function documentChecked(batchId, documentId, trustScore, verdict, findings) {
  return {
    batch_id: batchId,
    type: "document_checked",
    document_id: documentId,
    trust_score: trustScore,
    verdict,
    findings,
  };
}

/**
 * @param {string} batchId
 * @param {{ id: string; filename: string }} doc
 * @param {string} error
 * @returns {Schemas["DocumentFailed"]}
 */
export function documentFailed(batchId, doc, error) {
  return {
    batch_id: batchId,
    type: "document_failed",
    document_id: doc.id,
    filename: doc.filename,
    error,
  };
}

/**
 * @param {string} batchId
 * @param {string[]} claimIds
 * @returns {Schemas["ClaimsReady"]}
 */
export function claimsReady(batchId, claimIds) {
  return { batch_id: batchId, type: "claims_ready", claim_ids: claimIds };
}

/**
 * @param {string} batchId
 * @param {{ processed: number; failed: number; claims: number; cost: number }} totals
 * @returns {Schemas["BatchDone"]}
 */
export function batchDone(batchId, totals) {
  return {
    batch_id: batchId,
    type: "batch_done",
    processed: totals.processed,
    failed: totals.failed,
    claims: totals.claims,
    cost_usd: round6(totals.cost),
  };
}

/**
 * @param {string} batchId
 * @param {string} error
 * @returns {Schemas["BatchFailed"]}
 */
export function batchFailed(batchId, error) {
  return { batch_id: batchId, type: "batch_failed", error };
}

/**
 * @param {number} value
 */
function round6(value) {
  return Math.round(value * 1e6) / 1e6;
}
