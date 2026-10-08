// @ts-check
/**
 * HTTP plumbing shared by the routes: problem responses (RFC 9457 style, the backend's `type`
 * codes), FastAPI-style validation errors, CORS and JSON bodies.
 */

/** @typedef {import("node:http").IncomingMessage} IncomingMessage */
/** @typedef {import("node:http").ServerResponse} ServerResponse */

// --- problems ------------------------------------------------------------------------------------

/** An error the API answers with `application/problem+json` (problem.py). */
export class ApiProblem extends Error {
  /**
   * @param {number} status
   * @param {string} type  Stable machine code, e.g. `claim_not_ready`.
   * @param {string} title
   * @param {Record<string, unknown> | null} [detail]
   */
  constructor(status, type, title, detail = null) {
    super(title);
    this.name = "ApiProblem";
    this.status = status;
    this.type = type;
    this.title = title;
    this.detail = detail;
  }
}

/**
 * @param {number} status
 * @param {string} type
 * @param {string} title
 * @param {Record<string, unknown> | null} [detail]
 */
export function problem(status, type, title, detail = null) {
  return new ApiProblem(status, type, title, detail);
}

/**
 * A request the framework itself rejects (FastAPI's `{"detail": ...}` bodies).
 */
export class ValidationFailure extends Error {
  /**
   * @param {Array<Record<string, unknown>>} errors  FastAPI/pydantic style error objects.
   */
  constructor(errors) {
    super("Request validation failed");
    this.name = "ValidationFailure";
    this.errors = errors;
  }
}

/** Anything the framework answers on its own: `{"detail": "..."}` with a status code. */
export class PlainError extends Error {
  /**
   * @param {number} status
   * @param {string} detail
   */
  constructor(status, detail) {
    super(detail);
    this.name = "PlainError";
    this.status = status;
    this.detail = detail;
  }
}

// --- responses -----------------------------------------------------------------------------------

/**
 * @param {ServerResponse} res
 * @param {number} status
 * @param {unknown} body
 * @param {string} [contentType]
 */
export function sendJson(res, status, body, contentType = "application/json") {
  const payload = Buffer.from(JSON.stringify(body));
  res.writeHead(status, { "Content-Type": contentType, "Content-Length": payload.length });
  res.end(payload);
}

/**
 * @param {ServerResponse} res
 * @param {ApiProblem} error
 */
export function sendProblem(res, error) {
  /** @type {Record<string, unknown>} */
  const body = { type: error.type, title: error.title, status: error.status };
  if (error.detail) body.detail = error.detail;
  sendJson(res, error.status, body, "application/problem+json");
}

/**
 * Turn anything thrown by a handler into the response the backend would give.
 * @param {ServerResponse} res
 * @param {unknown} error
 */
export function sendError(res, error) {
  if (res.headersSent) {
    res.destroy();
    return;
  }
  if (error instanceof ApiProblem) {
    sendProblem(res, error);
  } else if (error instanceof ValidationFailure) {
    sendJson(res, 422, { detail: error.errors });
  } else if (error instanceof PlainError) {
    sendJson(res, error.status, { detail: error.detail });
  } else {
    console.error("mock-api: unhandled error", error);
    sendJson(
      res,
      500,
      { type: "internal_error", title: "Internal Server Error", status: 500 },
      "application/problem+json",
    );
  }
}

// --- CORS (the web app runs on localhost:<port> and calls this mock) -------------------------------------

const ALLOWED_ORIGIN = /^http:\/\/(?:localhost|127\.0\.0\.1)(?::\d{1,5})?$/;

/**
 * Headers every response carries: the origin is reflected only for http://localhost:<port> and
 * http://127.0.0.1:<port>.
 * @param {IncomingMessage} req
 * @returns {Record<string, string>}
 */
export function corsHeaders(req) {
  /** @type {Record<string, string>} */
  const headers = { Vary: "Origin" };
  const origin = req.headers.origin;
  if (typeof origin === "string" && ALLOWED_ORIGIN.test(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
    headers["Access-Control-Expose-Headers"] = "Content-Type";
  }
  return headers;
}

/**
 * What a preflight adds on top of `corsHeaders` (only for an allowed origin).
 * @param {IncomingMessage} req
 * @returns {Record<string, string>}
 */
export function preflightHeaders(req) {
  const origin = req.headers.origin;
  if (typeof origin !== "string" || !ALLOWED_ORIGIN.test(origin)) return {};
  return {
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "X-Persona, Idempotency-Key, Content-Type, Last-Event-ID",
    "Access-Control-Max-Age": "600",
  };
}

// --- bodies --------------------------------------------------------------------------------------

/**
 * @param {IncomingMessage} req
 * @param {number} [limit]
 * @returns {Promise<Buffer>}
 */
async function readAll(req, limit = 5 * 1024 * 1024) {
  /** @type {Buffer[]} */
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limit) {
      // Keep draining so the client can finish sending and read the answer.
      continue;
    }
    chunks.push(chunk);
  }
  if (size > limit) throw problem(413, "payload_too_large", "The request body is too large");
  return Buffer.concat(chunks);
}

/**
 * The parsed JSON body: `undefined` when there is none. A body that is not JSON is a 422 like in
 * FastAPI (`json_invalid`).
 * @param {IncomingMessage} req
 * @returns {Promise<unknown>}
 */
export async function readJson(req) {
  const raw = await readAll(req);
  if (!raw.length) return undefined;
  const text = raw.toString("utf8");
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new ValidationFailure([
      {
        type: "json_invalid",
        loc: ["body", 0],
        msg: "JSON decode error",
        input: {},
        ctx: { error: error instanceof Error ? error.message : "invalid JSON" },
      },
    ]);
  }
}

/**
 * Read and discard a body (for requests answered without looking at it).
 * @param {IncomingMessage} req
 */
export async function drain(req) {
  for await (const chunk of req) void chunk;
}
