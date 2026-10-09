import { API, BASE_URL, E2E_SANDBOX, resetEverything } from "./env";

interface Meta {
  demo?: boolean;
  llm_mode?: string;
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Runs once before the suite: make sure there is a demo stack to test (and that it cannot spend
 * money), start it empty, and leave it empty afterwards so scripts/smoke.py can run next without
 * tripping over duplicates of the receipts these tests uploaded.
 */
export default async function globalSetup(): Promise<() => Promise<void>> {
  const how =
    "Start the demo image first:\n" +
    "  docker build -f deploy/hf-space/Dockerfile --build-arg SOURCE=fetch-local -t claimpilot-demo .\n" +
    "  docker run -d --name cpdemo -p 7860:7860 claimpilot-demo\n" +
    "or point the suite at another one with E2E_BASE_URL (and E2E_API_URL if the API is elsewhere).";

  let meta: Meta;
  try {
    const response = await fetch(`${API}/v1/meta`, {
      headers: { "X-Sandbox": E2E_SANDBOX },
      signal: AbortSignal.timeout(20_000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    meta = (await response.json()) as Meta;
  } catch (error) {
    throw new Error(`No ClaimPilot API answers at ${API}/v1/meta (${describe(error)}).\n${how}`);
  }

  if (!meta.demo) {
    throw new Error(
      `${API} is not the public demo (GET /v1/meta says demo: false), so the sample receipts ` +
        `would not replay and "Start over" is not available.\n${how}`,
    );
  }
  if (meta.llm_mode === "live" && process.env.E2E_ALLOW_LIVE !== "1") {
    throw new Error(
      `${API} runs with LLM_MODE=live: every upload would call the paid model API. ` +
        `The suite only runs against recorded answers (LLM_MODE=replay). Set E2E_ALLOW_LIVE=1 ` +
        `if you really want that.`,
    );
  }

  try {
    const page = await fetch(`${BASE_URL}/`, { signal: AbortSignal.timeout(30_000) });
    if (!page.ok) throw new Error(`HTTP ${page.status}`);
  } catch (error) {
    throw new Error(
      `The API is up but the app does not answer at ${BASE_URL}/ (${describe(error)}).`,
    );
  }

  await resetEverything();
  return async () => {
    try {
      await resetEverything();
    } catch (error) {
      console.warn(`e2e: could not leave the demo stack empty: ${describe(error)}`);
    }
  };
}
