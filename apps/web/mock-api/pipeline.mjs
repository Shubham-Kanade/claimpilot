// @ts-check
/**
 * The simulated receipt pipeline: creating a batch, then playing its timeline server-side with
 * timers whether or not anyone listens (late SSE clients replay the events they missed).
 *
 * Timeline (every delay is divided by `ctx.speed`; the jitter comes from the document index, never
 * from Math.random, so a run always looks the same):
 *
 *   +250 ms          batch_started {total}
 *   then, per document in upload order, 500..1500 ms after the previous one:
 *                    document_extracted       (or document_failed, and nothing more for that file)
 *   +350 ms later    document_checked
 *   +400 ms          claims_ready {claim_ids}   (an empty list when every document failed)
 *   +150 ms          batch_done {processed, failed, claims, cost_usd}
 *
 * A file whose name contains "batch-fail" makes the whole batch fail instead (batch_failed 500 ms
 * after batch_started), so the web app's error path can be tested.
 *
 * Costs: a read costs about $0.0004 to $0.0009 (Claude Haiku 5.5), or nothing when the very same
 * bytes were already read earlier in this run (cached). A second upload of the same bytes by the
 * same persona is flagged `duplicate_exact` (per persona only), and joins the original's claim.
 */

import { buildClaims } from "./claims.mjs";
import { planForUpload } from "./generate.mjs";
import * as ev from "./events.mjs";
import { isoOfDate } from "./format.mjs";
import { sha256Hex } from "./samples.mjs";
import { calendarOf, employeeById, markChecked, nextBatchId, nextDocId } from "./state.mjs";
import { simulate } from "./timing.mjs";
import { duplicateExact } from "./trust.mjs";

/** @typedef {import("./types.mjs").BatchRecord} BatchRecord */
/** @typedef {import("./types.mjs").Context} Context */
/** @typedef {import("./types.mjs").DocRecord} DocRecord */
/** @typedef {import("./types.mjs").PipelineEvent} PipelineEvent */
/** @typedef {import("./types.mjs").Schemas} Schemas */

const BATCH_START_MS = 250;
const CHECK_AFTER_MS = 350;
const CLAIMS_AFTER_MS = 400;
const DONE_AFTER_MS = 150;
const FAIL_AFTER_MS = 500;
/** A file name that makes the whole batch fail (a hook for testing the error path). */
const BATCH_FAIL_NAME = /batch[-_ ]?fail/i;

/**
 * Spacing between two documents, 500..1500 ms, derived from the document's position.
 * @param {number} position
 */
export function gapMs(position) {
  return 500 + ((position * 337 + 211) % 1001);
}

/**
 * Append an event to the batch's list and tell everyone who is listening.
 * @param {BatchRecord} batch
 * @param {PipelineEvent} event
 */
export function publish(batch, event) {
  const index = batch.events.length;
  batch.events.push(event);
  for (const listener of [...batch.listeners]) listener(index, event);
}

/**
 * @typedef {Object} UploadedFile
 * @property {string} filename
 * @property {Buffer} bytes
 * @property {string} mediaType
 */

/**
 * Create a batch from validated uploads and start playing it.
 * @param {Context} ctx
 * @param {string} employeeId
 * @param {UploadedFile[]} files
 * @returns {Schemas["BatchCreated"]}
 */
export function createBatch(ctx, employeeId, files) {
  const { state } = ctx;
  const created = ctx.now();
  const today = isoOfDate(created);
  const batchId = nextBatchId(state);
  state.batchOrder += 1;

  /** @type {DocRecord[]} */
  const docs = files.map((file, position) => {
    const sha256 = sha256Hex(file.bytes);
    return {
      id: nextDocId(state),
      batchId,
      employeeId,
      filename: file.filename,
      position,
      status: "queued",
      error: null,
      bytes: file.bytes,
      sha256,
      plan: planForUpload(
        ctx.samples,
        { sha256, filename: file.filename, mediaType: file.mediaType, today },
        { strict: ctx.config.demoStrict },
      ),
      processed: null,
      trust: null,
      costUsd: 0,
    };
  });
  for (const doc of docs) state.docs.set(doc.id, doc);

  /** @type {BatchRecord} */
  const batch = {
    id: batchId,
    employeeId,
    status: "queued",
    total: docs.length,
    processed: 0,
    failed: 0,
    createdAt: created.toISOString(),
    finishedAt: null,
    error: null,
    docIds: docs.map((d) => d.id),
    claimIds: [],
    events: [],
    listeners: new Set(),
    order: state.batchOrder,
  };
  state.batches.set(batchId, batch);

  void play(ctx, batch).catch((error) => {
    if (ctx.abort.signal.aborted) return;
    batch.status = "failed";
    batch.error = String(error?.message ?? error).slice(0, 200);
    batch.finishedAt = ctx.now().toISOString();
    publish(batch, ev.batchFailed(batch.id, batch.error));
  });

  return {
    batch_id: batchId,
    status: "queued",
    documents: docs.map((d) => ({ id: d.id, filename: d.filename })),
    events_url: `/v1/batches/${batchId}/events`,
  };
}

/**
 * The persona's earlier upload of the same bytes, registering this one when there is none.
 * @param {Context} ctx
 * @param {DocRecord} doc
 * @returns {string | null}
 */
function earlierUpload(ctx, doc) {
  const perPersona = ctx.state.uploads.get(doc.employeeId) ?? new Map();
  ctx.state.uploads.set(doc.employeeId, perPersona);
  const earlier = perPersona.get(doc.sha256);
  if (earlier && earlier !== doc.id) return earlier;
  perPersona.set(doc.sha256, doc.id);
  return null;
}

/**
 * Play the whole timeline of one batch.
 * @param {Context} ctx
 * @param {BatchRecord} batch
 */
async function play(ctx, batch) {
  const { state } = ctx;
  const docs = batch.docIds.flatMap((id) => {
    const doc = state.docs.get(id);
    return doc ? [doc] : [];
  });

  /** @type {Array<{ at: number; run: () => void }>} */
  const timeline = [];
  let failures = 0;
  let cost = 0;

  timeline.push({
    at: BATCH_START_MS,
    run: () => {
      batch.status = "processing";
      publish(batch, ev.batchStarted(batch.id, batch.total));
    },
  });

  if (docs.some((d) => BATCH_FAIL_NAME.test(d.filename))) {
    timeline.push({
      at: BATCH_START_MS + FAIL_AFTER_MS,
      run: () => {
        const error =
          'RuntimeError: the mock was told to fail this batch (a file name says "batch-fail")';
        batch.status = "failed";
        batch.error = error;
        batch.finishedAt = ctx.now().toISOString();
        publish(batch, ev.batchFailed(batch.id, error));
      },
    });
    await runTimeline(ctx, batch, timeline);
    return;
  }

  let slot = BATCH_START_MS;
  let last = BATCH_START_MS;
  for (const doc of docs) {
    slot += gapMs(doc.position);
    const at = slot;
    last = at;
    timeline.push({
      at,
      run: () => {
        if (doc.plan.failure) {
          failures += 1;
          doc.status = "failed";
          doc.error = doc.plan.failure;
          publish(batch, ev.documentFailed(batch.id, doc, doc.plan.failure));
          return;
        }
        const cached = state.seenHashes.has(doc.sha256);
        state.seenHashes.add(doc.sha256);
        doc.costUsd = cached ? 0 : doc.plan.costUsd;
        cost += doc.costUsd;
        publish(batch, ev.documentExtracted(batch.id, doc, doc.plan, cached, doc.costUsd));
      },
    });
    if (doc.plan.failure) continue;
    last = at + CHECK_AFTER_MS;
    timeline.push({
      at: at + CHECK_AFTER_MS,
      run: () => {
        const earlier = earlierUpload(ctx, doc);
        const findings = markChecked(doc, [
          ...doc.plan.trustFindings,
          ...(earlier ? [duplicateExact(earlier)] : []),
        ]);
        publish(
          batch,
          ev.documentChecked(
            batch.id,
            doc.id,
            doc.trust?.score ?? 0,
            doc.trust?.verdict ?? "",
            findings.length,
          ),
        );
      },
    });
  }

  timeline.push({
    at: last + CLAIMS_AFTER_MS,
    run: () => {
      const employee = employeeById(batch.employeeId);
      const members = docs.flatMap((d) =>
        d.processed ? [{ doc: d.processed, hint: d.plan.hint }] : [],
      );
      const claims = employee
        ? buildClaims(employee, members, {
            today: isoOfDate(ctx.now()),
            calendar: calendarOf(employee.id),
            categoryQuestions: ctx.config.categoryQuestions,
          })
        : [];
      claims.forEach((claim, position) => {
        const view = { ...claim, batch_id: batch.id };
        state.claims.set(view.id, { view, order: batch.order, position });
      });
      batch.claimIds = claims.map((c) => c.id);
      publish(batch, ev.claimsReady(batch.id, batch.claimIds));
    },
  });
  timeline.push({
    at: last + CLAIMS_AFTER_MS + DONE_AFTER_MS,
    run: () => {
      const finished = ctx.now();
      batch.status = "done";
      batch.processed = docs.filter((d) => d.status === "processed").length;
      batch.failed = failures;
      batch.finishedAt = finished.toISOString();
      state.batchSeconds.push(
        ((finished.getTime() - Date.parse(batch.createdAt)) / 1000) * ctx.speed,
      );
      publish(
        batch,
        ev.batchDone(batch.id, {
          processed: batch.processed,
          failed: batch.failed,
          claims: batch.claimIds.length,
          cost,
        }),
      );
    },
  });

  await runTimeline(ctx, batch, timeline);
}

/**
 * Run the steps in order, waiting (in simulated time) between them; stops when the mock is reset
 * or the batch is deleted ("start over").
 * @param {Context} ctx
 * @param {BatchRecord} batch
 * @param {Array<{ at: number; run: () => void }>} timeline  Sorted by `at` (milliseconds).
 */
async function runTimeline(ctx, batch, timeline) {
  const state = ctx.state;
  let cursor = 0;
  for (const step of timeline) {
    if (step.at > cursor) {
      if (!(await simulate(ctx, step.at - cursor))) return;
      cursor = step.at;
    }
    if (state.batches.get(batch.id) !== batch) return;
    step.run();
  }
}
