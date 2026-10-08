// `npm run e2e`: run the Playwright suite against a running demo stack.
//
// The suite tests the real thing, the one-container demo image (the app at / and the API at
// /api, LLM answers replayed from recordings, so it costs nothing). Nothing is built or started
// here: start the image first (see README.md), then
//
//   npm run e2e                                  # E2E_BASE_URL defaults to http://localhost:7860
//   E2E_BASE_URL=http://localhost:7861 npm run e2e
//   npm run e2e -- --project=desktop -g "approve"   # extra arguments go to Playwright
//
// Env: E2E_BASE_URL (app, default http://localhost:7860), E2E_API_URL (default <base>/api, for a
// dev server on :3000 talking to an API elsewhere), PW_CHANNEL=msedge|chrome|chromium picks the
// browser (locally the installed Edge or Chrome; CI=true uses Playwright's bundled chromium),
// SCREENSHOTS=1 also runs the screenshot spec (saved to test-results/screens).
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL("..", import.meta.url));
const base = (process.env.E2E_BASE_URL ?? "http://localhost:7860").replace(/\/+$/, "");

console.log(`e2e: testing ${base} (API ${process.env.E2E_API_URL ?? `${base}/api`})`);
const result = spawnSync(
  process.execPath,
  [require.resolve("@playwright/test/cli"), "test", ...process.argv.slice(2)],
  { cwd: root, stdio: "inherit", env: process.env },
);
if (result.status !== 0) {
  console.error(`\nplaywright failed (exit ${result.status ?? "signal"})`);
  process.exit(result.status ?? 1);
}
