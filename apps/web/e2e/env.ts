/** Where the stack under test lives (shared by the Playwright config hooks and the tests). */

/** The app: the one-container demo image serves it at / and the API under /api. */
export const BASE_URL = (process.env.E2E_BASE_URL ?? "http://localhost:7860").replace(/\/+$/, "");

/** The API, for the checks and resets the tests do behind the browser's back. */
export const API = (process.env.E2E_API_URL ?? `${BASE_URL}/api`).replace(/\/+$/, "");

/**
 * The demo sandbox every test works in. The API keeps each visitor's data apart by the X-Sandbox
 * header; the suite pins one id so the API calls made behind the browser's back and the browser
 * itself (localStorage `claimpilot.sandbox`, set by `actAs`) always see the same data.
 */
export const E2E_SANDBOX = "e2e-sandbox-00000000000001";

/** A different, random sandbox: another visitor of the same demo. */
export function randomSandbox(): string {
  return `visitor-${Math.random().toString(36).slice(2, 12)}${Date.now().toString(36)}`.padEnd(
    24,
    "0",
  );
}

/** Directory ids (GET /v1/employees) of the people the demo is about. */
export const PERSONA = {
  asha: "DEMO-ASHA", // Asha Menon, L3, Pune: owns the sample receipts and the calendar they use
  meera: "DEMO-MEERA", // Meera Shah, L2, Mumbai
  ravi: "DEMO-RAVI", // Ravi Iyer, L5: the approver
  advika: "P001", // Advika Hayer, L4: one of the golden-dataset employees
} as const;

/**
 * "Start over" for the whole e2e sandbox: an approver's reset empties every persona's uploads and
 * claims in that sandbox (never another sandbox), which is what a test needs to start from nothing.
 */
export async function resetEverything(): Promise<void> {
  const response = await fetch(`${API}/v1/demo/reset`, {
    method: "POST",
    headers: { "X-Persona": PERSONA.ravi, "X-Sandbox": E2E_SANDBOX },
    signal: AbortSignal.timeout(30_000),
  });
  if (!response.ok) {
    throw new Error(`POST ${API}/v1/demo/reset answered ${response.status}: not the demo stack?`);
  }
}
