import type { BatchView, DocumentView, PipelineEvent } from "../api/types";

/**
 * The live-processing state of one upload batch, built ONLY from API data: the batch snapshot
 * (GET /v1/batches/{id}) and the typed progress events (history + SSE). The reducer is pure and
 * idempotent: replaying an event with an index it has already applied changes nothing, which is
 * what makes reloads and reconnects safe.
 */

/** waiting -> reading -> read -> checked, or failed. */
export type DocPhase = "waiting" | "reading" | "read" | "checked" | "failed";

export interface DocProgress {
  id: string;
  filename: string;
  position: number;
  /** Stored phase; use `phaseOf()` for the effective one (reading starts with the batch). */
  phase: DocPhase;
  docType: string | null;
  merchant: string | null;
  total: number | null;
  category: string | null;
  categoryConfidence: number | null;
  engine: string | null;
  cached: boolean;
  costUsd: number | null;
  trustScore: number | null;
  verdict: string | null;
  findings: number | null;
  error: string | null;
}

export interface BatchSummary {
  processed: number;
  failed: number;
  claims: number;
  costUsd: number;
}

export type BatchPhase = "idle" | "running" | "done" | "failed";

export interface BatchState {
  batchId: string;
  phase: BatchPhase;
  /** True once `batch_started` was seen (or the server says the batch is processing). */
  started: boolean;
  total: number | null;
  docs: Record<string, DocProgress>;
  order: string[];
  claimIds: string[];
  summary: BatchSummary | null;
  error: string | null;
  /** Sum of the per-document LLM costs reported so far. */
  costUsd: number;
  /** Index (SSE `id`) of the last event applied; -1 before any. */
  lastIndex: number;
  /** The server's batch creation / finish time (ISO), when known. */
  createdAt: string | null;
  finishedAt: string | null;
}

export type BatchAction =
  { type: "hydrate"; batch: BatchView } | { type: "event"; event: PipelineEvent; index?: number };

export function initialBatchState(batchId: string): BatchState {
  return {
    batchId,
    phase: "idle",
    started: false,
    total: null,
    docs: {},
    order: [],
    claimIds: [],
    summary: null,
    error: null,
    costUsd: 0,
    lastIndex: -1,
    createdAt: null,
    finishedAt: null,
  };
}

function blankDoc(id: string, filename: string, position: number): DocProgress {
  return {
    id,
    filename,
    position,
    phase: "waiting",
    docType: null,
    merchant: null,
    total: null,
    category: null,
    categoryConfidence: null,
    engine: null,
    cached: false,
    costUsd: null,
    trustScore: null,
    verdict: null,
    findings: null,
    error: null,
  };
}

function withDoc(state: BatchState, doc: DocProgress): BatchState {
  const docs = { ...state.docs, [doc.id]: doc };
  const order = state.docs[doc.id] ? state.order : [...state.order, doc.id];
  // Cards stay in upload order no matter which event arrives first.
  const sorted = [...order].sort((a, b) => docs[a].position - docs[b].position);
  return { ...state, docs, order: sorted };
}

function fromDocumentView(view: DocumentView, existing?: DocProgress): DocProgress {
  const base = existing ?? blankDoc(view.id, view.filename, view.position);
  const next: DocProgress = { ...base, filename: view.filename, position: view.position };
  if (view.status === "failed") {
    return { ...next, phase: "failed", error: view.error ?? base.error };
  }
  if (view.status === "processed") {
    const doc = view.document;
    return {
      ...next,
      phase: "checked",
      docType: doc?.receipt.doc_type ?? base.docType,
      merchant: doc?.receipt.merchant_name ?? base.merchant,
      total: doc?.receipt.total ?? base.total,
      category: doc?.decisions.category ?? base.category,
      categoryConfidence: doc?.decisions.category_confidence ?? base.categoryConfidence,
      engine: doc?.decisions.engine ?? base.engine,
      trustScore: view.trust_score ?? base.trustScore,
      verdict: view.verdict ?? base.verdict,
      findings: doc ? (doc.findings ?? []).length : base.findings,
    };
  }
  return next; // queued: keep whatever progress events already told us
}

/**
 * The phase a snapshot implies, merged with what the events already told us: a batch never goes
 * backwards (a snapshot fetched a moment before the DB caught up must not undo "done").
 */
function batchPhase(status: string, current: BatchPhase): BatchPhase {
  if (current === "done" || current === "failed") return current;
  if (status === "done") return "done";
  if (status === "failed") return "failed";
  if (status === "processing") return "running";
  return current; // "queued": nothing has started yet
}

function applyEvent(state: BatchState, event: PipelineEvent): BatchState {
  switch (event.type) {
    case "batch_started":
      return {
        ...state,
        started: true,
        total: event.total,
        phase: state.phase === "idle" ? "running" : state.phase,
      };
    case "document_extracted": {
      const existing = state.docs[event.document_id];
      const doc: DocProgress = {
        ...(existing ?? blankDoc(event.document_id, event.filename, event.position)),
        filename: event.filename,
        position: event.position,
        // Never move a document backwards (a replayed "extracted" after "checked").
        phase: existing?.phase === "checked" ? "checked" : "read",
        docType: event.doc_type,
        merchant: event.merchant,
        total: event.total,
        category: event.category,
        categoryConfidence: event.category_confidence,
        engine: event.engine,
        cached: event.cached,
        costUsd: event.cost_usd,
      };
      return {
        ...withDoc(state, doc),
        started: true,
        phase: state.phase === "idle" ? "running" : state.phase,
        costUsd: state.costUsd + event.cost_usd,
      };
    }
    case "document_checked": {
      const existing =
        state.docs[event.document_id] ?? blankDoc(event.document_id, "Receipt", state.order.length);
      return withDoc(state, {
        ...existing,
        phase: "checked",
        trustScore: event.trust_score,
        verdict: event.verdict,
        findings: event.findings,
      });
    }
    case "document_failed": {
      const existing =
        state.docs[event.document_id] ??
        blankDoc(event.document_id, event.filename, state.order.length);
      return withDoc(state, {
        ...existing,
        filename: event.filename,
        phase: "failed",
        error: event.error,
      });
    }
    case "claims_ready":
      return { ...state, claimIds: event.claim_ids };
    case "batch_done":
      return {
        ...state,
        phase: "done",
        summary: {
          processed: event.processed,
          failed: event.failed,
          claims: event.claims,
          costUsd: event.cost_usd,
        },
        costUsd: event.cost_usd,
      };
    case "batch_failed":
      return { ...state, phase: "failed", error: event.error };
  }
}

export function batchReducer(state: BatchState, action: BatchAction): BatchState {
  if (action.type === "hydrate") {
    const { batch } = action;
    let next: BatchState = {
      ...state,
      phase: batchPhase(batch.status, state.phase),
      started: state.started || batch.status === "processing" || batch.status === "done",
      total: state.total ?? batch.total,
      error: batch.error ?? state.error,
      createdAt: batch.created_at,
      finishedAt: batch.finished_at ?? null,
      claimIds: state.claimIds.length > 0 ? state.claimIds : batch.claims.map((c) => c.id),
    };
    for (const view of batch.documents) {
      next = withDoc(next, fromDocumentView(view, next.docs[view.id]));
    }
    return next;
  }

  const { event, index } = action;
  if (index !== undefined) {
    if (index <= state.lastIndex) return state; // already applied (reconnect / replay)
    return { ...applyEvent(state, event), lastIndex: index };
  }
  return applyEvent(state, event);
}

/** The effective phase: a waiting document is "reading" as soon as the batch has started. */
export function phaseOf(state: BatchState, doc: DocProgress): DocPhase {
  return doc.phase === "waiting" && state.started && state.phase === "running"
    ? "reading"
    : doc.phase;
}

export interface BatchProgress {
  total: number;
  /** Documents that have finished (checked or failed). */
  finished: number;
  read: number;
  failed: number;
  /** 0-100 */
  percent: number;
}

export function batchProgress(state: BatchState): BatchProgress {
  const docs = state.order.map((id) => state.docs[id]);
  const total = Math.max(state.total ?? 0, docs.length);
  const failed = docs.filter((d) => d.phase === "failed").length;
  const checked = docs.filter((d) => d.phase === "checked").length;
  const read = docs.filter((d) => d.phase === "read" || d.phase === "checked").length;
  const finished = state.phase === "done" ? total : checked + failed;
  return {
    total,
    finished,
    read,
    failed,
    percent: total === 0 ? 0 : Math.min(100, Math.round((finished / total) * 100)),
  };
}

export function isFinished(state: BatchState): boolean {
  return state.phase === "done" || state.phase === "failed";
}
