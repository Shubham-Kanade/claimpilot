// @vitest-environment node
/**
 * Contract test of the mock API: boots it on a free port, exercises EVERY endpoint over real HTTP
 * and validates every response (status, content type and body) against the OpenAPI schemas with
 * Ajv, so the mock cannot drift from openapi.json. The scenario assertions are the ones the web
 * app's E2E tests are built on (see samples.mjs).
 */
import { existsSync, readFileSync } from "node:fs";
import http from "node:http";
import type { AddressInfo } from "node:net";
import { Readable } from "node:stream";

import type { ValidateFunction } from "ajv";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import type { components } from "../src/lib/api/schema";
import { boxesFor } from "./boxes.mjs";
import { EMPLOYEES, META, POLICY_CLAUSES } from "./data.mjs";
import { dateRange, formatInr, roundHalfEven, rupees } from "./format.mjs";
import { generatePlan, makeRng } from "./generate.mjs";
import { checksumChar } from "./gstin.mjs";
import { publish } from "./pipeline.mjs";
import { TRUTH_IDS, TRUTH_RECEIPTS } from "./receipts.mjs";
import { sha256Hex } from "./samples.mjs";
import { createMockApi } from "./server.mjs";
import { checkGst } from "./trust.mjs";
import { streamEvents } from "./sse.mjs";
import { MultipartError, parseParams, readUploads, safeName, sniffMediaType } from "./uploads.mjs";

vi.setConfig({ testTimeout: 60_000, hookTimeout: 60_000 });

type S = components["schemas"];
type Finding = S["Finding"];
// The generated types mark the collections optional; the mock (like the backend) always sends them.
type OpenQuestion = S["OpenQuestion"] & { document_ids: string[] };
type ClaimView = S["ClaimView"] & { findings: Finding[]; open_questions: OpenQuestion[] };
type ProcessedDocument = S["ProcessedDocument"] & {
  findings: Finding[];
  boxes: Record<string, S["Box"]>;
};
type DocumentView = Omit<S["DocumentView"], "document"> & { document?: ProcessedDocument | null };
type BatchView = Omit<S["BatchView"], "claims" | "documents"> & {
  claims: ClaimView[];
  documents: DocumentView[];
};
type ReplyOut = Omit<S["ReplyOut"], "claim"> & { claim: ClaimView };
type Problem = S["Problem"];
type PipelineEvent =
  | S["BatchStarted"]
  | S["DocumentExtracted"]
  | S["DocumentChecked"]
  | S["DocumentFailed"]
  | S["ClaimsReady"]
  | S["BatchDone"]
  | S["BatchFailed"];
type MockApi = Awaited<ReturnType<typeof createMockApi>>;

const SAMPLES_DIR = new URL("./fixtures/", import.meta.url);
const REPO_ROOT = new URL("../../../", import.meta.url);

const ASHA = "DEMO-ASHA";
const P001 = "P001";
const P005 = "P005";
const MEERA = "DEMO-MEERA";
const RAVI = "DEMO-RAVI";

// --- OpenAPI conformance --------------------------------------------------------------------------

interface OpenApiOperation {
  responses: Record<string, { content?: Record<string, { schema?: unknown }> }>;
}
interface OpenApiDocument {
  components: { schemas: Record<string, unknown> };
  paths: Record<string, Record<string, OpenApiOperation>>;
}

const openapi = JSON.parse(
  readFileSync(new URL("../openapi.json", import.meta.url), "utf8"),
) as OpenApiDocument;

const AjvClass = (Ajv2020 as unknown as { default?: typeof Ajv2020 }).default ?? Ajv2020;
const ajv = new AjvClass({ strict: false, allErrors: true });
const applyFormats =
  (addFormats as unknown as { default?: typeof addFormats }).default ?? addFormats;
applyFormats(ajv);
ajv.addSchema(openapi as unknown as object, "openapi.json");

const validators = new Map<string, ValidateFunction>();

function schemaValidator(name: string): ValidateFunction {
  let validate = validators.get(name);
  if (!validate) {
    validate = ajv.compile({ $ref: `openapi.json#/components/schemas/${name}` });
    validators.set(name, validate);
  }
  return validate;
}

function assertSchema(name: string, data: unknown, context = "value"): void {
  const validate = schemaValidator(name);
  if (!validate(data)) {
    const errors = ajv.errorsText(validate.errors, { separator: "; " });
    throw new Error(
      `${context} does not match ${name}: ${errors}\n${JSON.stringify(data).slice(0, 700)}`,
    );
  }
}

/** Inline response schemas (arrays of ClaimView ...) refer to `#/components/...` of the document. */
function rebase(schema: unknown): unknown {
  if (Array.isArray(schema)) return schema.map(rebase);
  if (schema && typeof schema === "object") {
    return Object.fromEntries(
      Object.entries(schema).map(([key, value]) => [
        key,
        key === "$ref" && typeof value === "string"
          ? value.replace(/^#\//, "openapi.json#/")
          : rebase(value),
      ]),
    );
  }
  return schema;
}

const inlineValidators = new Map<string, ValidateFunction>();

function assertInline(key: string, schema: unknown, data: unknown, context: string): void {
  let validate = inlineValidators.get(key);
  if (!validate) {
    validate = ajv.compile(rebase(schema) as object);
    inlineValidators.set(key, validate);
  }
  if (!validate(data)) {
    const errors = ajv.errorsText(validate.errors, { separator: "; " });
    throw new Error(
      `${context} does not match its schema: ${errors}\n${JSON.stringify(data).slice(0, 700)}`,
    );
  }
}

interface Operation {
  method: string;
  template: string;
  pattern: RegExp;
  op: OpenApiOperation;
}

const OPERATIONS: Operation[] = Object.entries(openapi.paths).flatMap(([template, methods]) =>
  Object.entries(methods).map(([method, op]) => ({
    method: method.toUpperCase(),
    template,
    pattern: new RegExp(`^${template.replace(/\{[^}]+\}/g, "[^/]+")}$`),
    op,
  })),
);

function findOperation(method: string, path: string): Operation | undefined {
  return OPERATIONS.find((o) => o.method === method && o.pattern.test(path));
}

const exercised = new Set<string>();
/** Statuses the backend answers with that openapi.json does not declare (reported, not failed). */
const undeclared = new Set<string>();

function markExercised(method: string, path: string): Operation | undefined {
  const operation = findOperation(method, path);
  if (operation) exercised.add(`${operation.method} ${operation.template}`);
  return operation;
}

function assertConforms(
  method: string,
  path: string,
  status: number,
  contentType: string,
  body: unknown,
): void {
  const operation = markExercised(method, path);
  if (!operation) return; // an unknown route: FastAPI's own 404 / 405
  const kind = contentType.split(";")[0]?.trim() ?? "";
  const label = `${method} ${path} -> ${status}`;
  expect(status, `${label} must not be a server error`).toBeLessThan(500);

  if (status < 400) {
    const declared = operation.op.responses[String(status)];
    expect(declared, `${label}: status is not declared in openapi.json`).toBeDefined();
    const schema = declared?.content?.[kind]?.schema;
    if (kind === "application/json") {
      expect(schema, `${label}: JSON response is not declared`).toBeDefined();
      assertInline(`${operation.template}|${method}|${status}`, schema, body, label);
    }
    return;
  }
  if (kind === "application/problem+json") {
    assertSchema("Problem", body, label);
    expect((body as Problem).status).toBe(status);
  } else if (status === 422) {
    expect(kind, label).toBe("application/json");
    assertSchema("HTTPValidationError", body, label);
  } else {
    expect(body, label).toEqual(expect.objectContaining({ detail: expect.any(String) }));
  }
  if (!operation.op.responses[String(status)])
    undeclared.add(`${operation.method} ${operation.template} ${status}`);
}

// --- the event contract, read from openapi.json -------------------------------------------------------

const historyItems = (
  openapi.paths["/v1/batches/{batch_id}/history"]?.get?.responses["200"]?.content?.[
    "application/json"
  ]?.schema as {
    items: { discriminator: { mapping: Record<string, string> }; oneOf: Array<{ $ref: string }> };
  }
).items;

const EVENT_SCHEMAS: Record<string, string> = Object.fromEntries(
  Object.entries(historyItems.discriminator.mapping).map(([type, ref]) => [
    type,
    ref.split("/").at(-1) ?? "",
  ]),
);

function assertEvent(event: PipelineEvent): void {
  const name = EVENT_SCHEMAS[event.type];
  expect(name, `unknown event type ${event.type}`).toBeDefined();
  assertSchema(name ?? "", event, `event ${event.type}`);
}

// --- a tiny HTTP client ------------------------------------------------------------------------------------

let api: MockApi;

interface Reply<T> {
  status: number;
  headers: Headers;
  body: T;
  text: string;
  bytes: Buffer;
}

interface FileSpec {
  name: string;
  data: Uint8Array;
  type?: string;
}

function formOf(files: FileSpec[], field = "files"): FormData {
  const form = new FormData();
  for (const file of files) {
    form.append(
      field,
      new File([file.data as BlobPart], file.name, { type: file.type ?? "" }),
      file.name,
    );
  }
  return form;
}

async function request<T>(
  persona: string | null,
  method: "GET" | "POST",
  path: string,
  init: { json?: unknown; body?: BodyInit; headers?: Record<string, string> } = {},
): Promise<Reply<T>> {
  const headers: Record<string, string> = { ...init.headers };
  if (persona) headers["X-Persona"] = persona;
  if (init.json !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`${api.url}${path}`, {
    method,
    headers,
    body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
  });
  const bytes = Buffer.from(await res.arrayBuffer());
  const contentType = res.headers.get("content-type") ?? "";
  const isJson = contentType.includes("json");
  const text = isJson ? bytes.toString("utf8") : "";
  const body = (isJson ? JSON.parse(text) : bytes) as T;
  assertConforms(method, path.split("?")[0] ?? path, res.status, contentType, body);
  return { status: res.status, headers: res.headers, body, text, bytes };
}

function as(persona: string | null) {
  return {
    get: <T = unknown>(path: string, headers?: Record<string, string>) =>
      request<T>(persona, "GET", path, { headers }),
    post: <T = unknown>(path: string, json?: unknown, headers?: Record<string, string>) =>
      request<T>(persona, "POST", path, { json, headers }),
    upload: (files: FileSpec[]) =>
      request<S["BatchCreated"]>(persona, "POST", "/v1/batches", { body: formOf(files) }),
    raw: <T = unknown>(
      method: "GET" | "POST",
      path: string,
      init?: { json?: unknown; body?: BodyInit; headers?: Record<string, string> },
    ) => request<T>(persona, method, path, init),
  };
}

// --- server-sent events --------------------------------------------------------------------------------------

interface ParsedEvent {
  id: number;
  type: string;
  data: PipelineEvent;
}

function parseSse(text: string) {
  const frames = text.split("\n\n").filter((frame) => frame.length > 0);
  const comments: string[] = [];
  const events: ParsedEvent[] = [];
  for (const frame of frames) {
    if (frame.startsWith(":")) {
      comments.push(frame);
      continue;
    }
    const lines = frame.split("\n");
    expect(lines, `frame ${JSON.stringify(frame)}`).toHaveLength(3);
    const [idLine = "", eventLine = "", dataLine = ""] = lines;
    expect(idLine).toMatch(/^id: \d+$/);
    expect(eventLine).toMatch(/^event: [a-z_]+$/);
    expect(dataLine.startsWith("data: ")).toBe(true);
    const raw = dataLine.slice("data: ".length);
    const data = JSON.parse(raw) as PipelineEvent;
    expect(raw, "data is compact JSON like pydantic's model_dump_json").toBe(JSON.stringify(data));
    expect(eventLine).toBe(`event: ${data.type}`);
    events.push({ id: Number(idLine.slice(4)), type: data.type, data });
  }
  return { frames, comments, events };
}

async function readEvents(persona: string, path: string, lastEventId?: string) {
  const headers: Record<string, string> = { "X-Persona": persona, Accept: "text/event-stream" };
  if (lastEventId !== undefined) headers["Last-Event-ID"] = lastEventId;
  const res = await fetch(`${api.url}${path}`, { headers, signal: AbortSignal.timeout(30_000) });
  const text = await res.text();
  markExercised("GET", path);
  return {
    status: res.status,
    headers: res.headers,
    text,
    ...parseSse(res.status === 200 ? text : ""),
  };
}

/** A request with arbitrary headers (fetch may refuse to send Origin), through node:http. */
function rawRequest(method: string, path: string, headers: Record<string, string>) {
  return new Promise<{ status: number; headers: http.IncomingHttpHeaders }>((resolve, reject) => {
    const req = http.request(`${api.url}${path}`, { method, headers }, (res) => {
      res.resume();
      res.on("end", () => resolve({ status: res.statusCode ?? 0, headers: res.headers }));
    });
    req.on("error", reject);
    req.end();
  });
}

/** The events of a live response as they arrive. */
async function* streamed(res: Response): AsyncGenerator<PipelineEvent> {
  const decoder = new TextDecoder();
  let pending = "";
  for await (const chunk of res.body as unknown as AsyncIterable<Uint8Array>) {
    pending += decoder.decode(chunk, { stream: true });
    for (let end = pending.indexOf("\n\n"); end >= 0; end = pending.indexOf("\n\n")) {
      const frame = pending.slice(0, end);
      pending = pending.slice(end + 2);
      const data = frame.split("\n").find((line) => line.startsWith("data: "));
      if (data) yield JSON.parse(data.slice("data: ".length)) as PipelineEvent;
    }
  }
}

// --- fixtures ----------------------------------------------------------------------------------------------------

function sample(name: string): Buffer {
  return readFileSync(new URL(name, SAMPLES_DIR));
}

const SAMPLE_FILES = [
  "fuel-slip-agni-petroleum.png",
  "handwritten-bill-gupta-provision.png",
  "cab-receipt-raahi-cabs.png",
  "restaurant-bill-mehfil-cafe.png",
  "invoice-learnsphere-course.pdf",
  "upi-payment-auto-fare.png",
  "flight-ticket-suryoday-air.png",
  "hotel-folio-lotus-bay.png",
  "mobile-bill-nakshatra.pdf",
  "invoice-prakash-computer-world.pdf",
];

/** The ten samples in demo order, plus (by default) the cab receipt again under another name. */
function sampleFiles(withCopy = true): FileSpec[] {
  const files = SAMPLE_FILES.map((name) => ({ name, data: sample(name) }));
  if (withCopy)
    files.push({ name: "cab-receipt-copy.png", data: sample("cab-receipt-raahi-cabs.png") });
  return files;
}

const TINY_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);
const pngVariant = (n: number) => Buffer.concat([TINY_PNG, Buffer.from(`variant-${n}`)]);
const pdfVariant = (n: number) =>
  Buffer.from(
    `%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n${n}`,
  );
const jpegVariant = (n: number) =>
  Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10]), Buffer.from(`JFIF-${n}`)]);
const webpVariant = (n: number) =>
  Buffer.concat([Buffer.from("RIFF"), Buffer.from([0x1e, 0, 0, 0]), Buffer.from(`WEBPVP8 ${n}`)]);

async function runBatch(persona: string, files: FileSpec[]) {
  const client = as(persona);
  const created = await client.upload(files);
  expect(created.status).toBe(202);
  const id = created.body.batch_id;
  const sse = await readEvents(persona, created.body.events_url);
  const batch = await client.get<BatchView>(`/v1/batches/${id}`);
  const history = await client.get<PipelineEvent[]>(`/v1/batches/${id}/history`);
  return { created: created.body, sse, batch: batch.body, history: history.body };
}

type Run = Awaited<ReturnType<typeof runBatch>>;

function claimOf(run: Run, titlePrefix: string): ClaimView {
  const claim = run.batch.claims.find((c) => c.title.startsWith(titlePrefix));
  if (!claim) throw new Error(`no claim starting with "${titlePrefix}"`);
  return claim;
}

/** The index of a generated file (see `pngVariant`) whose scripted reading satisfies `matches`. */
function findVariant(matches: (plan: ReturnType<typeof generatePlan>) => boolean): number {
  const today = new Date().toISOString().slice(0, 10);
  for (let i = 5000; i < 25_000; i += 1) {
    const plan = generatePlan(sha256Hex(pngVariant(i)), { mediaType: "image/png", today });
    if (matches(plan)) return i;
  }
  throw new Error("no generated file matches");
}

const codes = (findings: Finding[] | undefined) =>
  (findings ?? []).map((f) => `${f.code}:${f.severity}`);

beforeAll(async () => {
  api = await createMockApi({ port: 0, speed: 50 });
});

afterAll(async () => {
  await api.close();
});

// =====================================================================================================
describe("health, meta and personas", () => {
  it("serves /healthz, /readyz, /v1/employees and /v1/meta without a persona", async () => {
    const anon = as(null);
    expect((await anon.get<S["Health"]>("/healthz")).body).toEqual({
      status: "ok",
      version: "0.1.0",
    });
    const ready = await anon.get<S["Readiness"]>("/readyz");
    expect(ready.body.status).toBe("ready");
    expect(ready.body.checks).toEqual({ redis: "ok", postgres: "ok" });

    const employees = await anon.get<S["Employee"][]>("/v1/employees");
    expect(employees.body.map((e) => e.id)).toEqual([ASHA, P001, P005, MEERA, RAVI]);
    expect(employees.body[0]).toEqual({
      id: "DEMO-ASHA",
      name: "Asha Menon",
      employee_id: "EMP90001",
      grade: "L3",
      base_city: "Pune",
      base_state_code: "27",
    });
    expect(employees.body.map((e) => `${e.name}/${e.grade}/${e.base_city}`)).toEqual([
      "Asha Menon/L3/Pune",
      "Advika Hayer/L4/Hyderabad",
      "Advik Kaur/L4/Panaji",
      "Meera Shah/L2/Mumbai",
      "Ravi Iyer/L5/Bengaluru",
    ]);

    const meta = await anon.get<S["MetaInfo"]>("/v1/meta");
    expect(meta.body.llm_mode).toBe("replay");
    expect(meta.body.decision_engine).toBe("jev");
    expect(meta.body).toMatchObject({ demo: true, runtime: "embedded" });
    expect(meta.body.routes.map((r) => r.route)).toEqual([
      "extraction",
      "extraction_retry",
      "agent_chat",
      "question_draft",
      "decision_fallback",
      "reply_parse",
      "locate",
      "policy_compile",
      "eval_judge",
    ]);
    expect(meta.body.routes[0]).toEqual({
      route: "extraction",
      model_key: "haiku",
      model_id: "claude-haiku-5-5",
      effort: "low",
      overridden: false,
    });
    expect(meta.body.routes.find((r) => r.route === "policy_compile")).toMatchObject({
      model_key: "opus",
      model_id: "claude-opus-5-5",
      effort: "high",
    });
    expect(meta.body.routes.every((r) => r.overridden === false)).toBe(true);
  });

  it("answers /v1/me and marks only DEMO-RAVI as approver", async () => {
    const asha = await as(ASHA).get<S["Me"]>("/v1/me");
    expect(asha.body.employee.id).toBe(ASHA);
    expect(asha.body.is_approver).toBe(false);
    const ravi = await as(RAVI).get<S["Me"]>("/v1/me");
    expect(ravi.body.is_approver).toBe(true);
    expect(ravi.body.employee.grade).toBe("L5");
  });

  const PROTECTED: Array<[string, string, unknown?]> = [
    ["GET", "/v1/me"],
    ["GET", "/v1/stats"],
    ["GET", "/v1/claims"],
    ["GET", "/v1/claims/clm-x"],
    ["GET", "/v1/claims/clm-x/prompt"],
    ["POST", "/v1/claims/clm-x/answers", { answers: {} }],
    ["POST", "/v1/claims/clm-x/reply", { text: "x" }],
    ["POST", "/v1/claims/clm-x/submit", { confirmed: true }],
    ["POST", "/v1/claims/clm-x/decision", { approved: true }],
    ["GET", "/v1/approvals"],
    ["POST", "/v1/batches"],
    ["GET", "/v1/batches/bat-x"],
    ["GET", "/v1/batches/bat-x/history"],
    ["GET", "/v1/batches/bat-x/events"],
    ["GET", "/v1/documents/doc-x"],
    ["GET", "/v1/documents/doc-x/file"],
    ["POST", "/v1/demo/reset"],
  ];

  it.each(PROTECTED)(
    "rejects %s %s without or with an unknown persona (401)",
    async (method, path, json) => {
      const missing = await as(null).raw<Problem>(method as "GET" | "POST", path, {
        json,
        body:
          method === "POST" && json === undefined
            ? formOf([{ name: "a.png", data: TINY_PNG }])
            : undefined,
      });
      expect(missing.status).toBe(401);
      expect(missing.headers.get("content-type")).toBe("application/problem+json");
      expect(missing.body).toEqual({
        type: "missing_persona",
        title: "Send an X-Persona header",
        status: 401,
      });
      const unknown = await as("NOBODY").raw<Problem>(method as "GET" | "POST", path, { json });
      expect(unknown.status).toBe(401);
      expect(unknown.body).toEqual({
        type: "unknown_persona",
        title: "Unknown persona NOBODY",
        status: 401,
      });
    },
  );

  it("answers FastAPI-style errors for unknown routes and methods", async () => {
    const missing = await as(ASHA).get<{ detail: string }>("/v1/nope");
    expect(missing.status).toBe(404);
    expect(missing.body).toEqual({ detail: "Not Found" });
    const wrong = await as(ASHA).raw<{ detail: string }>("POST", "/v1/me", { json: {} });
    expect(wrong.status).toBe(405);
    expect(wrong.body).toEqual({ detail: "Method Not Allowed" });
  });
});

// =====================================================================================================
describe("the seed", () => {
  beforeAll(() => api.reset());

  it("gives DEMO-ASHA exactly one claim, answered from the calendar", async () => {
    const list = await as(ASHA).get<ClaimView[]>("/v1/claims");
    expect(list.body).toHaveLength(1);
    const [claim] = list.body;
    expect(claim).toMatchObject({
      title: "Client dinner 6 Oct 2026",
      mode: "event",
      status: "ready",
      route: "auto_approve",
      city: "Pune",
      employee_id: ASHA,
      submission_reference: null,
      document_ids: ["doc-seed-06"],
      batch_id: "bat-seed-06",
      total: 829.5,
      start_date: "2026-10-06",
      end_date: "2026-10-06",
      findings: [],
    });
    expect(claim?.open_questions.map((q) => q.answer)).toEqual([
      "from calendar: Dinner with Kestrel Logistics (attendees: Neha Rao (Kestrel Logistics), Rohan Kapoor (Kestrel Logistics), Priya Nair (Kestrel Logistics))",
      "from calendar: Dinner with Kestrel Logistics",
    ]);
    const prompt = await as(ASHA).get<S["PromptOut"]>(`/v1/claims/${claim?.id}/prompt`);
    expect(prompt.body).toEqual({ prompt: null, open_question_ids: [] });
  });

  it("seeds the other personas' history", async () => {
    const p005 = await as(P005).get<ClaimView[]>("/v1/claims");
    expect(p005.body.map((c) => [c.title, c.status, c.submission_reference, c.route])).toEqual([
      ["Hyderabad trip 7 Aug 2026", "submitted", "FIN-2026-000001", "auto_approve"],
    ]);
    expect(p005.body[0]?.total).toBe(8875.95);
    expect(p005.body[0]?.findings).toEqual([]);
    expect(p005.body[0]?.open_questions[0]?.answer).toBe(
      "Customer workshop at the Hyderabad office",
    );

    const meera = await as(MEERA).get<ClaimView[]>("/v1/claims");
    expect(meera.body.map((c) => [c.title, c.status, c.submission_reference, c.route])).toEqual([
      ["WFH supplies Aug 2026", "rejected", "FIN-2026-000005", "finance_review"],
      ["Chandigarh trip 15\u201318 Aug 2026", "submitted", "FIN-2026-000002", "finance_review"],
    ]);
    const hotel = meera.body[1];
    expect(codes(hotel?.findings)).toEqual(["items_subtotal_mismatch:high", "hotel_over_cap:high"]);
    expect(hotel?.findings[1]?.message).toBe(
      "Hotel night \u20b97,700 exceeds the \u20b95,500 cap for grade L2 in a Tier-2 city (3 of 3 nights are over).",
    );
    expect(codes(meera.body[0]?.findings)).toEqual([
      "total_mismatch:high",
      "gst_rate_mismatch:warn",
    ]);

    const p001 = await as(P001).get<ClaimView[]>("/v1/claims");
    expect(
      p001.body.map((c) => [c.title, c.status, c.submission_reference, c.route, c.city]),
    ).toEqual([
      ["Fuel Jul 2026", "approved", "FIN-2026-000004", "auto_approve", "Hyderabad"],
      ["Learning 18 Jul 2026", "submitted", "FIN-2026-000003", "finance_review", "Hyderabad"],
    ]);
    expect(codes(p001.body[1]?.findings)).toEqual(["preapproval_required:warn"]);
  });

  it("lists the approvals queue newest first and filters by status", async () => {
    const queue = await as(RAVI).get<ClaimView[]>("/v1/approvals");
    expect(queue.body.map((c) => c.title)).toEqual([
      "Learning 18 Jul 2026",
      "Chandigarh trip 15\u201318 Aug 2026",
      "Hyderabad trip 7 Aug 2026",
    ]);
    const approved = await as(RAVI).get<ClaimView[]>("/v1/approvals?status=approved");
    expect(approved.body.map((c) => c.title)).toEqual(["Fuel Jul 2026"]);
    const rejected = await as(RAVI).get<ClaimView[]>("/v1/approvals?status=rejected");
    expect(rejected.body.map((c) => c.title)).toEqual(["WFH supplies Aug 2026"]);

    const denied = await as(ASHA).get<Problem>("/v1/approvals");
    expect(denied.status).toBe(403);
    expect(denied.body).toEqual({
      type: "approver_only",
      title: "Only approvers can do this",
      status: 403,
    });
  });

  it("lets approvers read everything and everyone else only their own", async () => {
    const all = await as(RAVI).get<ClaimView[]>("/v1/claims");
    expect(all.body).toHaveLength(6);
    const filtered = await as(RAVI).get<ClaimView[]>(`/v1/claims?employee_id=${P001}`);
    expect(filtered.body.map((c) => c.employee_id)).toEqual([P001, P001]);
    const ignored = await as(ASHA).get<ClaimView[]>(`/v1/claims?employee_id=${P001}`);
    expect(ignored.body.map((c) => c.employee_id)).toEqual([ASHA]);
    const byStatus = await as(RAVI).get<ClaimView[]>(
      "/v1/claims?status=submitted&route=finance_review",
    );
    expect(byStatus.body.map((c) => c.title)).toEqual([
      "Learning 18 Jul 2026",
      "Chandigarh trip 15\u201318 Aug 2026",
    ]);

    const claimId = all.body.find((c) => c.employee_id === P005)?.id ?? "";
    for (const persona of [ASHA, P001, MEERA]) {
      const hidden = await as(persona).get<Problem>(`/v1/claims/${claimId}`);
      expect(hidden.status).toBe(404);
      expect(hidden.body).toEqual({ type: "claim_not_found", title: "No such claim", status: 404 });
    }
    expect((await as(RAVI).get<ClaimView>(`/v1/claims/${claimId}`)).status).toBe(200);
    expect((await as(P005).get<ClaimView>(`/v1/claims/${claimId}`)).status).toBe(200);
  });

  it("serves the seeded documents with their evidence image", async () => {
    const doc = await as(RAVI).get<DocumentView>("/v1/documents/doc-seed-02");
    expect(doc.body).toMatchObject({
      id: "doc-seed-02",
      filename: "hotel-folio-lotus-bay.png",
      position: 0,
      status: "processed",
      error: null,
      trust_score: 60,
      verdict: "block",
    });
    expect(doc.body.document?.sha256).toMatch(/^[0-9a-f]{64}$/);
    const file = await as(RAVI).get<Buffer>("/v1/documents/doc-seed-02/file");
    expect(file.status).toBe(200);
    expect(file.bytes.equals(sample("hotel-folio-lotus-bay.png"))).toBe(true);

    const hidden = await as(ASHA).get<Problem>("/v1/documents/doc-seed-02");
    expect(hidden.status).toBe(404);
    expect(hidden.body.type).toBe("document_not_found");
    const hiddenFile = await as(ASHA).get<Problem>("/v1/documents/doc-seed-02/file");
    expect(hiddenFile.body).toEqual({
      type: "document_not_found",
      title: "No such document",
      status: 404,
    });
    const missing = await as(RAVI).get<Problem>("/v1/documents/doc-404");
    expect(missing.body.type).toBe("document_not_found");

    const batch = await as(RAVI).get<BatchView>("/v1/batches/bat-seed-02");
    expect(batch.body).toMatchObject({ status: "done", total: 1, processed: 1, failed: 0 });
    expect(batch.body.claims).toHaveLength(1);
    const hiddenBatch = await as(ASHA).get<Problem>("/v1/batches/bat-seed-02");
    expect(hiddenBatch.body.type).toBe("batch_not_found");
    const history = await as(RAVI).get<PipelineEvent[]>("/v1/batches/bat-seed-02/history");
    expect(history.body.map((e) => e.type)).toEqual([
      "batch_started",
      "document_extracted",
      "document_checked",
      "claims_ready",
      "batch_done",
    ]);
    history.body.forEach(assertEvent);
  });

  it("starts the statistics from the seeded history", async () => {
    const stats = await as(ASHA).get<S["Stats"]>("/v1/stats");
    expect(stats.body).toMatchObject({
      documents_processed: 53,
      documents_failed: 2,
      claims: 15,
      claims_by_status: { approved: 6, rejected: 3, submitted: 4, ready: 2 },
      auto_approvable_claims: 7,
      llm_calls: 53,
      avg_batch_seconds: 9.4,
      assumed_manual_minutes_per_document: 4,
      estimated_minutes_saved: 212,
    });
    expect(stats.body.llm_cost_usd).toBeGreaterThan(0.0211);
    expect(stats.body.llm_cost_per_document_usd).toBeCloseTo(stats.body.llm_cost_usd / 53, 6);
  });
});

// =====================================================================================================
describe("uploading the sample set as DEMO-ASHA", () => {
  let run: Run;

  beforeAll(async () => {
    api.reset();
    run = await runBatch(ASHA, sampleFiles());
  });

  it("accepts the upload with 202 and deterministic ids", () => {
    expect(run.created.batch_id).toBe("bat-000001");
    expect(run.created.status).toBe("queued");
    expect(run.created.events_url).toBe("/v1/batches/bat-000001/events");
    expect(run.created.documents.map((d) => d.id)).toEqual(
      Array.from({ length: 11 }, (_, i) => `doc-${String(i + 1).padStart(6, "0")}`),
    );
    expect(run.created.documents.map((d) => d.filename)).toEqual([
      ...SAMPLE_FILES,
      "cab-receipt-copy.png",
    ]);
  });

  it("streams the events with the backend's framing, in order", () => {
    const { sse } = run;
    expect(sse.status).toBe(200);
    expect(sse.headers.get("content-type")).toBe("text/event-stream; charset=utf-8");
    expect(sse.headers.get("cache-control")).toBe("no-cache");
    expect(sse.headers.get("x-accel-buffering")).toBe("no");
    expect(sse.events).toHaveLength(25);
    expect(sse.events.map((e) => e.id)).toEqual(Array.from({ length: 25 }, (_, i) => i));
    sse.events.forEach((e) => assertEvent(e.data));
    sse.frames.forEach((frame, i) => {
      expect(frame.startsWith(`id: ${i}\nevent: `)).toBe(true);
    });

    const types = sse.events.map((e) => e.type);
    expect(types[0]).toBe("batch_started");
    expect(types.slice(-2)).toEqual(["claims_ready", "batch_done"]);
    expect(types.slice(1, -2)).toEqual(
      Array.from({ length: 11 }, () => ["document_extracted", "document_checked"]).flat(),
    );
    const first = sse.events[0]?.data as S["BatchStarted"];
    expect(first).toEqual({ batch_id: "bat-000001", type: "batch_started", total: 11 });

    const extracted = sse.events
      .filter((e) => e.type === "document_extracted")
      .map((e) => e.data as S["DocumentExtracted"]);
    expect(extracted.map((e) => e.position)).toEqual([...Array(11).keys()]);
    expect(extracted.map((e) => e.document_id)).toEqual(run.created.documents.map((d) => d.id));
    // every document is checked right after it was read, before the next one is read
    sse.events.slice(1, -2).forEach((e, i) => {
      expect(e.type).toBe(i % 2 === 0 ? "document_extracted" : "document_checked");
      expect((e.data as S["DocumentChecked"]).document_id).toBe(
        run.created.documents[Math.floor(i / 2)]?.id,
      );
    });
  });

  it("reads each document as scripted: category, engine, cost, cache", () => {
    const extracted = run.sse.events
      .filter((e) => e.type === "document_extracted")
      .map((e) => e.data as S["DocumentExtracted"]);
    expect(
      extracted.map((e) => [
        e.category,
        e.category_confidence,
        e.engine,
        e.doc_type,
        e.merchant,
        e.total,
      ]),
    ).toEqual([
      ["fuel_vehicle", 0.97, "jev", "fuel_slip", "Agni Petroleum - Gachibowli", 2665.3],
      ["misc", 0.58, "llm", "handwritten_bill", "Gupta Provision Store", 430],
      ["local_conveyance", 0.95, "jev", "cab_receipt", "Raahi Cabs", 672.74],
      ["client_entertainment", 0.78, "llm", "restaurant_bill", "Mehfil Cafe", 829.5],
      ["learning", 0.93, "jev", "gst_invoice", "LearnSphere Academy Pvt Ltd", 22420],
      ["local_conveyance", 0.91, "jev", "upi_payment", "CHAMELI GARG", 120],
      ["travel_domestic", 0.98, "jev", "flight_ticket", "Suryoday Air", 8875.95],
      ["accommodation", 0.99, "jev", "hotel_folio", "Lotus Bay Suites", 22680],
      ["mobile_internet", 0.96, "jev", "mobile_bill", "Nakshatra Mobile", 1550.52],
      ["wfh_supplies", 0.88, "jev", "gst_invoice", "Prakash Computer World", 11146.28],
      ["local_conveyance", 0.95, "jev", "cab_receipt", "Raahi Cabs", 672.74],
    ]);
    const fresh = extracted.slice(0, 10);
    for (const e of fresh) {
      expect(e.cached).toBe(false);
      expect(e.cost_usd).toBeGreaterThanOrEqual(0.0004);
      expect(e.cost_usd).toBeLessThanOrEqual(0.0009);
    }
    expect(extracted[10]).toMatchObject({
      cached: true,
      cost_usd: 0,
      filename: "cab-receipt-copy.png",
    });
    const done = run.sse.events.at(-1)?.data as S["BatchDone"];
    expect(done).toMatchObject({ processed: 11, failed: 0, claims: 10 });
    expect(done.cost_usd).toBeCloseTo(
      fresh.reduce((s, e) => s + e.cost_usd, 0),
      6,
    );
    const ready = run.sse.events.at(-2)?.data as S["ClaimsReady"];
    expect(ready.claim_ids).toEqual(run.batch.claims.map((c) => c.id));
  });

  it("checks each document: trust score, verdict and number of findings", () => {
    const checked = run.sse.events
      .filter((e) => e.type === "document_checked")
      .map((e) => e.data as S["DocumentChecked"]);
    expect(checked.map((e) => [e.trust_score, e.verdict, e.findings])).toEqual([
      [97, "clean", 1], // exif_date_mismatch (info)
      [100, "clean", 0],
      [100, "clean", 0],
      [100, "clean", 0],
      [100, "clean", 0],
      [100, "clean", 0],
      [100, "clean", 0],
      [60, "block", 1], // items_subtotal_mismatch
      [60, "block", 1], // prompt_injection
      [45, "block", 2], // total_mismatch + gst_rate_mismatch
      [60, "review", 1], // duplicate_exact
    ]);
    expect(run.batch.documents.map((d) => [d.status, d.trust_score, d.verdict])).toEqual(
      checked.map((e) => ["processed", e.trust_score, e.verdict]),
    );
  });

  it("matches history, a resumed stream and a late joiner with the live stream", async () => {
    expect(run.history).toEqual(run.sse.events.map((e) => e.data));
    const after = await as(ASHA).get<PipelineEvent[]>("/v1/batches/bat-000001/history?after=20");
    expect(after.body).toEqual(run.sse.events.slice(20).map((e) => e.data));
    expect(
      (await as(ASHA).get<PipelineEvent[]>("/v1/batches/bat-000001/history?after=99")).body,
    ).toEqual([]);

    const resumed = await readEvents(ASHA, "/v1/batches/bat-000001/events", "10");
    expect(resumed.events.map((e) => e.id)).toEqual(Array.from({ length: 14 }, (_, i) => i + 11));
    expect(resumed.events.map((e) => e.data)).toEqual(run.sse.events.slice(11).map((e) => e.data));

    const late = await readEvents(ASHA, "/v1/batches/bat-000001/events");
    expect(late.frames.filter((f) => !f.startsWith(":"))).toEqual(
      run.sse.frames.filter((f) => !f.startsWith(":")),
    );
    const junk = await readEvents(ASHA, "/v1/batches/bat-000001/events", "not-a-number");
    expect(junk.events).toHaveLength(25);
    const beyond = await readEvents(ASHA, "/v1/batches/bat-000001/events", "24");
    expect(beyond.events).toEqual([]);
  });

  it("validates the history query like FastAPI", async () => {
    const negative = await as(ASHA).get<S["HTTPValidationError"]>(
      "/v1/batches/bat-000001/history?after=-1",
    );
    expect(negative.status).toBe(422);
    expect(negative.body.detail?.[0]).toMatchObject({
      type: "greater_than_equal",
      loc: ["query", "after"],
    });
    const text = await as(ASHA).get<S["HTTPValidationError"]>(
      "/v1/batches/bat-000001/history?after=abc",
    );
    expect(text.status).toBe(422);
    expect(text.body.detail?.[0]).toMatchObject({ type: "int_parsing", loc: ["query", "after"] });
  });

  it("reports the finished batch", () => {
    expect(run.batch).toMatchObject({
      id: "bat-000001",
      employee_id: ASHA,
      status: "done",
      total: 11,
      processed: 11,
      failed: 0,
      error: null,
    });
    expect(run.batch.finished_at).not.toBeNull();
    expect(Date.parse(run.batch.created_at)).toBeLessThanOrEqual(
      Date.parse(run.batch.finished_at ?? ""),
    );
    expect(run.batch.documents).toHaveLength(11);
    expect(run.batch.claims).toHaveLength(10);
    run.batch.claims.forEach((c) => expect(c.batch_id).toBe("bat-000001"));
  });

  it("groups the pile into the ten scripted claims", () => {
    const view = (c: ClaimView) => ({
      title: c.title,
      mode: c.mode,
      city: c.city,
      status: c.status,
      route: c.route,
      start: c.start_date,
      end: c.end_date,
      total: c.total,
      docs: c.document_ids,
      questions: c.open_questions.map((q) => q.kind),
      findings: codes(c.findings),
    });
    expect(run.batch.claims.map(view)).toEqual([
      {
        title: "Fuel Jul 2026",
        mode: "period",
        city: "Hyderabad",
        status: "ready",
        route: "auto_approve",
        start: "2026-07-04",
        end: "2026-07-04",
        total: 2665.3,
        docs: ["doc-000001"],
        questions: [],
        findings: ["exif_date_mismatch:info"],
      },
      {
        title: "Miscellaneous Jul 2026",
        mode: "period",
        city: "Hyderabad",
        status: "needs_info",
        route: "finance_review",
        start: "2026-07-06",
        end: "2026-07-06",
        total: 430,
        docs: ["doc-000002"],
        questions: ["confirm_personal"],
        findings: ["personal_expense_flagged:warn"],
      },
      {
        title: "Client dinner 12 Jul 2026",
        mode: "event",
        city: "Bengaluru",
        status: "needs_info",
        route: "finance_review",
        start: "2026-07-12",
        end: "2026-07-12",
        total: 829.5,
        docs: ["doc-000004"],
        questions: ["attendees", "business_purpose"],
        findings: [],
      },
      {
        title: "Bengaluru trip 12 Jul 2026",
        mode: "trip",
        city: "Bengaluru",
        status: "needs_info",
        route: "finance_review",
        start: "2026-07-12",
        end: "2026-07-12",
        total: 1345.48,
        docs: ["doc-000003", "doc-000011"],
        questions: ["business_purpose"],
        findings: ["duplicate_exact:high"],
      },
      {
        title: "Learning 18 Jul 2026",
        mode: "event",
        city: "Pune",
        status: "ready",
        route: "finance_review",
        start: "2026-07-18",
        end: "2026-07-18",
        total: 22420,
        docs: ["doc-000005"],
        questions: [],
        findings: ["preapproval_required:warn"],
      },
      {
        title: "Local conveyance Jul 2026",
        mode: "period",
        city: "Pune",
        status: "ready",
        route: "auto_approve",
        start: "2026-07-21",
        end: "2026-07-21",
        total: 120,
        docs: ["doc-000006"],
        questions: [],
        findings: [],
      },
      {
        title: "Hyderabad trip 7 Aug 2026",
        mode: "trip",
        city: "Hyderabad",
        status: "needs_info",
        route: "finance_review",
        start: "2026-08-07",
        end: "2026-08-07",
        total: 8875.95,
        docs: ["doc-000007"],
        questions: ["business_purpose"],
        findings: [],
      },
      {
        title: "Chandigarh trip 15\u201318 Aug 2026",
        mode: "trip",
        city: "Chandigarh",
        status: "needs_info",
        route: "finance_review",
        start: "2026-08-15",
        end: "2026-08-18",
        total: 22680,
        docs: ["doc-000008"],
        questions: ["business_purpose"],
        findings: ["items_subtotal_mismatch:high", "hotel_over_cap:high"],
      },
      {
        title: "WFH supplies Aug 2026",
        mode: "period",
        city: "Panaji",
        status: "ready",
        route: "finance_review",
        start: "2026-08-19",
        end: "2026-08-19",
        total: 11146.28,
        docs: ["doc-000010"],
        questions: [],
        findings: ["total_mismatch:high", "gst_rate_mismatch:warn"],
      },
      {
        title: "Mobile & internet Sep 2026",
        mode: "period",
        city: "Mumbai",
        status: "ready",
        route: "finance_review",
        start: "2026-09-13",
        end: "2026-09-13",
        total: 1550.52,
        docs: ["doc-000009"],
        questions: [],
        findings: ["prompt_injection:high"],
      },
    ]);
    for (const claim of run.batch.claims) {
      expect(claim.id).toMatch(/^clm-DEMO-ASHA-[0-9a-f]{10}$/);
      expect(claim.currency).toBe("INR");
      expect(claim.submission_reference).toBeNull();
    }
  });

  it("asks exactly the scripted questions, with the backend's wording", () => {
    const text = (prefix: string) => claimOf(run, prefix).open_questions.map((q) => q.text);
    expect(text("Bengaluru trip")).toEqual([
      "What was the business purpose of the Bengaluru trip 12 Jul 2026?",
    ]);
    expect(text("Client dinner")).toEqual([
      "Who attended the client dinner on 12 Jul (\u20b9830)? Please give names and company.",
      "What was the business purpose of the client dinner on 12 Jul (\u20b9830)?",
    ]);
    expect(text("Miscellaneous")).toEqual([
      "The \u20b9430 handwritten bill from \u201cGupta Provision Store\u201d looks personal. Is it a business expense? If yes, say what it was for; if not, I will leave it out.",
    ]);
    expect(text("Hyderabad trip")).toEqual([
      "What was the business purpose of the Hyderabad trip 7 Aug 2026?",
    ]);
    expect(text("Chandigarh trip")).toEqual([
      "What was the business purpose of the Chandigarh trip 15\u201318 Aug 2026?",
    ]);
    for (const claim of run.batch.claims) {
      for (const q of claim.open_questions) {
        expect(q.id).toMatch(/^q-(attendees|business_purpose|confirm_personal)-[0-9a-f]{8}$/);
        expect(q.answer).toBeNull();
        expect(q.document_ids.length).toBeGreaterThan(0);
      }
    }
    expect(claimOf(run, "Bengaluru trip").open_questions[0]?.document_ids).toEqual([
      "doc-000003",
      "doc-000011",
    ]);
  });

  it("quotes the policy clauses and the evidence in its findings", () => {
    const learning = claimOf(run, "Learning").findings[0];
    expect(learning).toEqual({
      code: "preapproval_required",
      severity: "warn",
      message:
        "This learning expense is \u20b922,420, above \u20b910,000, so it needs your manager's approval from before the purchase.",
      fields: ["total"],
      expected: 10000,
      actual: 22420,
      source: "policy",
      clause_id: "9.1",
      clause_text: POLICY_CLAUSES["9.1"]?.text,
      document_id: "doc-000005",
    });
    expect(learning?.clause_text).toBe(
      "A course, certification or similar learning expense above \u20b910,000 needs your manager's approval before you buy it. Attach the approval to the claim.",
    );

    const personal = claimOf(run, "Miscellaneous").findings[0];
    expect(personal).toMatchObject({
      code: "personal_expense_flagged",
      severity: "warn",
      source: "policy",
      clause_id: "10.1",
      clause_text:
        "Personal expenses are not reimbursable, even when they are paid during a business trip.",
      actual: 430,
      document_id: "doc-000002",
    });

    const [items, cap] = claimOf(run, "Chandigarh").findings;
    expect(items).toEqual({
      code: "items_subtotal_mismatch",
      severity: "high",
      message: "Line items add up to \u20b922,100.00 but the subtotal says \u20b921,600.00.",
      fields: ["line_items", "subtotal"],
      expected: 22100,
      actual: 21600,
      source: "trust",
      clause_id: null,
      clause_text: null,
      document_id: "doc-000008",
    });
    expect(cap).toMatchObject({
      code: "hotel_over_cap",
      severity: "high",
      source: "policy",
      message: "Hotel night \u20b97,700 exceeds the \u20b97,500 cap for grade L3 in a Tier-2 city.",
      expected: 7500,
      actual: 7700,
      clause_id: "4.1",
      clause_text: POLICY_CLAUSES["4.1"]?.text,
    });

    const wfh = claimOf(run, "WFH").findings;
    expect(wfh[0]).toMatchObject({
      code: "total_mismatch",
      message:
        "The printed total \u20b911,146.28 doesn't match the bill's own items, taxes and charges (\u20b912,846.56).",
      expected: 12846.56,
      actual: 11146.28,
      fields: ["total"],
    });
    expect(wfh[1]).toMatchObject({
      code: "gst_rate_mismatch",
      severity: "warn",
      message: "Tax charged is 36.0% of the subtotal, but the bill states 18% GST.",
      expected: 18,
      actual: 36,
    });

    expect(claimOf(run, "Mobile").findings[0]).toMatchObject({
      code: "prompt_injection",
      severity: "high",
      message:
        "The document contains text addressed to an AI reviewer or automated system (for example a request to approve the claim or skip checks). It was treated as part of the document and ignored, and the bill needs a human review.",
      actual: "flagged by the extractor",
      document_id: "doc-000009",
    });
    expect(claimOf(run, "Fuel").findings[0]).toMatchObject({
      code: "exif_date_mismatch",
      severity: "info",
      message:
        "The photo was taken on 06 Jul 2026, 2 days after the date printed on the bill (04 Jul 2026).",
    });
    expect(claimOf(run, "Bengaluru trip").findings[0]).toMatchObject({
      code: "duplicate_exact",
      severity: "high",
      message: "This exact file was already uploaded as document doc-000003.",
      actual: "doc-000003",
      document_id: "doc-000011",
    });
  });

  it("serves the extracted document with its boxes and trust-only findings", async () => {
    const hotel = await as(ASHA).get<DocumentView>("/v1/documents/doc-000008");
    expect(hotel.body.document?.findings.map((f) => f.code)).toEqual(["items_subtotal_mismatch"]);
    expect(hotel.body.document?.findings[0]?.document_id).toBeNull();
    expect(hotel.body.document?.decisions).toEqual({
      category: "accommodation",
      category_confidence: 0.99,
      alcohol_present: 0.01,
      personal_expense: 0.02,
      engine: "jev",
    });
    expect(hotel.body.document?.receipt.merchant_name).toBe("Lotus Bay Suites");
    for (const id of ["doc-000008", "doc-000003", "doc-000007"]) {
      const doc = await as(ASHA).get<DocumentView>(`/v1/documents/${id}`);
      const boxes = doc.body.document?.boxes ?? {};
      for (const field of [
        "merchant_name",
        "merchant_gstin",
        "invoice_number",
        "date",
        "subtotal",
        "total",
        "line_items",
      ]) {
        const box = boxes[field];
        expect(box, `${id} ${field}`).toBeDefined();
        expect(box?.page).toBe(0);
        expect((box?.x ?? 0) + (box?.w ?? 0)).toBeLessThanOrEqual(1);
        expect((box?.y ?? 0) + (box?.h ?? 0)).toBeLessThanOrEqual(1);
      }
    }
    const fuel = await as(ASHA).get<DocumentView>("/v1/documents/doc-000001");
    expect(fuel.body.document?.boxes).toEqual({});
    const handwritten = await as(ASHA).get<DocumentView>("/v1/documents/doc-000002");
    expect(handwritten.body.document?.receipt).toMatchObject({
      handwritten: true,
      low_confidence_fields: ["date", "line_items"],
    });
    const restaurant = await as(ASHA).get<DocumentView>("/v1/documents/doc-000004");
    expect(restaurant.body.document?.receipt).toMatchObject({
      languages: ["en", "hi"],
      low_confidence_fields: ["line_items"],
    });
    const mobile = await as(ASHA).get<DocumentView>("/v1/documents/doc-000009");
    expect(mobile.body.document?.receipt.contains_instructions).toBe(true);
  });

  it("serves the original file with the backend's headers", async () => {
    const png = await as(ASHA).get<Buffer>("/v1/documents/doc-000003/file");
    expect(png.status).toBe(200);
    expect(png.headers.get("content-type")).toBe("image/png");
    expect(png.headers.get("cache-control")).toBe("private, max-age=3600");
    expect(png.headers.get("x-content-type-options")).toBe("nosniff");
    expect(png.headers.get("content-disposition")).toBe("inline");
    expect(png.bytes.equals(sample("cab-receipt-raahi-cabs.png"))).toBe(true);
    const pdf = await as(RAVI).get<Buffer>("/v1/documents/doc-000005/file");
    expect(pdf.headers.get("content-type")).toBe("application/pdf");
    expect(pdf.bytes.equals(sample("invoice-learnsphere-course.pdf"))).toBe(true);
  });

  it("hides the batch, claims and documents from other personas but not from the approver", async () => {
    const claimId = run.batch.claims[0]?.id ?? "";
    for (const persona of [P001, P005, MEERA]) {
      const client = as(persona);
      expect((await client.get<Problem>("/v1/batches/bat-000001")).body).toEqual({
        type: "batch_not_found",
        title: "No such batch",
        status: 404,
      });
      expect((await client.get<Problem>("/v1/batches/bat-000001/history")).body.type).toBe(
        "batch_not_found",
      );
      expect((await client.get<Problem>("/v1/batches/bat-000001/events")).body.type).toBe(
        "batch_not_found",
      );
      expect((await client.get<Problem>(`/v1/claims/${claimId}`)).body.type).toBe(
        "claim_not_found",
      );
      expect((await client.get<Problem>(`/v1/claims/${claimId}/prompt`)).body.type).toBe(
        "claim_not_found",
      );
      expect((await client.get<Problem>("/v1/documents/doc-000001")).body.type).toBe(
        "document_not_found",
      );
      expect((await client.get<unknown[]>("/v1/claims")).body).not.toEqual(
        expect.arrayContaining([expect.objectContaining({ id: claimId })]),
      );
    }
    expect((await as(RAVI).get<BatchView>("/v1/batches/bat-000001")).status).toBe(200);
    expect((await as(RAVI).get<ClaimView>(`/v1/claims/${claimId}`)).status).toBe(200);
    expect((await as(RAVI).get<DocumentView>("/v1/documents/doc-000001")).status).toBe(200);
    expect(
      (await as(RAVI).get<PipelineEvent[]>("/v1/batches/bat-000001/history")).body,
    ).toHaveLength(25);
  });

  it("lists claims newest first and filters them", async () => {
    const list = await as(ASHA).get<ClaimView[]>("/v1/claims");
    expect(list.body).toHaveLength(11);
    expect(list.body.slice(0, 10).map((c) => c.id)).toEqual(run.batch.claims.map((c) => c.id));
    expect(list.body[10]?.title).toBe("Client dinner 6 Oct 2026");
    const needs = await as(ASHA).get<ClaimView[]>("/v1/claims?status=needs_info");
    expect(needs.body.map((c) => c.title)).toEqual([
      "Miscellaneous Jul 2026",
      "Client dinner 12 Jul 2026",
      "Bengaluru trip 12 Jul 2026",
      "Hyderabad trip 7 Aug 2026",
      "Chandigarh trip 15\u201318 Aug 2026",
    ]);
    const auto = await as(ASHA).get<ClaimView[]>("/v1/claims?route=auto_approve");
    expect(auto.body.map((c) => c.title)).toEqual([
      "Fuel Jul 2026",
      "Local conveyance Jul 2026",
      "Client dinner 6 Oct 2026",
    ]);
    expect((await as(ASHA).get<ClaimView[]>("/v1/claims?status=nonsense")).body).toEqual([]);
  });

  it("computes the hotel cap from the acting persona's grade", async () => {
    const hotelOnly = (): FileSpec[] => [
      { name: "hotel.png", data: sample("hotel-folio-lotus-bay.png") },
    ];
    const capFinding = (batch: BatchView) =>
      batch.claims.flatMap((c) => c.findings).find((f) => f.code === "hotel_over_cap");

    expect(capFinding(run.batch)?.message).toBe(
      "Hotel night \u20b97,700 exceeds the \u20b97,500 cap for grade L3 in a Tier-2 city.",
    );
    for (const persona of [P001, RAVI]) {
      const other = await runBatch(persona, hotelOnly());
      expect(capFinding(other.batch), persona).toBeUndefined();
      expect(codes(other.batch.claims[0]?.findings)).toEqual(["items_subtotal_mismatch:high"]);
    }
    const meera = await runBatch(MEERA, hotelOnly());
    expect(capFinding(meera.batch)).toMatchObject({
      message:
        "Hotel night \u20b97,700 exceeds the \u20b95,500 cap for grade L2 in a Tier-2 city (3 of 3 nights are over).",
      expected: 5500,
      actual: 7700,
    });
  });

  it("detects duplicates per persona only, but reads the same bytes only once", async () => {
    const other = await runBatch(P001, [
      { name: "cab.png", data: sample("cab-receipt-raahi-cabs.png") },
    ]);
    const extracted = other.sse.events.find((e) => e.type === "document_extracted")
      ?.data as S["DocumentExtracted"];
    expect(extracted).toMatchObject({ cached: true, cost_usd: 0 }); // read earlier in this run, by anyone
    expect(other.batch.documents[0]).toMatchObject({ trust_score: 100, verdict: "clean" });
    expect(codes(other.batch.claims[0]?.findings)).toEqual([]);

    const again = await runBatch(ASHA, [
      { name: "cab-again.png", data: sample("cab-receipt-raahi-cabs.png") },
    ]);
    expect(codes(again.batch.claims[0]?.findings)).toEqual(["duplicate_exact:high"]);
    expect(again.batch.claims[0]?.findings[0]?.message).toBe(
      "This exact file was already uploaded as document doc-000003.",
    );
    expect(again.batch.documents[0]).toMatchObject({ trust_score: 60, verdict: "review" });
  });
});

// =====================================================================================================
describe("upload validation", () => {
  beforeAll(() => api.reset());

  const asha = () => as(ASHA);

  it("rejects an upload without files (422 no_files)", async () => {
    const form = new FormData();
    form.append("note", "no files here");
    const empty = await asha().raw<Problem>("POST", "/v1/batches", { body: form });
    expect(empty.status).toBe(422);
    expect(empty.headers.get("content-type")).toBe("application/problem+json");
    expect(empty.body).toEqual({
      type: "no_files",
      title: "Upload at least one file",
      status: 422,
    });
    const bare = await asha().raw<Problem>("POST", "/v1/batches", { body: new FormData() });
    expect(bare.body.type).toBe("no_files");
  });

  it("rejects a body that is not multipart like FastAPI does", async () => {
    const json = await asha().raw<S["HTTPValidationError"]>("POST", "/v1/batches", { json: {} });
    expect(json.status).toBe(422);
    expect(json.body.detail?.[0]).toMatchObject({ type: "missing", loc: ["body", "files"] });
  });

  it("accepts 30 files and rejects 31 (413 too_many_files)", async () => {
    const thirtyOne = Array.from({ length: 31 }, (_, i) => ({
      name: `r${i}.png`,
      data: pngVariant(i),
    }));
    const tooMany = await asha().raw<Problem>("POST", "/v1/batches", { body: formOf(thirtyOne) });
    expect(tooMany.status).toBe(413);
    expect(tooMany.body).toEqual({
      type: "too_many_files",
      title: "At most 30 files per upload",
      status: 413,
    });
    const ok = await asha().upload(thirtyOne.slice(0, 30));
    expect(ok.status).toBe(202);
    expect(ok.body.batch_id).toBe("bat-000001"); // the refused upload consumed nothing
    expect(ok.body.documents).toHaveLength(30);
    const sse = await readEvents(ASHA, ok.body.events_url);
    expect(sse.events.at(-1)?.type).toBe("batch_done");
  });

  it("rejects a file over 15 MB (413 file_too_large)", async () => {
    const big = Buffer.alloc(15 * 1024 * 1024 + 1);
    TINY_PNG.copy(big);
    const small = { name: "small.png", data: pngVariant(1) };
    const response = await asha().raw<Problem>("POST", "/v1/batches", {
      body: formOf([small, { name: "scan.png", data: big }]),
    });
    expect(response.status).toBe(413);
    expect(response.body).toEqual({
      type: "file_too_large",
      title: "scan.png is larger than 15 MB",
      status: 413,
    });
    const exact = Buffer.alloc(15 * 1024 * 1024);
    TINY_PNG.copy(exact);
    expect((await asha().upload([{ name: "exact.png", data: exact }])).status).toBe(202);
  });

  it("refuses by magic bytes, never by the declared type (422 unsupported_files)", async () => {
    const response = await asha().raw<Problem>("POST", "/v1/batches", {
      body: formOf([
        { name: "fine.png", data: pngVariant(2), type: "image/png" },
        { name: "notes.png", data: Buffer.from("hello, I am text"), type: "image/png" },
        { name: "anim.gif", data: Buffer.from("GIF89a....."), type: "image/gif" },
        { name: "empty.pdf", data: Buffer.alloc(0), type: "application/pdf" },
      ]),
    });
    expect(response.status).toBe(422);
    expect(response.body).toEqual({
      type: "unsupported_files",
      title: "Some files are not JPEG, PNG, WebP or PDF",
      status: 422,
      detail: {
        files: [
          {
            filename: "notes.png",
            reason: "unsupported file type (expected JPEG, PNG, WebP or PDF)",
          },
          {
            filename: "anim.gif",
            reason: "unsupported file type (expected JPEG, PNG, WebP or PDF)",
          },
          {
            filename: "empty.pdf",
            reason: "unsupported file type (expected JPEG, PNG, WebP or PDF)",
          },
        ],
      },
    });
  });

  it("accepts JPEG, PNG, WebP and PDF whatever the extension says, and cleans file names", async () => {
    const created = await asha().upload([
      { name: "a.png", data: pngVariant(3), type: "text/plain" },
      { name: "b.pdf", data: jpegVariant(1) },
      { name: "c.txt", data: webpVariant(1) },
      { name: "d", data: pdfVariant(1) },
      { name: "..\\..\\evil.png", data: pngVariant(4) },
      { name: "my receipt; copy (final).png", data: pngVariant(5) },
      { name: "/var/tmp/x/", data: pngVariant(6) },
    ]);
    expect(created.status).toBe(202);
    expect(created.body.documents.map((d) => d.filename)).toEqual([
      "a.png",
      "b.pdf",
      "c.txt",
      "d",
      "evil.png",
      "my receipt; copy (final).png",
      "x",
    ]);
    const sse = await readEvents(ASHA, created.body.events_url);
    expect(sse.events.at(-1)?.type).toBe("batch_done");
    const files = await Promise.all(
      created.body.documents.map((d) => asha().get<Buffer>(`/v1/documents/${d.id}/file`)),
    );
    expect(files.map((f) => f.headers.get("content-type"))).toEqual([
      "image/png",
      "image/jpeg",
      "image/webp",
      "application/pdf",
      "image/png",
      "image/png",
      "image/png",
    ]);
  });

  it("reads failures: file names with blur, corrupt or unreadable fail without a checked event", async () => {
    api.reset();
    const run = await runBatch(ASHA, [
      { name: "good-receipt.png", data: pngVariant(10) },
      { name: "Blurry-photo.PNG", data: pngVariant(11) },
      { name: "corrupt.pdf", data: pdfVariant(12) },
      { name: "scan-UNREADABLE.jpg", data: jpegVariant(13) },
    ]);
    const types = run.sse.events.map((e) => e.type);
    expect(types).toEqual([
      "batch_started",
      "document_extracted",
      "document_checked",
      "document_failed",
      "document_failed",
      "document_failed",
      "claims_ready",
      "batch_done",
    ]);
    const failed = run.sse.events
      .filter((e) => e.type === "document_failed")
      .map((e) => e.data as S["DocumentFailed"]);
    expect(failed.map((e) => e.filename)).toEqual([
      "Blurry-photo.PNG",
      "corrupt.pdf",
      "scan-UNREADABLE.jpg",
    ]);
    for (const e of failed) {
      expect(e.error).toBe(
        "We could not read this file. It looks blurry or corrupted. Retake the photo and try again.",
      );
    }
    const done = run.sse.events.at(-1)?.data as S["BatchDone"];
    expect(done).toMatchObject({ processed: 1, failed: 3, claims: 1 });
    expect(run.batch).toMatchObject({ status: "done", total: 4, processed: 1, failed: 3 });
    expect(run.batch.documents.map((d) => d.status)).toEqual([
      "processed",
      "failed",
      "failed",
      "failed",
    ]);
    expect(run.batch.documents[1]).toMatchObject({
      error:
        "We could not read this file. It looks blurry or corrupted. Retake the photo and try again.",
      document: null,
      trust_score: null,
      verdict: null,
    });
    const view = await asha().get<DocumentView>("/v1/documents/doc-000002");
    expect(view.body).toMatchObject({ status: "failed", document: null });
  });

  it("still finishes a batch whose documents all fail (empty claims_ready, then batch_done)", async () => {
    const run = await runBatch(ASHA, [
      { name: "blur1.png", data: pngVariant(20) },
      { name: "blur2.png", data: pngVariant(21) },
    ]);
    expect(run.sse.events.map((e) => e.type)).toEqual([
      "batch_started",
      "document_failed",
      "document_failed",
      "claims_ready",
      "batch_done",
    ]);
    expect((run.sse.events.at(-2)?.data as S["ClaimsReady"]).claim_ids).toEqual([]);
    expect(run.sse.events.at(-1)?.data).toMatchObject({
      processed: 0,
      failed: 2,
      claims: 0,
      cost_usd: 0,
    });
    expect(run.batch.claims).toEqual([]);
    expect(run.batch.status).toBe("done");
  });
});

describe("a batch that fails", () => {
  it("ends with batch_failed when a file name asks for it", async () => {
    api.reset();
    const run = await runBatch(ASHA, [
      { name: "receipt.png", data: pngVariant(30) },
      { name: "Batch-Fail.png", data: pngVariant(31) },
    ]);
    expect(run.sse.events.map((e) => e.type)).toEqual(["batch_started", "batch_failed"]);
    const failed = run.sse.events[1]?.data as S["BatchFailed"];
    expect(failed.error).toContain("RuntimeError");
    expect(run.batch).toMatchObject({
      status: "failed",
      error: failed.error,
      processed: 0,
      claims: [],
    });
    expect(run.batch.finished_at).not.toBeNull();
    expect(run.batch.documents.map((d) => d.status)).toEqual(["queued", "queued"]);
    expect(run.history).toEqual(run.sse.events.map((e) => e.data));
    // a failed batch leaves the next one unaffected
    const next = await runBatch(ASHA, [{ name: "ok.png", data: pngVariant(32) }]);
    expect(next.sse.events.at(-1)?.type).toBe("batch_done");
  });
});

// =====================================================================================================
describe("demo mode", () => {
  /** Run `run` against a second mock with other options (the helpers talk to the current `api`). */
  async function using<T>(options: Parameters<typeof createMockApi>[0], run: () => Promise<T>) {
    const previous = api;
    api = await createMockApi({ port: 0, speed: 50, ...options });
    try {
      return await run();
    } finally {
      const used = api;
      api = previous;
      await used.close();
    }
  }

  it("starts over for an employee, then for the approver, and the statistics follow", async () => {
    api.reset();
    await runBatch(ASHA, sampleFiles());
    const before = (await as(ASHA).get<S["Stats"]>("/v1/stats")).body;

    // Asha's own: the seeded dinner and the new batch, seeded or not.
    const own = await as(ASHA).post<S["ResetResult"]>("/v1/demo/reset");
    expect(own.status).toBe(200);
    expect(own.body).toEqual({ batches: 2, documents: 12, claims: 11 });
    expect((await as(ASHA).get<ClaimView[]>("/v1/claims")).body).toEqual([]);
    expect((await as(ASHA).get<Problem>("/v1/batches/bat-000001")).body.type).toBe(
      "batch_not_found",
    );
    const gone = await as(ASHA).get<Problem>("/v1/documents/doc-000003/file");
    expect(gone.body).toEqual({
      type: "document_not_found",
      title: "No such document",
      status: 404,
    });
    expect((await as(P001).get<ClaimView[]>("/v1/claims")).body).toHaveLength(2); // others keep theirs
    const after = (await as(ASHA).get<S["Stats"]>("/v1/stats")).body;
    expect(after.documents_processed).toBe(before.documents_processed - 12);
    expect(after.claims).toBe(before.claims - 11);
    expect(Object.values(after.claims_by_status).reduce((s, n) => s + n, 0)).toBe(after.claims);

    // The same file is no longer a duplicate, and the counters carry on.
    const again = await runBatch(ASHA, [
      { name: "cab.png", data: sample("cab-receipt-raahi-cabs.png") },
    ]);
    expect(again.created.batch_id).toBe("bat-000002");
    expect(again.batch.claims[0]?.findings).toEqual([]);

    // The approver clears everyone's: only the seeded history in the statistics is left.
    const everyone = await as(RAVI).post<S["ResetResult"]>("/v1/demo/reset");
    expect(everyone.body).toEqual({ batches: 6, documents: 6, claims: 6 });
    expect((await as(RAVI).get<ClaimView[]>("/v1/claims")).body).toEqual([]);
    expect((await as(RAVI).get<ClaimView[]>("/v1/approvals")).body).toEqual([]);
    expect((await as(RAVI).get<S["Stats"]>("/v1/stats")).body).toMatchObject({
      documents_processed: 47,
      claims: 9,
    });
  });

  it("ends the stream of a batch that is deleted while it plays", async () => {
    await using({ speed: 2 }, async () => {
      const created = await as(ASHA).upload([{ name: "a.png", data: pngVariant(800) }]);
      const res = await fetch(`${api.url}${created.body.events_url}`, {
        headers: { "X-Persona": ASHA },
      });
      expect(res.status).toBe(200);
      await as(ASHA).post("/v1/demo/reset");
      const { events } = parseSse(await res.text()); // the stream ends instead of hanging
      expect(events.at(-1)?.type).toBe("batch_failed");
      expect((await as(ASHA).get<ClaimView[]>("/v1/claims")).body).toEqual([]); // nothing came back
    });
  });

  it("answers demo_disabled (404) when demo mode is off", async () => {
    await using({ demo: false }, async () => {
      const meta = await as(null).get<S["MetaInfo"]>("/v1/meta");
      expect(meta.body).toMatchObject({ demo: false, runtime: "embedded" });
      const res = await as(ASHA).post<Problem>("/v1/demo/reset");
      expect(res.status).toBe(404);
      expect(res.headers.get("content-type")).toBe("application/problem+json");
      expect(res.body).toEqual({
        type: "demo_disabled",
        title: "Start over is for the demo only",
        status: 404,
      });
      expect((await as(ASHA).get<ClaimView[]>("/v1/claims")).body).toHaveLength(1); // nothing deleted
    });
  });

  it("reads only the recorded samples in strict mode", async () => {
    await using({ demoStrict: true }, async () => {
      const run = await runBatch(ASHA, [
        { name: "cab.png", data: sample("cab-receipt-raahi-cabs.png") },
        { name: "mine.png", data: pngVariant(801) },
      ]);
      expect(run.sse.events.map((e) => e.type)).toEqual([
        "batch_started",
        "document_extracted",
        "document_checked",
        "document_failed",
        "claims_ready",
        "batch_done",
      ]);
      const failed = run.sse.events[3]?.data as S["DocumentFailed"];
      expect(failed).toMatchObject({ filename: "mine.png", document_id: "doc-000002" });
      expect(failed.error).toBe(
        "This demo reads only its recorded sample receipts. To read your own, run ClaimPilot with your own API key (see the README).",
      );
      expect(run.sse.events.at(-1)?.data).toMatchObject({ processed: 1, failed: 1, claims: 1 });
      expect(run.batch.documents.map((d) => d.status)).toEqual(["processed", "failed"]);
      expect(run.batch.documents[1]?.error).toBe(failed.error);
    });
  });
});

// =====================================================================================================
describe("files the mock does not know", () => {
  beforeAll(() => api.reset());

  it("reads any file as a plausible, deterministic, schema-valid receipt", async () => {
    const files: FileSpec[] = [
      ...Array.from({ length: 24 }, (_, i) => ({
        name: `photo-${i}.png`,
        data: pngVariant(100 + i),
      })),
      ...Array.from({ length: 6 }, (_, i) => ({
        name: `bill-${i}.pdf`,
        data: pdfVariant(100 + i),
      })),
    ];
    const first = await runBatch(MEERA, files);
    expect(first.batch).toMatchObject({ status: "done", total: 30, failed: 0, processed: 30 });
    const documents = first.batch.documents.map((d) => d.document);
    const today = new Date();
    for (const doc of documents) {
      expect(doc).toBeTruthy();
      // a generated bill adds up and has a valid GSTIN: the trust layer finds nothing in it
      expect(checkGst(doc?.receipt as never), doc?.filename).toEqual([]);
      const total = doc?.receipt.total ?? 0;
      expect(total).toBeGreaterThanOrEqual(80);
      expect(total).toBeLessThanOrEqual(9000);
      const age = (today.getTime() - Date.parse(`${doc?.receipt.date}T00:00:00Z`)) / 86_400_000;
      expect(age).toBeGreaterThanOrEqual(-1);
      expect(age).toBeLessThanOrEqual(31);
    }
    for (const d of first.batch.documents) {
      expect([100, 85]).toContain(d.trust_score);
      expect(d.verdict).toBe("clean");
    }
    expect(new Set(documents.map((d) => d?.decisions.category)).size).toBeGreaterThan(3);
    // claims: one period claim per category and month, one event claim per dinner or course
    for (const claim of first.batch.claims) {
      expect(["period", "event"]).toContain(claim.mode);
      const sum = claim.document_ids.reduce(
        (s, id) =>
          s + (first.batch.documents.find((d) => d.id === id)?.document?.receipt.total ?? 0),
        0,
      );
      expect(claim.total).toBeCloseTo(sum, 2);
    }
    const periodTitles = first.batch.claims.filter((c) => c.mode === "period").map((c) => c.title);
    expect(new Set(periodTitles).size).toBe(periodTitles.length);

    // The same bytes read the same way after a reset.
    api.reset();
    const second = await runBatch(MEERA, files);
    expect(second.batch.documents.map((d) => d.document)).toEqual(documents);
    expect(second.batch.claims).toEqual(first.batch.claims);
  });

  it("is deterministic from the bytes and ignores the file name", async () => {
    api.reset();
    const a = await runBatch(P001, [{ name: "one.png", data: pngVariant(500) }]);
    api.reset();
    const b = await runBatch(P001, [{ name: "two.png", data: pngVariant(500) }]);
    expect(b.batch.documents[0]?.document?.receipt).toEqual(
      a.batch.documents[0]?.document?.receipt,
    );
    expect(b.batch.documents[0]?.document?.decisions).toEqual(
      a.batch.documents[0]?.document?.decisions,
    );
  });
});

// =====================================================================================================
describe("answering, replying and submitting", () => {
  let run: Run;
  const ashaClaim = (prefix: string) => claimOf(run, prefix);

  beforeAll(async () => {
    api.reset();
    run = await runBatch(ASHA, sampleFiles());
  });

  it("builds the prompt of everything still open, or nothing", async () => {
    const dinner = ashaClaim("Client dinner");
    const prompt = await as(ASHA).get<S["PromptOut"]>(`/v1/claims/${dinner.id}/prompt`);
    expect(prompt.body).toEqual({
      prompt:
        "To finish \u201cClient dinner 12 Jul 2026\u201d I need a few details:\n" +
        "1. Who attended the client dinner on 12 Jul (\u20b9830)? Please give names and company.\n" +
        "2. What was the business purpose of the client dinner on 12 Jul (\u20b9830)?\n" +
        "You can answer in one message.",
      open_question_ids: dinner.open_questions.map((q) => q.id),
    });
    const trip = await as(ASHA).get<S["PromptOut"]>(
      `/v1/claims/${ashaClaim("Hyderabad trip").id}/prompt`,
    );
    expect(trip.body.prompt).toBe(
      "To finish \u201cHyderabad trip 7 Aug 2026\u201d I need one detail:\n1. What was the business purpose of the Hyderabad trip 7 Aug 2026?",
    );
    const ready = await as(ASHA).get<S["PromptOut"]>(`/v1/claims/${ashaClaim("Fuel").id}/prompt`);
    expect(ready.body).toEqual({ prompt: null, open_question_ids: [] });
  });

  it("takes the whole reply as the answer when exactly one question is open", async () => {
    const claim = ashaClaim("Miscellaneous");
    const reply = await as(ASHA).post<ReplyOut>(`/v1/claims/${claim.id}/reply`, {
      text: "  Stationery for the Hyderabad offsite  ",
    });
    expect(reply.status).toBe(200);
    expect(reply.body.understood).toEqual({
      [claim.open_questions[0]?.id ?? ""]: "Stationery for the Hyderabad offsite",
    });
    expect(reply.body.follow_up).toBeNull();
    expect(reply.body.claim).toMatchObject({ status: "ready", route: "finance_review" });
    expect(reply.body.claim.open_questions[0]?.answer).toBe("Stationery for the Hyderabad offsite");

    const nothing = await as(ASHA).post<ReplyOut>(`/v1/claims/${claim.id}/reply`, {
      text: "thanks",
    });
    expect(nothing.body).toMatchObject({ understood: {}, follow_up: null });
    expect(nothing.body.claim.id).toBe(claim.id);
  });

  it("refuses a blank single answer (422 blank_answer) and leaves the claim alone", async () => {
    const claim = ashaClaim("Hyderabad trip");
    const blank = await as(ASHA).post<Problem>(`/v1/claims/${claim.id}/reply`, { text: "   " });
    expect(blank.status).toBe(422);
    expect(blank.body).toEqual({
      type: "blank_answer",
      title: "An answer cannot be blank",
      status: 422,
    });
    expect((await as(ASHA).get<ClaimView>(`/v1/claims/${claim.id}`)).body.status).toBe(
      "needs_info",
    );
  });

  it("validates request bodies like FastAPI (422 with a detail list)", async () => {
    const claim = ashaClaim("Hyderabad trip");
    const noText = await as(ASHA).post<S["HTTPValidationError"]>(
      `/v1/claims/${claim.id}/reply`,
      {},
    );
    expect(noText.status).toBe(422);
    expect(noText.body.detail?.[0]).toMatchObject({ type: "missing", loc: ["body", "text"] });
    const empty = await as(ASHA).post<S["HTTPValidationError"]>(`/v1/claims/${claim.id}/reply`, {
      text: "",
    });
    expect(empty.body.detail?.[0]).toMatchObject({
      type: "string_too_short",
      loc: ["body", "text"],
    });
    const long = await as(ASHA).post<S["HTTPValidationError"]>(`/v1/claims/${claim.id}/reply`, {
      text: "x".repeat(2001),
    });
    expect(long.body.detail?.[0]).toMatchObject({ type: "string_too_long" });
    const extra = await as(ASHA).post<S["HTTPValidationError"]>(`/v1/claims/${claim.id}/answers`, {
      answers: {},
      surprise: 1,
    });
    expect(extra.body.detail?.[0]).toMatchObject({
      type: "extra_forbidden",
      loc: ["body", "surprise"],
    });
    const wrongType = await as(ASHA).post<S["HTTPValidationError"]>(
      `/v1/claims/${claim.id}/answers`,
      {
        answers: { a: 5 },
      },
    );
    expect(wrongType.body.detail?.[0]).toMatchObject({
      type: "string_type",
      loc: ["body", "answers", "a"],
    });
    const nobody = await as(ASHA).raw<S["HTTPValidationError"]>(
      "POST",
      `/v1/claims/${claim.id}/submit`,
    );
    expect(nobody.status).toBe(422);
    expect(nobody.body.detail?.[0]).toMatchObject({ type: "missing", loc: ["body"] });
    const notJson = await as(ASHA).raw<S["HTTPValidationError"]>(
      "POST",
      `/v1/claims/${claim.id}/submit`,
      {
        body: "{nope",
        headers: { "Content-Type": "application/json" },
      },
    );
    expect(notJson.status).toBe(422);
    expect(notJson.body.detail?.[0]).toMatchObject({ type: "json_invalid" });
    const badDecision = await as(RAVI).post<S["HTTPValidationError"]>(
      `/v1/claims/${claim.id}/decision`,
      {
        comment: "x",
      },
    );
    expect(badDecision.body.detail?.[0]).toMatchObject({
      type: "missing",
      loc: ["body", "approved"],
    });
  });

  it("edits answers, refuses unknown ids and blanks, and saves nothing on failure", async () => {
    const claim = ashaClaim("Client dinner");
    const [attendees, purpose] = claim.open_questions;
    const partial = await as(ASHA).post<ClaimView>(`/v1/claims/${claim.id}/answers`, {
      answers: { [attendees?.id ?? ""]: "Neha Rao (Kestrel)" },
    });
    expect(partial.status).toBe(200);
    expect(partial.body.status).toBe("needs_info");
    expect(partial.body.open_questions.map((q) => q.answer)).toEqual(["Neha Rao (Kestrel)", null]);

    const unknown = await as(ASHA).post<Problem>(`/v1/claims/${claim.id}/answers`, {
      answers: { [purpose?.id ?? ""]: "Quarterly review", "q-nope-00000000": "x" },
    });
    expect(unknown.status).toBe(422);
    expect(unknown.body).toEqual({
      type: "unknown_question",
      title: "This claim has no question q-nope-00000000",
      status: 422,
    });
    const blank = await as(ASHA).post<Problem>(`/v1/claims/${claim.id}/answers`, {
      answers: { [purpose?.id ?? ""]: "  " },
    });
    expect(blank.body.type).toBe("blank_answer");
    const unchanged = await as(ASHA).get<ClaimView>(`/v1/claims/${claim.id}`);
    expect(unchanged.body.open_questions.map((q) => q.answer)).toEqual([
      "Neha Rao (Kestrel)",
      null,
    ]); // atomic

    const both = await as(ASHA).post<ClaimView>(`/v1/claims/${claim.id}/answers`, {
      answers: {
        [attendees?.id ?? ""]: "Neha Rao and Rohan Kapoor (Kestrel Logistics)",
        [purpose?.id ?? ""]: "Quarterly review",
      },
    });
    expect(both.body.status).toBe("ready");
    expect(both.body.route).toBe("auto_approve"); // nothing open, no findings, small: the route is recomputed
    const edited = await as(ASHA).post<ClaimView>(`/v1/claims/${claim.id}/answers`, {
      answers: { [purpose?.id ?? ""]: "Contract renewal" },
    });
    expect(edited.body.status).toBe("ready");
    expect(edited.body.open_questions.map((q) => q.answer)).toEqual([
      "Neha Rao and Rohan Kapoor (Kestrel Logistics)",
      "Contract renewal",
    ]);
    const none = await as(ASHA).post<ClaimView>(`/v1/claims/${claim.id}/answers`, { answers: {} });
    expect(none.body).toEqual(edited.body);
  });

  it("only lets the owner answer, reply and submit (403 even for the approver)", async () => {
    const claim = ashaClaim("Chandigarh trip");
    const question = claim.open_questions[0]?.id ?? "";
    const forbidden = { type: "not_your_claim", status: 403 };
    for (const persona of [RAVI]) {
      const client = as(persona);
      expect(
        (
          await client.post<Problem>(`/v1/claims/${claim.id}/answers`, {
            answers: { [question]: "x" },
          })
        ).body,
      ).toMatchObject({
        ...forbidden,
        title: "Only the owner can answer",
      });
      expect(
        (await client.post<Problem>(`/v1/claims/${claim.id}/reply`, { text: "x" })).body,
      ).toMatchObject({
        ...forbidden,
        title: "Only the owner can answer",
      });
      expect(
        (await client.post<Problem>(`/v1/claims/${claim.id}/submit`, { confirmed: true })).body,
      ).toMatchObject({
        ...forbidden,
        title: "Only the owner can submit",
      });
    }
    for (const persona of [P001, MEERA]) {
      const client = as(persona);
      expect(
        (await client.post<Problem>(`/v1/claims/${claim.id}/answers`, { answers: {} })).status,
      ).toBe(404);
      expect(
        (await client.post<Problem>(`/v1/claims/${claim.id}/reply`, { text: "x" })).body.type,
      ).toBe("claim_not_found");
      expect(
        (await client.post<Problem>(`/v1/claims/${claim.id}/submit`, { confirmed: true })).body
          .type,
      ).toBe("claim_not_found");
    }
    expect(
      (await as(ASHA).post<Problem>("/v1/claims/clm-nope/answers", { answers: {} })).body.type,
    ).toBe("claim_not_found");
  });

  it("will not submit a claim that is not ready (409 claim_not_ready) or unconfirmed (422)", async () => {
    const claim = ashaClaim("Chandigarh trip");
    const notReady = await as(ASHA).post<Problem>(`/v1/claims/${claim.id}/submit`, {
      confirmed: true,
    });
    expect(notReady.status).toBe(409);
    expect(notReady.body).toEqual({
      type: "claim_not_ready",
      title: "The claim is not ready to submit",
      status: 409,
      detail: { status: "needs_info", unanswered: claim.open_questions.map((q) => q.id) },
    });
    const unconfirmed = await as(ASHA).post<Problem>(`/v1/claims/${claim.id}/submit`, {
      confirmed: false,
    });
    expect(unconfirmed.status).toBe(422);
    expect(unconfirmed.body).toEqual({
      type: "confirmation_required",
      title: "The employee must explicitly confirm the submission",
      status: 422,
    });
  });

  it("submits idempotently: the same claim keeps its reference, another claim gets the next one", async () => {
    const local = ashaClaim("Local conveyance");
    const first = await as(ASHA).post<ClaimView>(
      `/v1/claims/${local.id}/submit`,
      { confirmed: true },
      { "Idempotency-Key": "key-1" },
    );
    expect(first.status).toBe(200);
    expect(first.body).toMatchObject({
      status: "submitted",
      submission_reference: "FIN-2026-000006",
      route: "auto_approve",
    });
    const sameKey = await as(ASHA).post<ClaimView>(
      `/v1/claims/${local.id}/submit`,
      { confirmed: true },
      { "Idempotency-Key": "key-1" },
    );
    expect(sameKey.body).toEqual(first.body);
    const otherKey = await as(ASHA).post<ClaimView>(
      `/v1/claims/${local.id}/submit`,
      { confirmed: true },
      { "Idempotency-Key": "key-2" },
    );
    expect(otherKey.body).toEqual(first.body);
    const noKey = await as(ASHA).post<ClaimView>(`/v1/claims/${local.id}/submit`, {
      confirmed: true,
    });
    expect(noKey.body.submission_reference).toBe("FIN-2026-000006");
    const retryUnconfirmed = await as(ASHA).post<ClaimView>(`/v1/claims/${local.id}/submit`, {
      confirmed: false,
    });
    expect(retryUnconfirmed.status).toBe(200);

    const fuel = await as(ASHA).post<ClaimView>(
      `/v1/claims/${ashaClaim("Fuel").id}/submit`,
      { confirmed: true },
      { "Idempotency-Key": "key-1" },
    );
    expect(fuel.body.submission_reference).toBe("FIN-2026-000007");
    expect(fuel.body.status).toBe("submitted");

    const locked = await as(ASHA).post<Problem>(`/v1/claims/${local.id}/answers`, {
      answers: { "q-x": "late" },
    });
    expect(locked.status).toBe(409);
    expect(locked.body).toEqual({
      type: "claim_locked",
      title: "a submitted claim can no longer be changed",
      status: 409,
    });
    const fetched = await as(ASHA).get<ClaimView>(`/v1/claims/${local.id}`);
    expect(fetched.body).toEqual(first.body);
  });

  it("lets the approver decide (comment required to reject), repeating a decision is harmless", async () => {
    const local = ashaClaim("Local conveyance");
    const fuel = ashaClaim("Fuel");

    const forbidden = await as(ASHA).post<Problem>(`/v1/claims/${local.id}/decision`, {
      approved: true,
    });
    expect(forbidden.status).toBe(403);
    expect(forbidden.body).toEqual({
      type: "approver_only",
      title: "Only approvers can do this",
      status: 403,
    });

    // checked before the claim lookup, exactly like the backend
    for (const comment of [undefined, "", "   \n"]) {
      const body = comment === undefined ? { approved: false } : { approved: false, comment };
      const noReason = await as(RAVI).post<Problem>("/v1/claims/clm-does-not-exist/decision", body);
      expect(noReason.status).toBe(422);
      expect(noReason.headers.get("content-type")).toBe("application/problem+json");
      expect(noReason.body).toEqual({
        type: "comment_required",
        title: "Say why the claim is rejected",
        status: 422,
      });
    }
    expect(
      (await as(RAVI).post<Problem>("/v1/claims/clm-does-not-exist/decision", { approved: true }))
        .body.type,
    ).toBe("claim_not_found");

    const unsubmitted = await as(RAVI).post<Problem>(
      `/v1/claims/${ashaClaim("Learning").id}/decision`,
      { approved: true },
    );
    expect(unsubmitted.status).toBe(409);
    expect(unsubmitted.body).toEqual({
      type: "claim_not_submitted",
      title: "Only a submitted claim can be approved or rejected",
      status: 409,
      detail: { status: "ready" },
    });

    const queue = await as(RAVI).get<ClaimView[]>("/v1/approvals");
    expect(queue.body.map((c) => c.title)).toEqual([
      "Fuel Jul 2026",
      "Local conveyance Jul 2026",
      "Learning 18 Jul 2026",
      "Chandigarh trip 15\u201318 Aug 2026",
      "Hyderabad trip 7 Aug 2026",
    ]);

    const approved = await as(RAVI).post<ClaimView>(`/v1/claims/${local.id}/decision`, {
      approved: true,
    });
    expect(approved.status).toBe(200);
    expect(approved.body).toMatchObject({
      status: "approved",
      submission_reference: "FIN-2026-000006",
    });
    const repeat = await as(RAVI).post<ClaimView>(`/v1/claims/${local.id}/decision`, {
      approved: true,
      comment: "again",
    });
    expect(repeat.body).toEqual(approved.body);
    const flip = await as(RAVI).post<Problem>(`/v1/claims/${local.id}/decision`, {
      approved: false,
      comment: "changed my mind",
    });
    expect(flip.status).toBe(409);
    expect(flip.body.detail).toEqual({ status: "approved" });

    const rejected = await as(RAVI).post<ClaimView>(`/v1/claims/${fuel.id}/decision`, {
      approved: false,
      comment: "Bill is older than the policy window",
    });
    expect(rejected.body.status).toBe("rejected");
    expect(
      (
        await as(RAVI).post<ClaimView>(`/v1/claims/${fuel.id}/decision`, {
          approved: false,
          comment: "x",
        })
      ).body,
    ).toEqual(rejected.body);

    const after = await as(ASHA).get<ClaimView[]>("/v1/claims?status=approved");
    expect(after.body.map((c) => c.title)).toEqual(["Local conveyance Jul 2026"]);
    expect(
      (await as(RAVI).get<ClaimView[]>("/v1/approvals?status=rejected")).body.map((c) => c.title),
    ).toEqual(["Fuel Jul 2026", "WFH supplies Aug 2026"]);
  });

  it("keeps the statistics consistent with what happened", async () => {
    const stats = (await as(ASHA).get<S["Stats"]>("/v1/stats")).body;
    const all = (await as(RAVI).get<ClaimView[]>("/v1/claims")).body;
    expect(stats.claims).toBe(9 + all.length);
    expect(Object.values(stats.claims_by_status).reduce((s, n) => s + n, 0)).toBe(stats.claims);
    expect(stats.auto_approvable_claims).toBe(
      4 + all.filter((c) => c.route === "auto_approve").length,
    );
    expect(stats.documents_processed).toBe(47 + 6 + 11);
    expect(stats.llm_calls).toBe(stats.documents_processed);
    expect(stats.estimated_minutes_saved).toBe(stats.documents_processed * 4);
    expect(stats.llm_cost_per_document_usd).toBeCloseTo(
      stats.llm_cost_usd / stats.documents_processed,
      6,
    );
    expect(stats.avg_batch_seconds).toBeGreaterThan(0);
  });
});

// =====================================================================================================
describe("replying to several open questions at once", () => {
  const restaurant = () => [
    { name: "dinner.png", data: sample("restaurant-bill-mehfil-cafe.png") },
  ];
  const claims = new Map<string, ClaimView>();

  beforeAll(async () => {
    api.reset();
    for (const persona of [ASHA, P001, MEERA, P005, RAVI]) {
      const run = await runBatch(persona, restaurant());
      claims.set(persona, claimOf(run, "Client dinner"));
    }
  });

  const reply = (persona: string, text: string) =>
    as(persona).post<ReplyOut>(`/v1/claims/${claims.get(persona)?.id}/reply`, { text });
  const ids = (persona: string) => claims.get(persona)?.open_questions.map((q) => q.id) ?? [];

  it("splits a reply on semicolons into attendees then purpose", async () => {
    const out = await reply(
      ASHA,
      "Neha Rao and Rohan Kapoor from Kestrel Logistics; quarterly business review",
    );
    const [attendees, purpose] = ids(ASHA);
    expect(out.body.understood).toEqual({
      [attendees ?? ""]: "Neha Rao and Rohan Kapoor from Kestrel Logistics",
      [purpose ?? ""]: "quarterly business review",
    });
    expect(out.body.follow_up).toBeNull();
    expect(out.body.claim).toMatchObject({ status: "ready", route: "auto_approve" });
  });

  it("understands labelled replies, newlines and sentence ends", async () => {
    const labelled = await reply(
      P001,
      "Attendees: Neha Rao, Rohan Kapoor. Purpose: contract renewal talks",
    );
    expect(Object.values(labelled.body.understood)).toEqual([
      "Neha Rao, Rohan Kapoor",
      "contract renewal talks",
    ]);
    expect(labelled.body.claim.status).toBe("ready");

    const purposeOnly = await reply(RAVI, "purpose - annual contract review");
    expect(purposeOnly.body.understood).toEqual({ [ids(RAVI)[1] ?? ""]: "annual contract review" });
    expect(purposeOnly.body.follow_up).toBe(
      "To finish \u201cClient dinner 12 Jul 2026\u201d I need one detail:\n1. Who attended the client dinner on 12 Jul (\u20b9830)? Please give names and company.",
    );

    const sentences = await reply(P005, "Neha Rao. Quarterly review. Followed by dinner.");
    expect(Object.values(sentences.body.understood)).toEqual([
      "Neha Rao",
      "Quarterly review Followed by dinner",
    ]);
  });

  it("answers only the first question with a one-segment reply, and asks again for the rest", async () => {
    const out = await reply(MEERA, "Neha Rao and Rohan Kapoor");
    expect(out.body.understood).toEqual({ [ids(MEERA)[0] ?? ""]: "Neha Rao and Rohan Kapoor" });
    expect(out.body.claim.status).toBe("needs_info");
    expect(out.body.follow_up).toBe(
      "To finish \u201cClient dinner 12 Jul 2026\u201d I need one detail:\n1. What was the business purpose of the client dinner on 12 Jul (\u20b9830)?",
    );
    expect(out.body.follow_up).not.toContain("couldn't match");
  });

  it("says it could not match a reply that answers nothing", async () => {
    api.reset();
    const run = await runBatch(ASHA, restaurant());
    const claim = claimOf(run, "Client dinner");
    const out = await as(ASHA).post<ReplyOut>(`/v1/claims/${claim.id}/reply`, { text: "  ;  " });
    expect(out.body.understood).toEqual({});
    expect(out.body.claim.status).toBe("needs_info");
    expect(out.body.follow_up).toBe(
      "I couldn't match that to my questions, so I'll ask again.\n" +
        "To finish \u201cClient dinner 12 Jul 2026\u201d I need a few details:\n" +
        "1. Who attended the client dinner on 12 Jul (\u20b9830)? Please give names and company.\n" +
        "2. What was the business purpose of the client dinner on 12 Jul (\u20b9830)?\n" +
        "You can answer in one message.",
    );
    const numbered = await as(ASHA).post<ReplyOut>(`/v1/claims/${claim.id}/reply`, {
      text: "1) Neha and Rohan 2) Renewal talks",
    });
    expect(Object.values(numbered.body.understood)).toEqual(["Neha and Rohan", "Renewal talks"]);
    expect(numbered.body.claim.status).toBe("ready");
  });
});

// =====================================================================================================
describe("the live stream", () => {
  it("flushes the headers before the first event", async () => {
    const slow = await createMockApi({ port: 0, speed: 0.05 }); // first event after five seconds
    try {
      const created = await fetch(`${slow.url}/v1/batches`, {
        method: "POST",
        headers: { "X-Persona": ASHA },
        body: formOf([{ name: "a.png", data: pngVariant(900) }]),
      });
      const { events_url: eventsUrl } = (await created.json()) as S["BatchCreated"];
      const started = Date.now();
      const controller = new AbortController();
      const res = await fetch(`${slow.url}${eventsUrl}`, {
        headers: { "X-Persona": ASHA },
        signal: controller.signal,
      });
      expect(res.status).toBe(200);
      expect(Date.now() - started).toBeLessThan(2000);
      expect(res.headers.get("content-type")).toContain("text/event-stream");
      controller.abort();
    } finally {
      await slow.close();
    }
  });

  it("joins a running batch at any point and ends after the terminal event", async () => {
    api.reset();
    const created = await as(ASHA).upload(sampleFiles(false).slice(0, 4));
    const fromTwo = await readEvents(ASHA, created.body.events_url, "1");
    expect(fromTwo.events[0]?.id).toBe(2);
    expect(fromTwo.events.at(-1)?.type).toBe("batch_done");
    const all = await readEvents(ASHA, created.body.events_url);
    expect(all.events.map((e) => e.id)).toEqual([...Array(all.events.length).keys()]);
  });

  it("shows the batch moving from queued to processing to done", async () => {
    const slow = await createMockApi({ port: 0, speed: 2 });
    try {
      const headers = { "X-Persona": ASHA };
      const post = await fetch(`${slow.url}/v1/batches`, {
        method: "POST",
        headers,
        body: formOf([
          { name: "a.png", data: pngVariant(700) },
          { name: "b.png", data: pngVariant(701) },
        ]),
      });
      const created = (await post.json()) as S["BatchCreated"];
      const view = async () =>
        (await (
          await fetch(`${slow.url}/v1/batches/${created.batch_id}`, { headers })
        ).json()) as BatchView;

      const queued = await view();
      expect(queued).toMatchObject({
        status: "queued",
        total: 2,
        processed: 0,
        failed: 0,
        finished_at: null,
        claims: [],
      });
      expect(queued.documents.map((d) => [d.status, d.document, d.trust_score, d.verdict])).toEqual(
        [
          ["queued", null, null, null],
          ["queued", null, null, null],
        ],
      );

      const res = await fetch(`${slow.url}${created.events_url}`, { headers });
      const seen: string[] = [];
      for await (const event of streamed(res)) {
        seen.push(event.type);
        if (event.type === "batch_started") expect((await view()).status).toBe("processing");
        if (event.type === "document_checked" && event.document_id === created.documents[0]?.id) {
          const now = await view();
          expect(now.documents.map((d) => d.status)).toEqual(["processed", "queued"]);
          expect(now.documents[0]?.trust_score).toBeGreaterThanOrEqual(85);
          expect(now.documents[0]?.document?.id).toBe(created.documents[0]?.id);
          expect(now.documents[1]?.document).toBeNull();
          // the counters are only set when the batch is done, like the backend
          expect(now).toMatchObject({ status: "processing", processed: 0, claims: [] });
        }
        if (event.type === "claims_ready") {
          const now = await view();
          expect(now.claims.map((c) => c.id)).toEqual(event.claim_ids);
          expect(["processing", "done"]).toContain(now.status); // batch_done follows right behind
        }
        if (event.type === "batch_done") {
          const now = await view();
          expect(now).toMatchObject({ status: "done", processed: 2, failed: 0 });
          expect(now.finished_at).not.toBeNull();
        }
      }
      expect(seen.at(-1)).toBe("batch_done");
      expect(seen).toContain("claims_ready");
    } finally {
      await slow.close();
    }
  });

  it("sends keepalive comments while idle and ends after batch_done", async () => {
    // A hand-made batch that stays quiet, streamed through the real SSE writer at speed 1000.
    const batch = {
      id: "bat-test",
      status: "processing",
      events: [] as PipelineEvent[],
      listeners: new Set<(index: number, event: PipelineEvent) => void>(),
    };
    const streams = new Set<() => void>();
    const ctx = { speed: 1000, streams };
    const server = http.createServer((req, res) => {
      streamEvents(ctx as never, req, res, batch as never, 0);
    });
    await new Promise<void>((resolve) => server.listen(0, resolve));
    const { port } = server.address() as AddressInfo;
    try {
      const res = await fetch(`http://127.0.0.1:${port}/`);
      expect(res.headers.get("content-type")).toBe("text/event-stream; charset=utf-8");
      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let text = "";
      const readUntil = async (done: (text: string) => boolean) => {
        while (!done(text)) {
          const chunk = await reader?.read();
          if (!chunk || chunk.done) break;
          text += decoder.decode(chunk.value, { stream: true });
        }
      };
      await readUntil((t) => t.includes(": keepalive\n\n"));
      expect(text).toMatch(/^(: keepalive\n\n)+$/);

      const event: PipelineEvent = { batch_id: "bat-test", type: "batch_started", total: 1 };
      publish(batch as never, event);
      await readUntil((t) => t.includes("event: batch_started"));
      expect(text).toContain(`id: 0\nevent: batch_started\ndata: ${JSON.stringify(event)}\n\n`);

      publish(batch as never, {
        batch_id: "bat-test",
        type: "batch_done",
        processed: 1,
        failed: 0,
        claims: 0,
        cost_usd: 0,
      });
      await readUntil(() => false); // the server ends the stream after the terminal event
      expect(text).toContain("id: 1\nevent: batch_done\n");
      expect(streams.size).toBe(0);
    } finally {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    }
  });
});

// =====================================================================================================
describe("reset and CORS", () => {
  it("restores the seed and the counters, so a rerun gives identical ids", async () => {
    api.reset();
    const first = await runBatch(ASHA, sampleFiles());
    const submit = await as(ASHA).post<ClaimView>(
      `/v1/claims/${claimOf(first, "Local conveyance").id}/submit`,
      {
        confirmed: true,
      },
    );
    expect(submit.body.submission_reference).toBe("FIN-2026-000006");

    const reset = await fetch(`${api.url}/__mock/reset`, { method: "POST" });
    expect(reset.status).toBe(204);
    expect(await reset.text()).toBe("");
    expect((await as(ASHA).get<ClaimView[]>("/v1/claims")).body).toHaveLength(1);
    expect((await as(ASHA).get<Problem>("/v1/batches/bat-000001")).body.type).toBe(
      "batch_not_found",
    );
    expect((await as(ASHA).get<Problem>("/v1/documents/doc-000001")).body.type).toBe(
      "document_not_found",
    );
    expect((await as(RAVI).get<ClaimView[]>("/v1/approvals")).body).toHaveLength(3);

    const second = await runBatch(ASHA, sampleFiles());
    expect(second.created).toEqual(first.created);
    expect(second.batch.claims.map((c) => c.id)).toEqual(first.batch.claims.map((c) => c.id));
    expect(second.batch.claims.flatMap((c) => c.open_questions.map((q) => q.id))).toEqual(
      first.batch.claims.flatMap((c) => c.open_questions.map((q) => q.id)),
    );
    expect(second.sse.events.map((e) => e.data)).toEqual(first.sse.events.map((e) => e.data));
    const again = await as(ASHA).post<ClaimView>(
      `/v1/claims/${claimOf(second, "Local conveyance").id}/submit`,
      {
        confirmed: true,
      },
    );
    expect(again.body.submission_reference).toBe("FIN-2026-000006");
  });

  it("stops a batch that is still playing when the mock is reset", async () => {
    api.reset();
    const slow = await createMockApi({ port: 0, speed: 20 });
    try {
      const created = await fetch(`${slow.url}/v1/batches`, {
        method: "POST",
        headers: { "X-Persona": ASHA },
        body: formOf(sampleFiles(false).map((f) => ({ ...f }))),
      });
      const { events_url: eventsUrl } = (await created.json()) as S["BatchCreated"];
      const res = await fetch(`${slow.url}${eventsUrl}`, { headers: { "X-Persona": ASHA } });
      expect(res.status).toBe(200);
      slow.reset();
      const text = await res.text(); // the stream ends instead of hanging
      expect(text).not.toContain("batch_done");
      const gone = await fetch(`${slow.url}/v1/batches/bat-000001`, {
        headers: { "X-Persona": ASHA },
      });
      expect(gone.status).toBe(404);
    } finally {
      await slow.close();
    }
  });

  it("answers preflight requests for localhost and 127.0.0.1 origins only", async () => {
    const preflight = (origin: string) =>
      rawRequest("OPTIONS", "/v1/claims", {
        Origin: origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-persona,content-type",
      });
    for (const origin of ["http://localhost:3000", "http://127.0.0.1:5173", "http://localhost"]) {
      const res = await preflight(origin);
      expect(res.status, origin).toBe(204);
      expect(res.headers["access-control-allow-origin"]).toBe(origin);
      expect(res.headers["access-control-allow-methods"]).toBe("GET, POST, OPTIONS");
      expect(res.headers["access-control-allow-headers"]).toBe(
        "X-Persona, Idempotency-Key, Content-Type, Last-Event-ID",
      );
      expect(res.headers.vary).toContain("Origin");
    }
    for (const origin of [
      "https://evil.example",
      "http://localhost.evil.example",
      "https://localhost:3000",
      "null",
    ]) {
      const res = await preflight(origin);
      expect(res.status, origin).toBe(204);
      expect(res.headers["access-control-allow-origin"]).toBeUndefined();
      expect(res.headers.vary).toContain("Origin");
    }
    const simple = await rawRequest("GET", "/v1/employees", { Origin: "http://localhost:3000" });
    expect(simple.status).toBe(200);
    expect(simple.headers["access-control-allow-origin"]).toBe("http://localhost:3000");
    const problem = await rawRequest("GET", "/v1/me", { Origin: "http://127.0.0.1:3000" });
    expect(problem.status).toBe(401);
    expect(problem.headers["access-control-allow-origin"]).toBe("http://127.0.0.1:3000");
    const stranger = await rawRequest("GET", "/v1/employees", { Origin: "https://evil.example" });
    expect(stranger.headers["access-control-allow-origin"]).toBeUndefined();
  });

  it("answers on localhost, 127.0.0.1 and the IPv6 loopback", async () => {
    for (const host of ["localhost", "127.0.0.1"]) {
      const res = await fetch(`http://${host}:${api.port}/healthz`);
      expect(res.status, host).toBe(200);
    }
    const v6 = await fetch(`http://[::1]:${api.port}/healthz`).then(
      (res) => res.status,
      () => null,
    );
    expect([200, null]).toContain(v6); // null when the machine has no IPv6 loopback
  });

  it("honours a smaller upload limit option (413 with the limit in the message)", async () => {
    const small = await createMockApi({ port: 0, speed: 50, maxFileBytes: 1024 });
    try {
      const res = await fetch(`${small.url}/v1/batches`, {
        method: "POST",
        headers: { "X-Persona": ASHA },
        body: formOf([{ name: "big.png", data: Buffer.concat([TINY_PNG, Buffer.alloc(2000)]) }]),
      });
      expect(res.status).toBe(413);
      expect(await res.json()).toEqual({
        type: "file_too_large",
        title: "big.png is larger than 0.001 MB",
        status: 413,
      });
    } finally {
      await small.close();
    }
  });

  it("asks what an unsure document was for, unless the claim already asks about it", async () => {
    // The handwritten sample is unsure (0.58) but is already asked about as a personal expense.
    api.reset();
    const sampleRun = await runBatch(ASHA, [
      { name: "bill.png", data: sample("handwritten-bill-gupta-provision.png") },
    ]);
    expect(sampleRun.batch.claims[0]?.open_questions.map((q) => q.kind)).toEqual([
      "confirm_personal",
    ]);

    // A generated document that is unsure and not personal gets the "other" question.
    const index = findVariant(
      (plan) => plan.decisions.category_confidence < 0.7 && plan.decisions.personal_expense < 0.5,
    );
    api.reset();
    const run = await runBatch(ASHA, [{ name: "unsure.png", data: pngVariant(index) }]);
    const [claim] = run.batch.claims;
    const doc = run.batch.documents[0]?.document;
    expect(doc?.decisions.engine).toBe("llm");
    expect(claim?.status).toBe("needs_info");
    expect(claim?.open_questions).toHaveLength(1);
    expect(claim?.open_questions[0]).toMatchObject({
      id: "q-category-doc-000001",
      kind: "other",
      document_ids: ["doc-000001"],
      answer: null,
    });
    expect(claim?.open_questions[0]?.text).toMatch(
      /^What was the \u20b9[\d,.]+ handwritten bill from \u201c.+\u201d on \d+ \w{3} for\?$/,
    );

    // Answering it completes the claim, and the question stays on the claim.
    const reply = await as(ASHA).post<ReplyOut>(`/v1/claims/${claim?.id}/reply`, {
      text: "Office pantry supplies",
    });
    expect(reply.body.claim.status).toBe("ready");
    expect(reply.body.claim.open_questions.map((q) => q.answer)).toEqual([
      "Office pantry supplies",
    ]);

    const quiet = await createMockApi({ port: 0, speed: 50, categoryQuestions: false });
    try {
      const created = await fetch(`${quiet.url}/v1/batches`, {
        method: "POST",
        headers: { "X-Persona": ASHA },
        body: formOf([{ name: "unsure.png", data: pngVariant(index) }]),
      });
      const { batch_id: id, events_url: eventsUrl } = (await created.json()) as S["BatchCreated"];
      await (await fetch(`${quiet.url}${eventsUrl}`, { headers: { "X-Persona": ASHA } })).text();
      const batch = (await (
        await fetch(`${quiet.url}/v1/batches/${id}`, { headers: { "X-Persona": ASHA } })
      ).json()) as BatchView;
      expect(batch.claims[0]).toMatchObject({ status: "ready", open_questions: [] });
    } finally {
      await quiet.close();
    }
  });

  it("re-runs the policy when an answer arrives: a headcount can add a finding and change the route", async () => {
    const index = findVariant(
      (plan) =>
        plan.decisions.category === "client_entertainment" &&
        plan.decisions.alcohol_present < 0.5 &&
        (plan.receipt.total ?? 0) >= 5200,
    );
    const file = [{ name: "dinner.png", data: pngVariant(index) }];
    api.reset();
    const few = claimOf(await runBatch(P001, file), "Client dinner");
    const many = claimOf(await runBatch(P005, file), "Client dinner");
    expect(few.open_questions.map((q) => q.kind)).toEqual(["attendees", "business_purpose"]);
    expect(few.findings).toEqual([]);

    // Two people at more than 2,500 each: over the per-head cap (clause 5.2).
    const over = await as(P001).post<ReplyOut>(`/v1/claims/${few.id}/reply`, {
      text: "Neha Rao; renewal talks",
    });
    expect(over.body.claim.status).toBe("ready");
    const finding = over.body.claim.findings[0];
    expect(finding).toMatchObject({
      code: "entertainment_over_cap",
      severity: "warn",
      source: "policy",
      clause_id: "5.2",
      clause_text: POLICY_CLAUSES["5.2"]?.text,
      expected: 2500,
      document_id: few.document_ids[0],
    });
    expect(finding?.message).toMatch(
      /^This client entertainment costs \u20b9[\d,.]+ per head \(\u20b9[\d,.]+ for 2 people, alcohol excluded\), above the \u20b92,500 per-head cap\.$/,
    );
    expect(over.body.claim.route).toBe("finance_review");

    // Five people: well under the cap, nothing flagged, and a small clean claim goes straight through.
    const fine = await as(P005).post<ReplyOut>(`/v1/claims/${many.id}/reply`, {
      text: "Neha Rao, Rohan Kapoor, Priya Nair, Sameer Joshi; renewal talks",
    });
    expect(fine.body.claim.findings).toEqual([]);
    expect(fine.body.claim).toMatchObject({ status: "ready", route: "auto_approve" });

    // Correcting the answer re-runs the policy again, both ways.
    const attendees = few.open_questions[0]?.id ?? "";
    const fixed = await as(P001).post<ClaimView>(`/v1/claims/${few.id}/answers`, {
      answers: { [attendees]: "Neha Rao, Rohan Kapoor, Priya Nair, Sameer Joshi, Anita Desai" },
    });
    expect(fixed.body.findings).toEqual([]);
    expect(fixed.body.route).toBe("auto_approve");
    const again = await as(P001).post<ClaimView>(`/v1/claims/${few.id}/answers`, {
      answers: { [attendees]: "just the two of us" }, // a headcount, not names
    });
    expect(again.body.findings.map((f) => f.code)).toEqual(["entertainment_over_cap"]);
    expect(again.body.findings[0]?.message).toContain("for 2 people");
    expect(again.body.route).toBe("finance_review");
    const six = await as(P001).post<ClaimView>(`/v1/claims/${few.id}/answers`, {
      answers: { [attendees]: "party of six" },
    });
    expect(six.body.findings).toEqual([]);
    expect(six.body).toMatchObject({ status: "ready", route: "auto_approve" });
  });
});

// =====================================================================================================
describe("drift guards (checked against the backend files when they exist)", () => {
  const read = (relative: string) => readFileSync(new URL(relative, REPO_ROOT), "utf8");
  const has = (relative: string) => existsSync(new URL(relative, REPO_ROOT));

  it.skipIf(!has("services/api/config/policy.yaml"))(
    "quotes policy.yaml's clause texts verbatim",
    () => {
      const lines = read("services/api/config/policy.yaml").split(/\r?\n/);
      const found = new Map<string, { title: string; text: string }>();
      for (let i = 0; i < lines.length; i += 1) {
        const id = /^\s+- id: "([\d.]+)"/.exec(lines[i] ?? "")?.[1];
        if (!id) continue;
        let title = "";
        let text = "";
        for (let j = i + 1; j < lines.length && !/^\s+- id:/.test(lines[j] ?? ""); j += 1) {
          const line = lines[j] ?? "";
          const t = /^\s+title: (.*)$/.exec(line);
          if (t) title = t[1] ?? "";
          if (/^\s+text: >-\s*$/.test(line)) {
            const parts: string[] = [];
            for (let k = j + 1; k < lines.length && /^\s{6}\S/.test(lines[k] ?? ""); k += 1)
              parts.push((lines[k] ?? "").trim());
            text = parts.join(" ");
          }
        }
        found.set(id, { title, text });
      }
      expect([...found.keys()]).toEqual(Object.keys(POLICY_CLAUSES));
      for (const [id, clause] of found) expect(POLICY_CLAUSES[id], id).toEqual(clause);
    },
  );

  it.skipIf(!has("data/synth/fixtures/truth/s42-0002.json"))(
    "copies the sample receipts from the ground truth",
    () => {
      for (const [key, truthId] of Object.entries(TRUTH_IDS)) {
        const truth = JSON.parse(read(`data/synth/fixtures/truth/${truthId}.json`)) as {
          receipt: unknown;
        };
        expect(TRUTH_RECEIPTS[key as keyof typeof TRUTH_RECEIPTS], key).toEqual(truth.receipt);
      }
    },
  );

  it.skipIf(!has("services/mcp-corp/seed/employees.json"))(
    "lists the personas of the corporate directory",
    () => {
      const directory = JSON.parse(read("services/mcp-corp/seed/employees.json")) as Array<
        Record<string, unknown>
      >;
      for (const employee of EMPLOYEES) {
        const source = directory.find((e) => e.id === employee.id);
        expect(source, employee.id).toBeDefined();
        for (const field of [
          "name",
          "employee_id",
          "grade",
          "base_city",
          "base_state_code",
        ] as const) {
          expect(employee[field], `${employee.id}.${field}`).toBe(source?.[field]);
        }
      }
    },
  );

  it.skipIf(!has("services/api/config/models.yaml"))(
    "mirrors the model routes of models.yaml",
    () => {
      const yaml = read("services/api/config/models.yaml").replace(/\r\n/g, "\n");
      const models = new Map<string, string>();
      for (const m of yaml.matchAll(/^ {2}(\w+):\s*(?:#.*)?\n\s+id: ([\w.-]+)/gm))
        models.set(m[1] ?? "", m[2] ?? "");
      const routes = [...yaml.matchAll(/^ {2}(\w+):\s*\{ model: (\w+),\s*effort: (\w+),/gm)].map(
        (m) => ({
          route: m[1],
          model_key: m[2],
          model_id: models.get(m[2] ?? ""),
          effort: m[3],
          overridden: false,
        }),
      );
      expect(META.routes).toEqual(routes);
    },
  );

  it("agrees with the ported GST checks on every sample receipt", () => {
    const expected: Record<string, string[]> = {
      hotel: ["items_subtotal_mismatch:high"],
      wfh: ["total_mismatch:high", "gst_rate_mismatch:warn"],
    };
    for (const [key, receipt] of Object.entries(TRUTH_RECEIPTS)) {
      expect(codes(checkGst(receipt)), key).toEqual(expected[key] ?? []);
    }
    // the arithmetic findings of a tampered bill, as the backend words them
    expect(checkGst(TRUTH_RECEIPTS.hotel)[0]?.message).toBe(
      "Line items add up to ₹22,100.00 but the subtotal says ₹21,600.00.",
    );
    const missingLine = { ...TRUTH_RECEIPTS.hotel, subtotal: 23000, total: 24150 };
    expect(checkGst(missingLine)[0]).toMatchObject({
      code: "items_incomplete",
      severity: "warn",
      message:
        "The items read add up to ₹22,100.00, ₹900.00 less than the subtotal of ₹23,000.00; a line may be missing.",
    });
    const guessed = { ...TRUTH_RECEIPTS.hotel, low_confidence_fields: ["line_items"] };
    expect(checkGst(guessed)[0]).toMatchObject({
      code: "items_subtotal_mismatch",
      severity: "warn",
    });
    expect(checkGst(guessed)[0]?.message.endsWith("compare it with the original.")).toBe(true);
    const badGstin = { ...TRUTH_RECEIPTS.cab, merchant_gstin: "29MMBPY7409A9ZX" };
    expect(checkGst(badGstin).map((f) => f.code)).toEqual(["gstin_invalid_checksum"]);
    const noGstin = { ...TRUTH_RECEIPTS.cab, merchant_gstin: null };
    expect(checkGst(noGstin).map((f) => f.code)).toEqual(["gstin_missing"]);
  });

  it("gives the sample GSTINs valid checksums with the ported algorithm", () => {
    for (const receipt of Object.values(TRUTH_RECEIPTS)) {
      if (!receipt.merchant_gstin) continue;
      const gstin = receipt.merchant_gstin;
      expect(checksumChar(gstin.slice(0, 14)), gstin).toBe(gstin.charAt(14));
    }
  });

  it("keeps every box inside the page", () => {
    for (const key of ["hotel", "cab", "flight"]) {
      const boxes = boxesFor(key);
      expect(Object.keys(boxes)).toEqual(
        expect.arrayContaining([
          "merchant_name",
          "merchant_gstin",
          "invoice_number",
          "date",
          "subtotal",
          "total",
          "line_items",
        ]),
      );
      for (const box of Object.values(boxes)) {
        expect(box.x + box.w).toBeLessThanOrEqual(1);
        expect(box.y + box.h).toBeLessThanOrEqual(1);
      }
    }
    expect(boxesFor("fuel")).toEqual({});
    expect(boxesFor(null)).toEqual({});
  });
});

// =====================================================================================================
describe("the multipart parser", () => {
  const BOUNDARY = "----mockBoundary7MA4YWxkTrZu0gW";

  interface PartSpec {
    name: string;
    filename?: string;
    data: Uint8Array | string;
  }

  function bodyOf(
    parts: PartSpec[],
    options: { closing?: boolean; preamble?: string; epilogue?: string } = {},
  ) {
    const pieces: Buffer[] = [];
    if (options.preamble) pieces.push(Buffer.from(options.preamble));
    for (const part of parts) {
      const filename = part.filename === undefined ? "" : `; filename="${part.filename}"`;
      pieces.push(
        Buffer.from(
          `--${BOUNDARY}\r\nContent-Disposition: form-data; name="${part.name}"${filename}\r\n` +
            "Content-Type: application/octet-stream\r\n\r\n",
        ),
        Buffer.from(part.data),
        Buffer.from("\r\n"),
      );
    }
    if (options.closing !== false) pieces.push(Buffer.from(`--${BOUNDARY}--\r\n`));
    if (options.epilogue) pieces.push(Buffer.from(options.epilogue));
    return Buffer.concat(pieces);
  }

  function requestOf(payload: Buffer, chunkSize: number, contentType?: string) {
    const pieces: Buffer[] = [];
    for (let i = 0; i < payload.length; i += chunkSize)
      pieces.push(payload.subarray(i, i + chunkSize));
    return Object.assign(Readable.from(pieces), {
      headers: { "content-type": contentType ?? `multipart/form-data; boundary=${BOUNDARY}` },
    }) as never;
  }

  const limits = { field: "files", maxFiles: 30, maxFileBytes: 1_000_000 };
  const everyByte = Buffer.from(Array.from({ length: 3000 }, (_, i) => i % 256));
  // content that looks like the start of a delimiter must not end the part
  const lookalike = Buffer.from(`line\r\n--${BOUNDARY.slice(0, 12)}xx\r\n--\r\n-\r\n\r\n`);

  it("reads the same files whatever the chunking, byte-exact", async () => {
    const payload = bodyOf(
      [
        { name: "note", data: "just a field" },
        { name: "files", filename: "a b.png", data: everyByte },
        { name: "files", filename: "résumé.pdf", data: lookalike },
        { name: "files", filename: "empty.bin", data: "" },
        { name: "files", data: "a text field called files is not a file" },
      ],
      { preamble: "this is ignored\r\n", epilogue: "so is this" },
    );
    for (const size of [1, 2, 3, 5, 17, 64, 1000, payload.length]) {
      const { files, count } = await readUploads(requestOf(payload, size), limits);
      expect(count, `chunks of ${size}`).toBe(3);
      expect(files.map((f) => f.filename)).toEqual(["a b.png", "résumé.pdf", "empty.bin"]);
      expect(files[0]?.data.equals(everyByte)).toBe(true);
      expect(files[1]?.data.equals(lookalike)).toBe(true);
      expect(files[2]?.data.length).toBe(0);
      expect(files.some((f) => f.tooLarge)).toBe(false);
    }
  });

  it("keeps only what it needs of big and surplus files", async () => {
    const parts = Array.from({ length: 5 }, (_, i) => ({
      name: "files",
      filename: `f${i}.png`,
      data: Buffer.alloc(i === 1 ? 10 : 4, i + 65),
    }));
    const { files, count } = await readUploads(requestOf(bodyOf(parts), 7), {
      field: "files",
      maxFiles: 3,
      maxFileBytes: 4,
    });
    expect(count).toBe(5); // every file part is counted ...
    expect(files).toHaveLength(3); // ... but only the first three are kept
    expect(files.map((f) => f.tooLarge)).toEqual([false, true, false]);
    expect(files[1]?.data).toHaveLength(5); // one byte over the limit is enough to know
  });

  it("refuses bodies that are not well-formed multipart", async () => {
    const good = bodyOf([{ name: "files", filename: "a.png", data: "x" }]);
    await expect(readUploads(requestOf(good, 9, "application/json"), limits)).rejects.toThrow(
      MultipartError,
    );
    await expect(readUploads(requestOf(good, 9, "multipart/form-data"), limits)).rejects.toThrow(
      "missing boundary",
    );
    const open = bodyOf([{ name: "files", filename: "a.png", data: "x" }], { closing: false });
    await expect(readUploads(requestOf(open, 9), limits)).rejects.toThrow("ended early");
    await expect(readUploads(requestOf(Buffer.alloc(0), 9), limits)).rejects.toThrow("ended early");
  });

  it("answers a truncated upload with 400 like FastAPI does", async () => {
    const res = await fetch(`${api.url}/v1/batches`, {
      method: "POST",
      headers: { "X-Persona": ASHA, "Content-Type": `multipart/form-data; boundary=${BOUNDARY}` },
      body: bodyOf([{ name: "files", filename: "a.png", data: TINY_PNG }], { closing: false }),
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ detail: "There was an error parsing the body" });
  });
});

// =====================================================================================================
describe("helpers", () => {
  it("formats rupees like the backend", () => {
    expect(formatInr(10450)).toBe("\u20b910,450");
    expect(formatInr(122420)).toBe("\u20b91,22,420");
    expect(formatInr(651.5)).toBe("\u20b9651.50");
    expect(formatInr(7700)).toBe("\u20b97,700");
    expect(rupees(829.5)).toBe("\u20b9830"); // Python rounds halves to even
    expect(rupees(830.5)).toBe("\u20b9830");
    expect(roundHalfEven(2.5)).toBe(2);
    expect(roundHalfEven(3.5)).toBe(4);
    expect(roundHalfEven(672.74)).toBe(673);
  });

  it("writes date ranges with an en dash", () => {
    expect(dateRange("2026-08-15", "2026-08-18")).toBe("15\u201318 Aug 2026");
    expect(dateRange("2026-07-30", "2026-08-02")).toBe("30 Jul\u20132 Aug 2026");
    expect(dateRange("2026-08-12", "2026-08-12")).toBe("12 Aug 2026");
  });

  it("sniffs magic bytes and cleans names", () => {
    expect(sniffMediaType(Buffer.from([0xff, 0xd8, 0xff, 0x00]))).toBe("image/jpeg");
    expect(sniffMediaType(Buffer.from([0x89, 0x50, 0x4e, 0x47]))).toBe("image/png");
    expect(sniffMediaType(Buffer.from("%PDF-1.7"))).toBe("application/pdf");
    expect(sniffMediaType(Buffer.from("RIFF\u0000\u0000\u0000\u0000WEBPVP8 "))).toBe("image/webp");
    expect(sniffMediaType(Buffer.from("RIFF\u0000\u0000\u0000\u0000WAVEfmt "))).toBeNull();
    expect(sniffMediaType(Buffer.alloc(0))).toBeNull();
    expect(safeName("C:\\Users\\me\\scan 1.png", 0)).toBe("scan 1.png");
    expect(safeName("  ", 2)).toBe("receipt-3");
    expect(safeName(null, 0)).toBe("receipt-1");
    expect(safeName("x".repeat(300), 0)).toHaveLength(255);
    expect(parseParams('form-data; name="files"; filename="a \\"b\\"; c.png"')).toEqual({
      name: "files",
      filename: 'a "b"; c.png',
    });
    expect(parseParams("form-data; name=files; filename*=UTF-8''r%C3%A9.png")["filename*"]).toBe(
      "UTF-8''r%C3%A9.png",
    );
  });

  it("generates the same random numbers for the same digest", () => {
    const a = makeRng("0123456789abcdef0123456789abcdef");
    const b = makeRng("0123456789abcdef0123456789abcdef");
    expect(Array.from({ length: 5 }, () => a.next())).toEqual(
      Array.from({ length: 5 }, () => b.next()),
    );
    expect(makeRng("ffffffffffffffffffffffffffffffff").int(3, 3)).toBe(3);
  });
});

// =====================================================================================================
describe("coverage of the contract", () => {
  it("exercised every operation of openapi.json", () => {
    const all = OPERATIONS.map((o) => `${o.method} ${o.template}`).sort();
    expect(all).toContain("POST /v1/demo/reset"); // the contract has 21 operations now
    expect([...exercised].sort()).toEqual(all);
  });

  it("reports the statuses the backend answers with but openapi.json does not declare", () => {
    // Informational: openapi.json declares only some of the problems (e.g. 401 on /v1/me only).
    console.info(`undeclared problem statuses seen: ${[...undeclared].sort().join(", ")}`);
    expect(undeclared.size).toBeGreaterThanOrEqual(0);
  });
});
