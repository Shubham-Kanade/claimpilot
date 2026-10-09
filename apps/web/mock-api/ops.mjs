// @ts-check
/**
 * GET /v1/ops/llm for the mock: the AI-calls report, derived from the mock's own documents (one
 * extraction call per document read, plus a category call when the document needed one) with a
 * fixed, plausible system-wide history on top, so the page has something to show before any
 * upload. Samples are "recorded" calls (recorded latency, would-be cost); every other file is a
 * "live" call. Failed documents become failures, traced by their batch id.
 * Failures and trace calls are only the caller's own; totals and routes are system-wide.
 */

/** @typedef {import("./types.mjs").Context} Context */

const round6 = (/** @type {number} */ n) => Math.round(n * 1e6) / 1e6;

/** System-wide history before this run: [route, model_key, calls, live calls, errors, p50, p95, live $, recorded $]. */
const HISTORY = [
  ["extraction", "haiku", 128, 21, 2, 2400, 5200, 0.0189, 0.0942],
  ["decision_fallback", "haiku", 96, 18, 0, 900, 1700, 0.0054, 0.0231],
  ["agent_chat", "sonnet", 31, 6, 1, 3100, 6400, 0.0412, 0.1318],
  ["reply_parse", "haiku", 44, 9, 0, 700, 1400, 0.0031, 0.0102],
  ["locate", "haiku", 128, 21, 0, 1100, 2300, 0.0067, 0.0394],
];

/**
 * @typedef {Object} Call
 * @property {string} at
 * @property {string} route
 * @property {string} model_key
 * @property {string} mode
 * @property {number} latency_ms
 * @property {number} cost_usd
 * @property {string | null} error
 * @property {string | null} document_id
 * @property {string | null} claim_id
 * @property {string} batch
 */

/**
 * @param {Context} ctx
 * @param {string} employeeId  The caller: failures and trace calls are theirs only.
 * @param {number} hours
 * @param {string | null} traceId
 */
export function collectLlmOps(ctx, employeeId, hours, traceId) {
  const now = ctx.now();
  const since = new Date(now.getTime() - hours * 3600_000);
  const oldest = new Date(now.getTime() - Math.min(hours, 36) * 3600_000).toISOString();

  /** @type {Call[]} */
  const calls = [];
  for (const doc of ctx.state.docs.values()) {
    const batch = ctx.state.batches.get(doc.batchId);
    const at = batch?.createdAt ?? now.toISOString();
    const recorded = doc.plan.sample !== null;
    const claimId =
      [...ctx.state.claims.values()].find((c) => c.view.document_ids.includes(doc.id))?.view.id ??
      null;
    const failed = doc.status === "failed";
    calls.push({
      at,
      route: "extraction",
      model_key: "haiku",
      mode: recorded ? "recorded" : "live",
      latency_ms: recorded ? 2100 + (doc.position % 5) * 150 : 2600,
      cost_usd: failed ? 0 : doc.plan.costUsd,
      error: failed ? (doc.error ?? "This file could not be read") : null,
      document_id: doc.id,
      claim_id: claimId,
      batch: doc.batchId,
    });
    if (!failed && doc.plan.decisions.engine !== "jev") {
      calls.push({
        at,
        route: "decision_fallback",
        model_key: "haiku",
        mode: recorded ? "recorded" : "live",
        latency_ms: 850,
        cost_usd: round6(doc.plan.costUsd / 8),
        error: null,
        document_id: doc.id,
        claim_id: claimId,
        batch: doc.batchId,
      });
    }
  }
  const inWindow = calls.filter((c) => new Date(c.at) >= since);
  const mine = (/** @type {Call} */ c) => ctx.state.batches.get(c.batch)?.employeeId === employeeId;

  /** @type {Map<string, { route: string; model_key: string; calls: number; live: number; errors: number; lat: number[]; liveCost: number; recCost: number }>} */
  const routes = new Map();
  const bucket = (/** @type {string} */ route, /** @type {string} */ model) => {
    const key = `${route}|${model}`;
    let found = routes.get(key);
    if (!found) {
      found = {
        route,
        model_key: model,
        calls: 0,
        live: 0,
        errors: 0,
        lat: [],
        liveCost: 0,
        recCost: 0,
      };
      routes.set(key, found);
    }
    return found;
  };
  for (const [route, model, n, live, errors, p50, p95, liveCost, recCost] of HISTORY) {
    const b = bucket(String(route), String(model));
    b.calls += Number(n);
    b.live += Number(live);
    b.errors += Number(errors);
    b.lat.push(Number(p50), Number(p95));
    b.liveCost += Number(liveCost);
    b.recCost += Number(recCost);
  }
  for (const c of inWindow) {
    const b = bucket(c.route, c.model_key);
    b.calls += 1;
    if (c.mode === "live") {
      b.live += 1;
      b.liveCost += c.cost_usd;
    } else b.recCost += c.cost_usd;
    if (c.error) b.errors += 1;
    b.lat.push(c.latency_ms);
  }
  const percentile = (/** @type {number[]} */ xs, /** @type {number} */ p) => {
    if (xs.length === 0) return 0;
    const sorted = [...xs].sort((a, b) => a - b);
    return Math.round(sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))]);
  };
  const routeRows = [...routes.values()]
    .map((b) => ({
      route: b.route,
      model_key: b.model_key,
      calls: b.calls,
      live_calls: b.live,
      errors: b.errors,
      p50_ms: percentile(b.lat, 0.5),
      p95_ms: percentile(b.lat, 0.95),
      live_cost_usd: round6(b.liveCost),
      recorded_cost_usd: round6(b.recCost),
    }))
    .sort((a, b) => b.calls - a.calls);

  const sum = (/** @type {(r: typeof routeRows[number]) => number} */ f) =>
    routeRows.reduce((s, r) => s + f(r), 0);
  const total = sum((r) => r.calls);
  const liveCalls = sum((r) => r.live_calls);
  const errors = sum((r) => r.errors);
  const inputTokens = total * 1800;
  const cacheRead = Math.round(inputTokens * 0.42);

  const failures = inWindow
    .filter((c) => c.error && mine(c))
    .map((c) => ({
      at: c.at,
      route: c.route,
      model_key: c.model_key,
      kind: /recorded sample receipts/i.test(c.error ?? "") ? "not_recorded" : "error",
      message: c.error ?? "",
      trace_id: c.batch,
      batch_id: c.batch,
      document_id: c.document_id,
    }))
    .reverse();

  const traceCalls = traceId
    ? calls
        .filter((c) => c.batch === traceId && mine(c))
        .map((c) => ({ ...c, batch: undefined }))
        .map(({ batch, ...rest }) => (void batch, rest))
    : [];

  return {
    hours,
    since: calls.reduce((m, c) => (c.at < m ? c.at : m), oldest),
    sampled: false,
    totals: {
      calls: total,
      live_calls: liveCalls,
      recorded_calls: total - liveCalls,
      errors,
      not_recorded: failures.filter((f) => f.kind === "not_recorded").length,
      budget_refusals: 0,
      error_rate: total ? round6(errors / total) : 0,
      live_cost_usd: round6(sum((r) => r.live_cost_usd)),
      recorded_cost_usd: round6(sum((r) => r.recorded_cost_usd)),
      input_tokens: inputTokens,
      output_tokens: Math.round(total * 240),
      cache_read_tokens: cacheRead,
      cache_read_share: inputTokens ? round6(cacheRead / inputTokens) : 0,
    },
    routes: routeRows,
    failures,
    trace_calls: traceCalls,
  };
}
