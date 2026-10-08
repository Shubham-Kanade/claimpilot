import type {
  ApprovalStatus,
  BatchCreated,
  BatchView,
  ClaimStatus,
  ClaimView,
  DocumentView,
  Employee,
  Me,
  MetaInfo,
  PipelineEvent,
  PromptOut,
  ReplyOut,
  ResetResult,
  Stats,
} from "./types";

export type { MetaInfo, RouteInfo } from "./types";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

interface ProblemBody {
  type: string;
  title: string;
  detail: Record<string, unknown> | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readProblem(body: unknown, status: number): ProblemBody {
  if (isRecord(body) && typeof body.type === "string" && typeof body.title === "string") {
    return {
      type: body.type,
      title: body.title,
      detail: isRecord(body.detail) ? body.detail : null,
    };
  }
  // FastAPI request-validation errors are not problem+json: { detail: [{ loc, msg, type }] }.
  if (isRecord(body) && Array.isArray(body.detail)) {
    return { type: "validation_error", title: "The request was not valid", detail: null };
  }
  const title = isRecord(body) && typeof body.title === "string" ? body.title : null;
  return {
    type: `http_${status}`,
    title: title ?? `API request failed with ${status}`,
    detail: null,
  };
}

/**
 * Every failed API call surfaces as one typed `ApiError`: the problem+json `type` code, its title
 * and optional `detail` object. `status` 0 means the request never reached the server.
 * `friendlyError()` (problems.ts) is the single place that turns these into user-facing copy.
 */
export class ApiError extends Error {
  readonly type: string;
  readonly title: string;
  readonly detail: Record<string, unknown> | null;

  constructor(
    readonly status: number,
    readonly body: unknown,
  ) {
    const problem = readProblem(body, status);
    super(problem.title);
    this.name = "ApiError";
    this.type = problem.type;
    this.title = problem.title;
    this.detail = problem.detail;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

const NETWORK_PROBLEM = {
  type: "network_error",
  title: "Cannot reach the ClaimPilot API",
  status: 0,
};

/**
 * Turn the configured API base and a path into an absolute URL.
 *
 * The base is either absolute ("http://localhost:8000": local dev, mock API) or RELATIVE ("/api":
 * the hosted demo is one origin behind a reverse proxy that strips "/api"). A relative base is
 * resolved against the page's own origin, so it only works in the browser; server rendering never
 * calls the API (all data is fetched client-side), and a relative base without an origin is a
 * configuration error worth a clear message.
 */
export function resolveApiUrl(baseUrl: string, path: string, origin?: string | null): URL {
  const base = baseUrl.replace(/\/+$/, "");
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(base)) return new URL(`${base}${path}`);
  const resolvedOrigin = origin ?? (typeof window === "undefined" ? null : window.location.origin);
  if (!resolvedOrigin) {
    throw new Error(`The API base URL "${baseUrl}" is relative and can only be used in a browser.`);
  }
  return new URL(`${base}${path}`, resolvedOrigin);
}

export interface ApiOptions {
  baseUrl?: string;
  /** Page origin used to resolve a relative `baseUrl` (defaults to window.location.origin). */
  origin?: string;
  /** Employee id sent as `X-Persona` on every request. */
  persona?: string | null;
  /** Override for tests; defaults to the global fetch, looked up at call time. */
  fetch?: typeof fetch;
}

interface RequestOptions {
  method?: "GET" | "POST";
  json?: unknown;
  form?: FormData;
  headers?: Record<string, string>;
  query?: Record<string, string | number | undefined | null>;
  signal?: AbortSignal;
  accept?: string;
}

function isAbort(error: unknown): boolean {
  // Matched by name, not class: the abort error can come from another realm (jsdom, undici).
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: string }).name === "AbortError"
  );
}

export function createApi(options: ApiOptions = {}) {
  const baseUrl = options.baseUrl ?? API_BASE_URL;
  const persona = options.persona ?? null;

  async function send(path: string, opts: RequestOptions = {}): Promise<Response> {
    const url = resolveApiUrl(baseUrl, path, options.origin);
    for (const [key, value] of Object.entries(opts.query ?? {})) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
    const headers: Record<string, string> = { Accept: opts.accept ?? "application/json" };
    if (persona) headers["X-Persona"] = persona;
    if (opts.json !== undefined) headers["Content-Type"] = "application/json";
    Object.assign(headers, opts.headers);

    let response: Response;
    try {
      response = await (options.fetch ?? globalThis.fetch)(url.toString(), {
        method: opts.method ?? "GET",
        headers,
        body: opts.form ?? (opts.json !== undefined ? JSON.stringify(opts.json) : undefined),
        signal: opts.signal,
      });
    } catch (error) {
      if (opts.signal?.aborted || isAbort(error)) throw error;
      throw new ApiError(0, NETWORK_PROBLEM);
    }
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      let body: unknown = null;
      try {
        body = text ? JSON.parse(text) : null;
      } catch {
        body = null;
      }
      throw new ApiError(response.status, body);
    }
    return response;
  }

  async function json<T>(path: string, opts?: RequestOptions): Promise<T> {
    const response = await send(path, opts);
    return (await response.json()) as T;
  }

  const enc = encodeURIComponent;

  return {
    persona,
    baseUrl,

    // --- no persona needed -------------------------------------------------------------
    meta: (signal?: AbortSignal) => json<MetaInfo>("/v1/meta", { signal }),
    employees: (signal?: AbortSignal) => json<Employee[]>("/v1/employees", { signal }),

    // --- persona ------------------------------------------------------------------------
    me: (signal?: AbortSignal) => json<Me>("/v1/me", { signal }),
    stats: (signal?: AbortSignal) => json<Stats>("/v1/stats", { signal }),

    // --- batches -------------------------------------------------------------------------
    createBatch(files: readonly File[], signal?: AbortSignal) {
      const form = new FormData();
      for (const file of files) form.append("files", file, file.name);
      return json<BatchCreated>("/v1/batches", { method: "POST", form, signal });
    },
    getBatch: (id: string, signal?: AbortSignal) =>
      json<BatchView>(`/v1/batches/${enc(id)}`, { signal }),
    batchHistory: (id: string, after = 0, signal?: AbortSignal) =>
      json<PipelineEvent[]>(`/v1/batches/${enc(id)}/history`, { query: { after }, signal }),
    /**
     * The raw SSE response. EventSource cannot send the X-Persona header, so the stream is read
     * with fetch (see sse.ts); `lastEventId` resumes after the last event the caller has seen.
     */
    batchEvents: (id: string, opts: { lastEventId?: string | null; signal?: AbortSignal } = {}) =>
      send(`/v1/batches/${enc(id)}/events`, {
        accept: "text/event-stream",
        headers: opts.lastEventId ? { "Last-Event-ID": opts.lastEventId } : undefined,
        signal: opts.signal,
      }),

    // --- claims --------------------------------------------------------------------------
    listClaims: (
      filter: { status?: ClaimStatus | null; route?: string | null } = {},
      signal?: AbortSignal,
    ) =>
      json<ClaimView[]>("/v1/claims", {
        query: { status: filter.status, route: filter.route },
        signal,
      }),
    getClaim: (id: string, signal?: AbortSignal) =>
      json<ClaimView>(`/v1/claims/${enc(id)}`, { signal }),
    getPrompt: (id: string, signal?: AbortSignal) =>
      json<PromptOut>(`/v1/claims/${enc(id)}/prompt`, { signal }),
    reply: (id: string, text: string) =>
      json<ReplyOut>(`/v1/claims/${enc(id)}/reply`, { method: "POST", json: { text } }),
    answer: (id: string, answers: Record<string, string>) =>
      json<ClaimView>(`/v1/claims/${enc(id)}/answers`, { method: "POST", json: { answers } }),
    /** `idempotencyKey` must stay the same across retries of one submission. */
    submit: (id: string, idempotencyKey: string) =>
      json<ClaimView>(`/v1/claims/${enc(id)}/submit`, {
        method: "POST",
        json: { confirmed: true },
        headers: { "Idempotency-Key": idempotencyKey },
      }),

    // --- approvals ------------------------------------------------------------------------
    approvals: (status: ApprovalStatus = "submitted", signal?: AbortSignal) =>
      json<ClaimView[]>("/v1/approvals", { query: { status }, signal }),
    decide: (id: string, decision: { approved: boolean; comment?: string }) =>
      json<ClaimView>(`/v1/claims/${enc(id)}/decision`, {
        method: "POST",
        json: { approved: decision.approved, comment: decision.comment ?? "" },
      }),

    // --- public demo ----------------------------------------------------------------------
    /** "Start over" (only when GET /v1/meta says `demo`): delete your uploads and claims. */
    demoReset: () => json<ResetResult>("/v1/demo/reset", { method: "POST" }),

    // --- documents ------------------------------------------------------------------------
    getDocument: (id: string, signal?: AbortSignal) =>
      json<DocumentView>(`/v1/documents/${enc(id)}`, { signal }),
    /** The original upload as a Blob (an <img src> cannot send the X-Persona header). */
    async documentFile(id: string, signal?: AbortSignal) {
      const response = await send(`/v1/documents/${enc(id)}/file`, { accept: "*/*", signal });
      const blob = await response.blob();
      return { blob, contentType: response.headers.get("Content-Type") ?? blob.type };
    },
  };
}

export type ApiClient = ReturnType<typeof createApi>;

/** Persona-less client for the public endpoints (`/v1/meta`, `/v1/employees`). */
export const api = createApi();
