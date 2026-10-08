import { expect } from "@playwright/test";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { API, PERSONA } from "./env";

/**
 * The demo pile, driven through the API (not the UI). Most specs only need Asha's seven claims to
 * exist: uploading through the API takes a few seconds, uploading through the browser takes
 * three times that, and the upload flow itself is covered (once) by upload-flow.spec.ts.
 * The files are the very ones the "Try with sample receipts" button uploads: public/samples.
 */
// (the specs are CommonJS once Playwright has compiled them; the cwd is apps/web otherwise)
const E2E_DIR = typeof __dirname === "string" ? __dirname : path.join(process.cwd(), "e2e");
export const SAMPLES_DIR = path.resolve(E2E_DIR, "..", "public", "samples");

const MEDIA: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".pdf": "application/pdf",
};

/** The 15 sample files, in upload (file name) order. */
export const SAMPLE_FILES = readdirSync(SAMPLES_DIR)
  .filter((name) => MEDIA[path.extname(name)])
  .sort();

export interface ApiClaim {
  id: string;
  batch_id: string;
  title: string;
  status: string;
  route: string | null;
  total: number;
  document_ids: string[];
  findings: { code: string; severity: string; message: string }[];
  open_questions: { id: string; kind: string; text: string; answer: string | null }[];
  submission_reference: string | null;
}

/** A JSON call to the API as a persona (throws a readable error when it fails). */
export async function callApi<T = unknown>(
  method: "GET" | "POST",
  route: string,
  options: { persona?: string; body?: unknown; idempotencyKey?: string; ok?: number } = {},
): Promise<T> {
  const headers: Record<string, string> = { "X-Persona": options.persona ?? PERSONA.asha };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  const response = await fetch(`${API}${route}`, {
    method,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: AbortSignal.timeout(60_000),
  });
  const text = await response.text();
  expect(response.status, `${method} ${route}: ${text.slice(0, 300)}`).toBe(options.ok ?? 200);
  return (text ? JSON.parse(text) : null) as T;
}

/** Upload the whole pile as `persona` and wait until the batch is done (claims exist). */
export async function seedPile(persona: string = PERSONA.asha): Promise<ApiClaim[]> {
  const form = new FormData();
  for (const name of SAMPLE_FILES) {
    const bytes = readFileSync(path.join(SAMPLES_DIR, name));
    form.append("files", new Blob([bytes], { type: MEDIA[path.extname(name)] }), name);
  }
  const upload = await fetch(`${API}/v1/batches`, {
    method: "POST",
    headers: { "X-Persona": persona },
    body: form,
    signal: AbortSignal.timeout(60_000),
  });
  expect(upload.status, await upload.clone().text()).toBe(202);
  const { batch_id: batchId } = (await upload.json()) as { batch_id: string };

  const deadline = Date.now() + 90_000;
  for (;;) {
    const batch = await callApi<{ status: string; error?: string | null }>(
      "GET",
      `/v1/batches/${batchId}`,
      { persona },
    );
    if (batch.status === "done") break;
    if (batch.status === "failed") throw new Error(`The sample batch failed: ${batch.error}`);
    if (Date.now() > deadline) throw new Error("The sample batch did not finish in 90 s");
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return listClaims(persona);
}

export function listClaims(persona: string = PERSONA.asha): Promise<ApiClaim[]> {
  return callApi<ApiClaim[]>("GET", "/v1/claims", { persona });
}

/** The claim titled `title` (the demo has two "Client dinner 6 Oct 2026": pick with `where`). */
export async function findClaim(
  title: string,
  where: (claim: ApiClaim) => boolean = () => true,
  persona: string = PERSONA.asha,
): Promise<ApiClaim> {
  const claim = (await listClaims(persona)).find((c) => c.title === title && where(c));
  expect(claim, `no claim titled "${title}" for ${persona}`).toBeDefined();
  return claim as ApiClaim;
}

/** Answer every open question of a claim, as a cooperative employee would (no UI). */
export async function answerClaim(claim: ApiClaim, persona: string = PERSONA.asha) {
  const open = claim.open_questions.filter((q) => !q.answer);
  const answers = Object.fromEntries(
    open.map((q) => [
      q.id,
      q.kind === "business_purpose"
        ? "Client visit and quarterly review with Kestrel Logistics"
        : "Auto fare to the client office, no receipt was available",
    ]),
  );
  return callApi<ApiClaim>("POST", `/v1/claims/${claim.id}/answers`, {
    persona,
    body: { answers },
  });
}

/** Submit a ready claim with explicit confirmation (no UI). */
export function submitClaim(claim: ApiClaim, persona: string = PERSONA.asha) {
  return callApi<ApiClaim>("POST", `/v1/claims/${claim.id}/submit`, {
    persona,
    body: { confirmed: true },
    idempotencyKey: `e2e-${claim.id}`,
  });
}
