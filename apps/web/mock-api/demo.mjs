// @ts-check
/**
 * "Start over" of the public demo (api/demo.py, repo `delete_data`): an employee deletes ALL of
 * their own batches, documents and claims (the seeded ones included, so their claim list becomes
 * empty); the approver deletes everyone's. The stored files go with the documents, and the
 * statistics, which are derived from what is left, follow. `POST /__mock/reset` is the other way
 * to start over: it restores the whole seed, for tests.
 */

import * as ev from "./events.mjs";
import { publish } from "./pipeline.mjs";

/** @typedef {import("./types.mjs").Context} Context */
/** @typedef {import("./types.mjs").Schemas} Schemas */

/**
 * Delete one employee's data (everyone's when `employeeId` is null) and say how much it was.
 * @param {Context} ctx
 * @param {string | null} employeeId
 * @returns {Schemas["ResetResult"]}
 */
export function startOver(ctx, employeeId) {
  const { state } = ctx;
  const mine = (/** @type {string} */ owner) => employeeId === null || owner === employeeId;
  let batches = 0;
  let documents = 0;
  let claims = 0;

  for (const batch of [...state.batches.values()]) {
    if (!mine(batch.employeeId)) continue;
    if (batch.status === "queued" || batch.status === "processing") {
      // Still playing: end its streams with a terminal event; the timeline stops by itself.
      const error = "RuntimeError: the batch was deleted (start over)";
      batch.status = "failed";
      batch.error = error;
      batch.finishedAt = ctx.now().toISOString();
      publish(batch, ev.batchFailed(batch.id, error));
    }
    state.batches.delete(batch.id);
    batches += 1;
  }
  for (const doc of [...state.docs.values()]) {
    if (!mine(doc.employeeId)) continue;
    state.docs.delete(doc.id);
    documents += 1;
  }
  for (const [id, record] of [...state.claims]) {
    if (!mine(record.view.employee_id)) continue;
    state.claims.delete(id);
    claims += 1;
  }
  // The duplicate check forgets what was deleted; the extraction cache (`seenHashes`) does not.
  if (employeeId === null) state.uploads.clear();
  else state.uploads.delete(employeeId);
  return { batches, documents, claims };
}
