import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";

/**
 * End-to-end tests against the REAL stack: the one-container demo image (Caddy -> this app +
 * the FastAPI backend with LLM answers replayed from recordings, so nothing here costs money):
 *
 *   docker build -f deploy/hf-space/Dockerfile --build-arg SOURCE=fetch-local -t claimpilot-demo .
 *   docker run -d --name cpdemo -p 7860:7860 claimpilot-demo
 *   npm run e2e                       # E2E_BASE_URL defaults to http://localhost:7860
 *
 * The app is served at / and the API at /api. Playwright starts nothing itself. To test a dev
 * server instead: `E2E_BASE_URL=http://localhost:3000 E2E_API_URL=http://localhost:7860/api`
 * (the API's CORS allows localhost:3000). Every test starts from "Start over" (POST
 * /v1/demo/reset), so tests run one at a time against the one shared stack.
 *
 * Browser: locally the installed Microsoft Edge (Chromium downloads are blocked behind the
 * corporate proxy), falling back to Chrome; with CI=true Playwright's bundled chromium
 * (`npx playwright install --with-deps chromium`). Override with PW_CHANNEL=msedge|chrome|chromium.
 */
const BASE_URL = (process.env.E2E_BASE_URL ?? "http://localhost:7860").replace(/\/+$/, "");

const EDGE_PATHS = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "/usr/bin/microsoft-edge",
  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
];

function browserChannel(): string | undefined {
  if (process.env.PW_CHANNEL) {
    return process.env.PW_CHANNEL === "chromium" ? undefined : process.env.PW_CHANNEL;
  }
  if (process.env.CI) return undefined; // bundled chromium (installed by `playwright install`)
  return EDGE_PATHS.some((path) => existsSync(path)) ? "msedge" : "chrome";
}

const channel = browserChannel();

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  globalSetup: "./e2e/global-setup.ts",
  // One shared stack with shared state (every test resets it first), so run serially.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  timeout: 120_000,
  expect: { timeout: 20_000 },
  use: {
    baseURL: BASE_URL,
    locale: "en-IN",
    timezoneId: "Asia/Kolkata",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // The suite checks UI motion-free states; this also keeps screenshots deterministic.
    reducedMotion: "reduce",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], channel, viewport: { width: 1280, height: 800 } },
    },
    {
      name: "mobile",
      use: {
        ...devices["Pixel 5"],
        channel,
        viewport: { width: 360, height: 740 },
        deviceScaleFactor: 1, // layout is what is tested; keeps screenshots readable
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
});
