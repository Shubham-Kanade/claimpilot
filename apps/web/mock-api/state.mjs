// @ts-check
/**
 * The in-memory world: the seed ("history" that makes the app look alive), the read models the API
 * returns (DocumentView, BatchView, the statistics) and the id counters. `reset` rebuilds all of it
 * with `createState`, so ids and references are deterministic again.
 *
 * Seed (documents `doc-seed-NN`, batches `bat-seed-NN`; the live counters start from 1):
 *
 *   01  P005 Advik Kaur (L4)     Hyderabad trip 7 Aug 2026         submitted  FIN-2026-000001  auto_approve
 *   02  DEMO-MEERA Meera (L2)    Chandigarh trip 15-18 Aug 2026    submitted  FIN-2026-000002  finance_review
 *   03  P001 Advika Hayer (L4)   Learning 18 Jul 2026              submitted  FIN-2026-000003  finance_review
 *   04  P001 Advika Hayer (L4)   Fuel Jul 2026                     approved   FIN-2026-000004  auto_approve
 *   05  DEMO-MEERA Meera (L2)    WFH supplies Aug 2026             rejected   FIN-2026-000005  finance_review
 *   06  DEMO-ASHA Asha (L3)      Client dinner 6 Oct 2026          ready (answered from the calendar)
 */

import { answerQuestion, buildClaims, decideClaim, submitClaim } from "./claims.mjs";
import { CALENDAR, EMPLOYEES, HISTORY } from "./data.mjs";
import * as ev from "./events.mjs";
import { round2 } from "./format.mjs";
import { checksumChar } from "./gstin.mjs";
import { routeFor } from "./policy.mjs";
import { planForSample, sampleByKey } from "./samples.mjs";
import { scoreFindings, sortBySeverity, verdictFor } from "./trust.mjs";

/** @typedef {import("./types.mjs").BatchRecord} BatchRecord */
/** @typedef {import("./types.mjs").Claim} Claim */
/** @typedef {import("./types.mjs").ClaimRecord} ClaimRecord */
/** @typedef {import("./types.mjs").DocRecord} DocRecord */
/** @typedef {import("./types.mjs").Employee} Employee */
/** @typedef {import("./types.mjs").Finding} Finding */
/** @typedef {import("./types.mjs").GroupHint} GroupHint */
/** @typedef {import("./types.mjs").PipelineEvent} PipelineEvent */
/** @typedef {import("./types.mjs").ProcessedDocument} ProcessedDocument */
/** @typedef {import("./types.mjs").Schemas} Schemas */
/** @typedef {import("./types.mjs").State} State */
/** @typedef {import("./samples.mjs").SampleLibrary} SampleLibrary */

/** The day the seed is "from"; it only matters for undated documents, which the seed has none of. */
const SEED_TODAY = "2026-10-07";

// --- ids ------------------------------------------------------------------------------------------

/**
 * @param {number} n
 */
export function pad6(n) {
  return String(n).padStart(6, "0");
}

/**
 * @param {string} id
 * @returns {Employee | undefined}
 */
export function employeeById(id) {
  return EMPLOYEES.find((e) => e.id === id);
}

/**
 * @param {string} ownerId
 */
export function calendarOf(ownerId) {
  return CALENDAR.filter((e) => e.owner === ownerId);
}

/**
 * @param {State} state
 */
export function nextBatchId(state) {
  state.counters.batch += 1;
  return `bat-${pad6(state.counters.batch)}`;
}

/**
 * @param {State} state
 */
export function nextDocId(state) {
  state.counters.doc += 1;
  return `doc-${pad6(state.counters.doc)}`;
}

/**
 * The next finance reference, `FIN-2026-000006` after the seed.
 * @param {State} state
 */
export function nextReference(state) {
  state.counters.fin += 1;
  return `FIN-2026-${pad6(state.counters.fin)}`;
}

// --- documents ------------------------------------------------------------------------------------

/**
 * The document as the API shows it once it has been checked.
 * @param {DocRecord} doc
 * @param {Finding[]} findings  Its trust findings (duplicates included).
 * @returns {ProcessedDocument}
 */
export function makeProcessed(doc, findings) {
  return {
    id: doc.id,
    filename: doc.filename,
    sha256: doc.sha256,
    receipt: doc.plan.receipt,
    decisions: doc.plan.decisions,
    findings,
    boxes: doc.plan.boxes,
  };
}

/**
 * Mark a document as checked: trust score, verdict, and the ProcessedDocument.
 * @param {DocRecord} doc
 * @param {Finding[]} trustFindings
 */
export function markChecked(doc, trustFindings) {
  const findings = sortBySeverity(trustFindings);
  const score = scoreFindings(findings);
  doc.processed = makeProcessed(doc, findings);
  doc.trust = { score, verdict: verdictFor(findings, score) };
  doc.status = "processed";
  return findings;
}

/**
 * @param {DocRecord} doc
 * @returns {Schemas["DocumentView"]}
 */
export function documentView(doc) {
  return {
    id: doc.id,
    filename: doc.filename,
    position: doc.position,
    status: doc.status,
    error: doc.error,
    document: doc.processed,
    trust_score: doc.trust ? doc.trust.score : null,
    verdict: doc.trust ? doc.trust.verdict : null,
  };
}

/**
 * @param {State} state
 * @param {BatchRecord} batch
 * @returns {Schemas["BatchView"]}
 */
export function batchView(state, batch) {
  return {
    id: batch.id,
    employee_id: batch.employeeId,
    status: batch.status,
    total: batch.total,
    processed: batch.processed,
    failed: batch.failed,
    created_at: batch.createdAt,
    finished_at: batch.finishedAt,
    error: batch.error,
    documents: batch.docIds.flatMap((id) => {
      const doc = state.docs.get(id);
      return doc ? [documentView(doc)] : [];
    }),
    claims: batch.claimIds.flatMap((id) => {
      const record = state.claims.get(id);
      return record ? [record.view] : [];
    }),
  };
}

// --- claims -----------------------------------------------------------------------------------------

/**
 * Claims, newest batch first (within a batch in grouping order), optionally filtered.
 * @param {State} state
 * @param {{ employeeId?: string | null; status?: string | null; route?: string | null }} [filter]
 * @returns {Claim[]}
 */
export function listClaims(state, filter = {}) {
  return [...state.claims.values()]
    .filter(
      (r) =>
        (!filter.employeeId || r.view.employee_id === filter.employeeId) &&
        (!filter.status || r.view.status === filter.status) &&
        (!filter.route || r.view.route === filter.route),
    )
    .sort((a, b) => b.order - a.order || a.position - b.position)
    .map((r) => r.view);
}

/**
 * Replace a claim's view (the records are never mutated in place).
 * @param {State} state
 * @param {Claim} view
 */
export function saveClaim(state, view) {
  const record = state.claims.get(view.id);
  if (record) state.claims.set(view.id, { ...record, view });
}

// --- statistics ----------------------------------------------------------------------------------------

/**
 * The impact meter: the seeded history plus everything that happened in this run.
 * @param {State} state
 * @returns {Schemas["Stats"]}
 */
export function collectStats(state) {
  const docs = [...state.docs.values()];
  const processed =
    HISTORY.documentsProcessed + docs.filter((d) => d.status === "processed").length;
  const failed = HISTORY.documentsFailed + docs.filter((d) => d.status === "failed").length;
  const claims = [...state.claims.values()].map((r) => r.view);
  /** @type {Record<string, number>} */
  const byStatus = { ...HISTORY.claimsByStatus };
  for (const claim of claims) byStatus[claim.status] = (byStatus[claim.status] ?? 0) + 1;
  const cost = round6(HISTORY.llmCostUsd + docs.reduce((sum, d) => sum + d.costUsd, 0));
  const runs = state.batchSeconds;
  const seconds =
    (HISTORY.avgBatchSeconds * HISTORY.batches + runs.reduce((s, x) => s + x, 0)) /
    (HISTORY.batches + runs.length);
  const minutes = HISTORY.assumedManualMinutesPerDocument;
  return {
    documents_processed: processed,
    documents_failed: failed,
    claims: HISTORY.claims + claims.length,
    claims_by_status: byStatus,
    auto_approvable_claims:
      HISTORY.autoApprovableClaims + claims.filter((c) => c.route === "auto_approve").length,
    llm_calls: processed,
    llm_cost_usd: cost,
    llm_cost_per_document_usd: processed ? round6(cost / processed) : null,
    avg_batch_seconds: round2(seconds),
    assumed_manual_minutes_per_document: minutes,
    estimated_minutes_saved: processed * minutes,
  };
}

/**
 * @param {number} value
 */
function round6(value) {
  return Math.round(value * 1e6) / 1e6;
}

// --- the seed ---------------------------------------------------------------------------------------------

/**
 * @typedef {Object} Seed
 * @property {string} owner
 * @property {string} sample
 * @property {string} created  ISO timestamp the batch was uploaded.
 * @property {number} seconds  How long the batch took.
 * @property {"submitted" | "approved" | "rejected" | "ready"} end
 * @property {Record<string, string>} [answers]  Question kind -> answer given by the employee.
 * @property {GroupHint} [hint]
 * @property {Partial<ProcessedDocument["receipt"]>} [receipt]
 */

/** A valid-looking (checksum-correct) fictional Maharashtra GSTIN for the seeded Pune dinner. */
const PUNE_GSTIN = `27SAFTR4821K1Z${checksumChar("27SAFTR4821K1Z")}`;

/** @type {Seed[]} In upload (and finance reference) order. */
const SEEDS = [
  {
    owner: "P005",
    sample: "flight",
    created: "2026-10-01T09:41:12.000Z",
    seconds: 8.4,
    end: "submitted",
    answers: { business_purpose: "Customer workshop at the Hyderabad office" },
  },
  {
    owner: "DEMO-MEERA",
    sample: "hotel",
    created: "2026-10-02T14:20:31.000Z",
    seconds: 9.1,
    end: "submitted",
    answers: { business_purpose: "Vendor audit at the Chandigarh facility" },
  },
  {
    owner: "P001",
    sample: "learning",
    created: "2026-10-03T10:05:48.000Z",
    seconds: 9.8,
    end: "submitted",
  },
  {
    owner: "P001",
    sample: "fuel",
    created: "2026-10-05T08:55:20.000Z",
    seconds: 7.9,
    end: "approved",
  },
  {
    owner: "DEMO-MEERA",
    sample: "wfh",
    created: "2026-10-06T16:12:09.000Z",
    seconds: 10.6,
    end: "rejected",
  },
  {
    // The restaurant bill of the sample set stands in for a dinner whose calendar entry answers
    // both questions, so nothing is asked of the employee ("ask only what's needed").
    owner: "DEMO-ASHA",
    sample: "restaurant",
    created: "2026-10-07T21:30:44.000Z",
    seconds: 9.2,
    end: "ready",
    hint: { mode: "event", title: "Client dinner 6 Oct 2026", city: "Pune" },
    receipt: {
      merchant_name: "Saffron Terrace",
      merchant_gstin: PUNE_GSTIN,
      merchant_city: "Pune",
      invoice_number: "ST/2610/0187",
      date: "2026-10-06",
      time: "21:40",
    },
  },
];

/**
 * A fresh world, as after startup or `POST /__mock/reset`.
 * @param {SampleLibrary} samples
 * @returns {State}
 */
export function createState(samples) {
  /** @type {State} */
  const state = {
    counters: { batch: 0, doc: 0, fin: 0 },
    batchOrder: 0,
    batches: new Map(),
    docs: new Map(),
    claims: new Map(),
    seenHashes: new Set(),
    uploads: new Map(),
    idempotency: new Map(),
    batchSeconds: [],
  };

  SEEDS.forEach((seed, index) => {
    const n = String(index + 1).padStart(2, "0");
    const employee = employeeById(seed.owner);
    const sample = sampleByKey(seed.sample);
    const bytes = samples.bytes.get(seed.sample) ?? null;
    if (!employee) return;

    const plan = planForSample(sample, samples.sha.get(seed.sample) ?? `seed${n}`.padEnd(64, "0"));
    if (seed.receipt) plan.receipt = { ...plan.receipt, ...seed.receipt };
    /** @type {DocRecord} */
    const doc = {
      id: `doc-seed-${n}`,
      batchId: `bat-seed-${n}`,
      employeeId: employee.id,
      filename: sample.file,
      position: 0,
      status: "queued",
      error: null,
      bytes,
      sha256: samples.sha.get(seed.sample) ?? "0".repeat(64),
      plan,
      processed: null,
      trust: null,
      costUsd: plan.costUsd,
    };
    markChecked(doc, plan.trustFindings);

    const [grouped] = buildClaims(
      employee,
      [{ doc: /** @type {ProcessedDocument} */ (doc.processed), hint: seed.hint ?? sample.hint }],
      { today: SEED_TODAY, calendar: calendarOf(employee.id) },
    );
    if (!grouped) return;
    let claim = grouped;
    for (const [kind, answer] of Object.entries(seed.answers ?? {})) {
      const open = claim.open_questions.find((q) => q.kind === kind);
      if (open) claim = answerQuestion(claim, open.id, answer);
    }
    claim = { ...claim, batch_id: doc.batchId, route: routeFor(claim) };
    if (seed.end !== "ready") {
      claim = submitClaim(claim, nextReference(state));
      if (seed.end !== "submitted") claim = decideClaim(claim, seed.end === "approved");
    }

    const created = new Date(seed.created);
    const finished = new Date(created.getTime() + seed.seconds * 1000);
    /** @type {PipelineEvent[]} */
    const events = [
      ev.batchStarted(doc.batchId, 1),
      ev.documentExtracted(doc.batchId, doc, plan, false, doc.costUsd),
      ev.documentChecked(
        doc.batchId,
        doc.id,
        doc.trust?.score ?? 0,
        doc.trust?.verdict ?? "clean",
        doc.processed?.findings?.length ?? 0,
      ),
      ev.claimsReady(doc.batchId, [claim.id]),
      ev.batchDone(doc.batchId, { processed: 1, failed: 0, claims: 1, cost: doc.costUsd }),
    ];
    state.batchOrder += 1;
    state.batches.set(doc.batchId, {
      id: doc.batchId,
      employeeId: employee.id,
      status: "done",
      total: 1,
      processed: 1,
      failed: 0,
      createdAt: created.toISOString(),
      finishedAt: finished.toISOString(),
      error: null,
      docIds: [doc.id],
      claimIds: [claim.id],
      events,
      listeners: new Set(),
      order: state.batchOrder,
    });
    state.docs.set(doc.id, doc);
    state.claims.set(claim.id, { view: claim, order: state.batchOrder, position: 0 });
  });
  return state;
}
