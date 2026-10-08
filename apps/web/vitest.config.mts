import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    css: false,
    // Keep failure output readable: Testing Library prints the DOM it searched, truncated here.
    env: { DEBUG_PRINT_LIMIT: "1200" },
    // Loading jsdom is slow on locked-down Windows laptops (virus scanners open every file), and
    // starting a fresh worker per test file would pay that for each of them. A handful of
    // long-lived workers keep the suite fast; every test cleans up after itself (src/test/setup.ts).
    maxWorkers: process.env.CI ? undefined : 4,
    // Two projects: the UI (jsdom + MSW, no real network) and the mock API's contract test,
    // which talks real HTTP to a server it starts itself (node, no MSW).
    projects: [
      {
        extends: true,
        test: {
          name: "web",
          environment: "jsdom",
          isolate: false,
          setupFiles: ["./src/test/setup.ts"],
          include: ["src/**/*.test.{ts,tsx}"],
        },
      },
      {
        extends: true,
        test: {
          name: "mock-api",
          environment: "node",
          include: ["mock-api/**/*.test.ts"],
        },
      },
    ],
    coverage: {
      provider: "v8",
      reporter: ["text-summary", "lcov", "json-summary"],
      include: ["src/**/*.{ts,tsx}"],
      // Route glue (layouts, pages, error/not-found boundaries, manifest) is a few lines each
      // and is exercised end to end by the Playwright suite in e2e/.
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/lib/api/schema.d.ts", "src/app/**"],
      thresholds: { lines: 80, statements: 80, functions: 80, branches: 75 },
    },
  },
});
