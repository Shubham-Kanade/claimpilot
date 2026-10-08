// @ts-check
/**
 * The routes: every endpoint of openapi.json plus `POST /__mock/reset`, with the backend's status
 * codes, problem types and ordering of checks (persona 401, approver 403, body validation 422,
 * then the domain rules).
 */

import {
  ClaimError,
  combinedPrompt,
  answerQuestion,
  decideClaim,
  refinalizeClaim,
  submitClaim,
  unansweredOf,
} from "./claims.mjs";
import { APPROVER_IDS, EMPLOYEES, META } from "./data.mjs";
import {
  PlainError,
  ValidationFailure,
  corsHeaders,
  drain,
  preflightHeaders,
  problem,
  readJson,
  sendError,
  sendJson,
} from "./http.mjs";
import { startOver } from "./demo.mjs";
import { createBatch } from "./pipeline.mjs";
import { routeFor } from "./policy.mjs";
import { followUpMessage, interpretReply } from "./reply.mjs";
import { streamEvents } from "./sse.mjs";
import {
  batchView,
  collectStats,
  documentView,
  listClaims,
  nextReference,
  saveClaim,
} from "./state.mjs";
import { simulate } from "./timing.mjs";
import { MultipartError, readUploads, sniffMediaType, validateUploads } from "./uploads.mjs";
import {
  parseAfter,
  parseAnswersIn,
  parseDecisionIn,
  parseReplyIn,
  parseSubmitIn,
} from "./validate.mjs";

/** @typedef {import("node:http").IncomingMessage} IncomingMessage */
/** @typedef {import("node:http").ServerResponse} ServerResponse */
/** @typedef {import("./types.mjs").Claim} Claim */
/** @typedef {import("./types.mjs").Context} Context */
/** @typedef {import("./types.mjs").Persona} Persona */
/** @typedef {import("./types.mjs").Schemas} Schemas */

const API_VERSION = "0.1.0";

/**
 * @typedef {Object} Call
 * @property {Context} ctx
 * @property {IncomingMessage} req
 * @property {ServerResponse} res
 * @property {URL} url
 * @property {Record<string, string>} params  Path parameters.
 * @property {Persona} persona  The acting persona (a placeholder on public routes).
 * @property {unknown} body  The parsed JSON body (undefined when there is none).
 */

/**
 * @typedef {Object} Route
 * @property {string} method
 * @property {RegExp} pattern
 * @property {boolean} persona  Needs `X-Persona`.
 * @property {boolean} [approver]  Approvers only.
 * @property {boolean} [json]  Has a JSON body, read before the persona check like FastAPI does.
 * @property {(call: Call) => Promise<void> | void} handler
 */

// --- persona and visibility ----------------------------------------------------------------------------------

/**
 * The acting persona from `X-Persona` (no real login in the demo).
 * @param {IncomingMessage} req
 * @returns {Persona}
 */
function resolvePersona(req) {
  const header = req.headers["x-persona"];
  const value = Array.isArray(header) ? header[0] : header;
  if (!value) throw problem(401, "missing_persona", "Send an X-Persona header");
  const employee = EMPLOYEES.find((e) => e.id === value);
  if (!employee) throw problem(401, "unknown_persona", `Unknown persona ${value}`);
  return { employee, isApprover: APPROVER_IDS.includes(employee.id) };
}

/**
 * Approvers see everything; everyone else only what they own (a 404 otherwise, not a 403).
 * @param {Persona} persona
 * @param {string} ownerId
 */
function canSee(persona, ownerId) {
  return persona.isApprover || persona.employee.id === ownerId;
}

/**
 * @param {Call} call
 */
function visibleBatch({ ctx, persona, params }) {
  const batch = ctx.state.batches.get(params.id ?? "");
  if (!batch || !canSee(persona, batch.employeeId)) {
    throw problem(404, "batch_not_found", "No such batch");
  }
  return batch;
}

/**
 * @param {Call} call
 * @returns {Claim}
 */
function visibleClaim({ ctx, persona, params }) {
  const record = ctx.state.claims.get(params.id ?? "");
  if (!record || !canSee(persona, record.view.employee_id)) {
    throw problem(404, "claim_not_found", "No such claim");
  }
  return record.view;
}

/**
 * @param {Call} call
 */
function visibleDocument({ ctx, persona, params }) {
  const doc = ctx.state.docs.get(params.id ?? "");
  if (!doc || !canSee(persona, doc.employeeId)) {
    throw problem(404, "document_not_found", "No such document");
  }
  return doc;
}

// --- claim actions (pipeline/actions.py) -------------------------------------------------------------------------

/**
 * Apply answers to a claim of the acting persona; nothing is saved when any answer is refused.
 * @param {Call} call
 * @param {Claim} view
 * @param {Record<string, string>} answers
 * @returns {Claim}
 */
function applyAnswers(call, view, answers) {
  if (call.persona.employee.id !== view.employee_id) {
    throw problem(403, "not_your_claim", "Only the owner can answer");
  }
  let claim = view;
  for (const [questionId, text] of Object.entries(answers)) {
    try {
      claim = answerQuestion(claim, questionId, text);
    } catch (error) {
      if (!(error instanceof ClaimError)) throw error;
      if (error.kind === "locked") throw problem(409, "claim_locked", error.message);
      if (error.kind === "blank") throw problem(422, "blank_answer", "An answer cannot be blank");
      throw problem(422, "unknown_question", `This claim has no question ${questionId}`);
    }
  }
  // Answers can change the policy picture (a headcount, say): rebuild findings and questions.
  const docs = claim.document_ids.flatMap((id) => {
    const processed = call.ctx.state.docs.get(id)?.processed;
    return processed ? [processed] : [];
  });
  claim = refinalizeClaim(claim, docs, call.persona.employee, call.ctx.config.categoryQuestions);
  const updated = { ...claim, route: routeFor(claim) };
  saveClaim(call.ctx.state, updated);
  return updated;
}

// --- handlers ---------------------------------------------------------------------------------------------------

/** @type {Route[]} */
export const ROUTES = [
  {
    method: "GET",
    pattern: /^\/healthz$/,
    persona: false,
    handler: ({ res }) => {
      /** @type {Schemas["Health"]} */
      const body = { status: "ok", version: API_VERSION };
      sendJson(res, 200, body);
    },
  },
  {
    method: "GET",
    pattern: /^\/readyz$/,
    persona: false,
    handler: ({ res }) => {
      /** @type {Schemas["Readiness"]} */
      const body = { status: "ready", checks: { redis: "ok", postgres: "ok" } };
      sendJson(res, 200, body);
    },
  },
  {
    method: "GET",
    pattern: /^\/v1\/meta$/,
    persona: false,
    handler: ({ ctx, res }) => {
      /** @type {Schemas["MetaInfo"]} */
      const body = {
        llm_mode: META.llm_mode,
        decision_engine: META.decision_engine,
        demo: ctx.config.demo,
        runtime: META.runtime,
        routes: META.routes,
      };
      sendJson(res, 200, body);
    },
  },
  {
    method: "GET",
    pattern: /^\/v1\/employees$/,
    persona: false,
    handler: ({ res }) => {
      /** @type {Schemas["Employee"][]} */
      const body = EMPLOYEES;
      sendJson(res, 200, body);
    },
  },
  {
    method: "GET",
    pattern: /^\/v1\/me$/,
    persona: true,
    handler: ({ res, persona }) => {
      /** @type {Schemas["Me"]} */
      const body = { employee: persona.employee, is_approver: persona.isApprover };
      sendJson(res, 200, body);
    },
  },
  {
    method: "GET",
    pattern: /^\/v1\/stats$/,
    persona: true,
    handler: ({ res, ctx }) => sendJson(res, 200, collectStats(ctx.state)),
  },

  // --- batches ---
  {
    method: "POST",
    pattern: /^\/v1\/batches$/,
    persona: true,
    handler: async ({ ctx, req, res, persona }) => {
      let upload;
      try {
        upload = await readUploads(req, {
          field: "files",
          maxFiles: ctx.config.maxFiles,
          maxFileBytes: ctx.config.maxFileBytes,
        });
      } catch (error) {
        if (!(error instanceof MultipartError)) throw error;
        // Not a multipart body at all: FastAPI says the required `files` field is missing.
        if (error.message === "not multipart") {
          throw new ValidationFailure([
            { type: "missing", loc: ["body", "files"], msg: "Field required", input: null },
          ]);
        }
        throw new PlainError(400, "There was an error parsing the body");
      }
      const files = validateUploads(upload, ctx.config);
      sendJson(res, 202, createBatch(ctx, persona.employee.id, files));
    },
  },
  {
    method: "GET",
    pattern: /^\/v1\/batches\/([^/]+)$/,
    persona: true,
    handler: (call) => {
      const batch = visibleBatch(call);
      sendJson(call.res, 200, batchView(call.ctx.state, batch));
    },
  },
  {
    method: "GET",
    pattern: /^\/v1\/batches\/([^/]+)\/history$/,
    persona: true,
    handler: (call) => {
      const after = parseAfter(call.url.searchParams.get("after"));
      const batch = visibleBatch(call);
      sendJson(call.res, 200, batch.events.slice(after));
    },
  },
  {
    method: "GET",
    pattern: /^\/v1\/batches\/([^/]+)\/events$/,
    persona: true,
    handler: (call) => {
      const batch = visibleBatch(call);
      const lastEventId = call.req.headers["last-event-id"];
      const start =
        typeof lastEventId === "string" && /^\d+$/.test(lastEventId) ? Number(lastEventId) + 1 : 0;
      streamEvents(call.ctx, call.req, call.res, batch, start);
    },
  },

  // --- claims ---
  {
    method: "GET",
    pattern: /^\/v1\/claims$/,
    persona: true,
    handler: ({ ctx, res, url, persona }) => {
      const owner = persona.isApprover ? url.searchParams.get("employee_id") : persona.employee.id;
      sendJson(
        res,
        200,
        listClaims(ctx.state, {
          employeeId: owner,
          status: url.searchParams.get("status"),
          route: url.searchParams.get("route"),
        }),
      );
    },
  },
  {
    method: "GET",
    pattern: /^\/v1\/claims\/([^/]+)$/,
    persona: true,
    handler: (call) => sendJson(call.res, 200, visibleClaim(call)),
  },
  {
    method: "GET",
    pattern: /^\/v1\/claims\/([^/]+)\/prompt$/,
    persona: true,
    handler: (call) => {
      const claim = visibleClaim(call);
      /** @type {Schemas["PromptOut"]} */
      const body = {
        prompt: combinedPrompt(claim),
        open_question_ids: unansweredOf(claim).map((q) => q.id),
      };
      sendJson(call.res, 200, body);
    },
  },
  {
    method: "POST",
    pattern: /^\/v1\/claims\/([^/]+)\/answers$/,
    persona: true,
    json: true,
    handler: (call) => {
      const { answers } = parseAnswersIn(call.body);
      const view = visibleClaim(call);
      sendJson(call.res, 200, applyAnswers(call, view, answers));
    },
  },
  {
    method: "POST",
    pattern: /^\/v1\/claims\/([^/]+)\/reply$/,
    persona: true,
    json: true,
    handler: async (call) => {
      const { text } = parseReplyIn(call.body);
      const view = visibleClaim(call);
      if (call.persona.employee.id !== view.employee_id) {
        throw problem(403, "not_your_claim", "Only the owner can answer");
      }
      const open = unansweredOf(view);
      if (!open.length) {
        /** @type {Schemas["ReplyOut"]} */
        const nothing = { claim: view, understood: {}, follow_up: null };
        sendJson(call.res, 200, nothing);
        return;
      }
      const understood = interpretReply(view, text);
      const answered = Object.keys(understood).length > 0;
      const claim = answered ? applyAnswers(call, view, understood) : view;
      // With several questions open the backend asks a small LLM to split the reply.
      if (open.length > 1) await simulate(call.ctx, 700);
      /** @type {Schemas["ReplyOut"]} */
      const body = { claim, understood, follow_up: followUpMessage(claim, answered) };
      sendJson(call.res, 200, body);
    },
  },
  {
    method: "POST",
    pattern: /^\/v1\/claims\/([^/]+)\/submit$/,
    persona: true,
    json: true,
    handler: async (call) => {
      const { confirmed } = parseSubmitIn(call.body);
      const view = visibleClaim(call);
      if (call.persona.employee.id !== view.employee_id) {
        throw problem(403, "not_your_claim", "Only the owner can submit");
      }
      if (view.status === "submitted" && view.submission_reference) {
        sendJson(call.res, 200, view); // a retry of a submission that went through: same result
        return;
      }
      if (!confirmed) {
        throw problem(
          422,
          "confirmation_required",
          "The employee must explicitly confirm the submission",
        );
      }
      if (view.status !== "ready") {
        throw problem(409, "claim_not_ready", "The claim is not ready to submit", {
          status: view.status,
          unanswered: unansweredOf(view).map((q) => q.id),
        });
      }
      const header = call.req.headers["idempotency-key"];
      const key = (Array.isArray(header) ? header[0] : header) || `submit-${view.id}`;
      const slot = `${view.id}|${key}`;
      const { state } = call.ctx;
      const reference = state.idempotency.get(slot) ?? nextReference(state);
      state.idempotency.set(slot, reference);
      const claim = submitClaim(view, reference); // the route stays as decided
      saveClaim(state, claim);
      await simulate(call.ctx, 350); // the finance system takes a moment
      sendJson(call.res, 200, claim);
    },
  },
  {
    method: "GET",
    pattern: /^\/v1\/approvals$/,
    persona: true,
    approver: true,
    handler: ({ ctx, res, url }) => {
      const status = url.searchParams.has("status") ? url.searchParams.get("status") : "submitted";
      sendJson(res, 200, listClaims(ctx.state, { status }));
    },
  },
  {
    method: "POST",
    pattern: /^\/v1\/claims\/([^/]+)\/decision$/,
    persona: true,
    approver: true,
    json: true,
    handler: async (call) => {
      const { approved, comment } = parseDecisionIn(call.body);
      if (!approved && !comment.trim()) {
        throw problem(422, "comment_required", "Say why the claim is rejected");
      }
      const view = visibleClaim(call);
      if (view.status === (approved ? "approved" : "rejected")) {
        sendJson(call.res, 200, view); // repeating a decision that was already made is harmless
        return;
      }
      if (view.status !== "submitted" || !view.submission_reference) {
        throw problem(
          409,
          "claim_not_submitted",
          "Only a submitted claim can be approved or rejected",
          { status: view.status },
        );
      }
      const claim = decideClaim(view, approved);
      saveClaim(call.ctx.state, claim);
      await simulate(call.ctx, 250);
      sendJson(call.res, 200, claim);
    },
  },

  // --- documents ---
  {
    method: "GET",
    pattern: /^\/v1\/documents\/([^/]+)$/,
    persona: true,
    handler: (call) => sendJson(call.res, 200, documentView(visibleDocument(call))),
  },
  {
    method: "GET",
    pattern: /^\/v1\/documents\/([^/]+)\/file$/,
    persona: true,
    handler: (call) => {
      const doc = visibleDocument(call);
      const mediaType = doc.bytes ? sniffMediaType(doc.bytes) : null;
      if (!doc.bytes || !mediaType) {
        throw problem(404, "file_unavailable", "The file is no longer available");
      }
      call.res.writeHead(200, {
        "Content-Type": mediaType,
        "Content-Length": doc.bytes.length,
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline",
      });
      call.res.end(doc.bytes);
    },
  },

  // --- demo ---
  {
    method: "POST",
    pattern: /^\/v1\/demo\/reset$/,
    persona: true,
    handler: ({ ctx, res, persona }) => {
      if (!ctx.config.demo) {
        throw problem(404, "demo_disabled", "Start over is for the demo only");
      }
      // An employee clears their own uploads and claims; the approver clears everyone's.
      /** @type {Schemas["ResetResult"]} */
      const body = startOver(ctx, persona.isApprover ? null : persona.employee.id);
      sendJson(res, 200, body);
    },
  },
];

// --- dispatch -----------------------------------------------------------------------------------------------------

/**
 * Serve one request. `reset` is called for `POST /__mock/reset`.
 * @param {Context} ctx
 * @param {IncomingMessage} req
 * @param {ServerResponse} res
 * @param {() => void} reset
 */
export async function handle(ctx, req, res, reset) {
  for (const [name, value] of Object.entries(corsHeaders(req))) res.setHeader(name, value);
  try {
    const method = req.method ?? "GET";
    const url = new URL(req.url ?? "/", "http://localhost");
    const path = url.pathname.length > 1 ? url.pathname.replace(/\/+$/, "") : url.pathname;

    if (method === "OPTIONS") {
      for (const [name, value] of Object.entries(preflightHeaders(req))) res.setHeader(name, value);
      res.writeHead(204);
      res.end();
      return;
    }
    if (method === "POST" && path === "/__mock/reset") {
      await drain(req);
      reset();
      res.writeHead(204);
      res.end();
      return;
    }

    let known = false;
    for (const route of ROUTES) {
      const match = route.pattern.exec(path);
      if (!match) continue;
      known = true;
      if (route.method !== method) continue;
      const params = { id: decodeId(match[1]) };
      const body = route.json ? await readJson(req) : undefined;
      const persona = route.persona ? resolvePersona(req) : placeholderPersona();
      if (route.approver && !persona.isApprover) {
        throw problem(403, "approver_only", "Only approvers can do this");
      }
      await route.handler({ ctx, req, res, url, params, persona, body });
      return;
    }
    await drain(req);
    throw new PlainError(known ? 405 : 404, known ? "Method Not Allowed" : "Not Found");
  } catch (error) {
    if (req.socket.destroyed || isDisconnect(error)) {
      res.destroy(); // the client went away mid-request: nobody to answer
      return;
    }
    sendError(res, error);
  }
}

/**
 * Did the client hang up (a closed or reset connection)?
 * @param {unknown} error
 */
function isDisconnect(error) {
  const code = error instanceof Error && "code" in error ? String(error.code) : "";
  return (
    code === "ECONNRESET" ||
    code === "ERR_STREAM_PREMATURE_CLOSE" ||
    (error instanceof Error && error.message === "aborted")
  );
}

/** Public routes have no acting persona; handlers never read this one. */
function placeholderPersona() {
  return { employee: EMPLOYEES[0], isApprover: false };
}

/**
 * A path parameter, percent-decoded.
 * @param {string | undefined} raw
 */
function decodeId(raw) {
  try {
    return decodeURIComponent(raw ?? "");
  } catch {
    return raw ?? "";
  }
}
