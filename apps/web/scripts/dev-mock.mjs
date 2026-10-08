// Runs the in-memory mock API and `next dev` together, so the whole UI works without the backend.
//
//   npm run dev:mock
//
// Env: MOCK_PORT (default 8000), WEB_PORT (default 3000), MOCK_SPEED (default 1; 0.5 = slower
// simulated processing, 4 = faster). Ctrl+C stops both.
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL("..", import.meta.url));
const mockPort = process.env.MOCK_PORT ?? "8000";
const webPort = process.env.WEB_PORT ?? "3000";
const nextBin = require.resolve("next/dist/bin/next");

const children = [
  spawn(process.execPath, ["mock-api/server.mjs"], {
    cwd: root,
    stdio: "inherit",
    env: { ...process.env, PORT: mockPort },
  }),
  spawn(process.execPath, [nextBin, "dev", "-p", webPort], {
    cwd: root,
    stdio: "inherit",
    env: { ...process.env, NEXT_PUBLIC_API_BASE_URL: `http://localhost:${mockPort}` },
  }),
];

let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) child.kill();
  setTimeout(() => process.exit(code), 300).unref();
}

for (const child of children) {
  child.on("exit", (code) => stop(code ?? 0));
}
process.on("SIGINT", () => stop(0));
process.on("SIGTERM", () => stop(0));

console.log(
  `\nClaimPilot UI on http://localhost:${webPort}  (mock API on http://localhost:${mockPort})\n`,
);
