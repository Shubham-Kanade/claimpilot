// @ts-check
/**
 * ClaimPilot MOCK API: a faithful, in-memory, zero-dependency stand-in for the FastAPI backend
 * (services/api), for local web development, hermetic Playwright runs and demo recordings.
 *
 * RUN
 *   node mock-api/server.mjs                         # http://localhost:8000
 *   PORT=8123 MOCK_SPEED=8 node mock-api/server.mjs  # E2E: every simulated delay is 8x shorter
 *   point the web app at it with NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
 *
 *   Environment: PORT (default 8000), MOCK_SPEED (default 1; divides every delay),
 *   MOCK_MAX_FILE_BYTES (default 15 MB), MOCK_CATEGORY_QUESTIONS=0 (do not ask what an unsure
 *   document was for), MOCK_DEMO=0 (not a demo: /v1/meta says demo false and "start over" is
 *   404), MOCK_DEMO_STRICT=1 (read ONLY the recorded sample receipts), MOCK_LOG=1 (one line per
 *   request).
 *   Prints `ClaimPilot mock API listening on http://localhost:8000` and stops cleanly on
 *   SIGINT / SIGTERM.
 *
 * EMBED (tests)
 *   import { createMockApi } from "./mock-api/server.mjs";
 *   const api = await createMockApi({ port: 0, speed: 50 });   // { server, url, port, reset, close }
 *   await fetch(`${api.url}/v1/employees`);
 *   api.reset();   // same as POST /__mock/reset: back to the seed, counters included
 *   await api.close();
 *   Options: port (0 = any free port), speed, samplesDir (default ./fixtures),
 *   maxFileBytes (default 15 MB; lower it to test 413 quickly), maxFiles (30), categoryQuestions,
 *   demo (default true), demoStrict (default false), log.
 *   It listens on every interface (dual stack), so http://localhost:PORT and
 *   http://127.0.0.1:PORT both work; CORS reflects http://localhost:<port> and
 *   http://127.0.0.1:<port> origins only.
 *
 * CONTRACT
 *   All 21 endpoints of openapi.json with the same shapes and status codes (the response types are
 *   checked against the generated TypeScript types by `npx tsc --noEmit`, and every response is
 *   validated against the OpenAPI schemas by mock-api.contract.test.ts). Errors are
 *   application/problem+json {type, title, status, detail?} with the backend's `type` codes:
 *   missing_persona, unknown_persona (401); approver_only, not_your_claim (403); batch_not_found,
 *   claim_not_found, document_not_found, file_unavailable (404); no_files, unsupported_files,
 *   confirmation_required, unknown_question, blank_answer, comment_required (422); too_many_files,
 *   file_too_large (413); claim_locked, claim_not_ready, claim_not_submitted (409); demo_disabled
 *   (404). Framework-level validation errors are FastAPI's {"detail": [...]} (422).
 *   Only /healthz, /readyz, /v1/employees and /v1/meta need no persona; everything else needs
 *   `X-Persona: <employee id>`. DEMO-RAVI is the only approver and can read everything; everybody
 *   else sees only their own batches, claims and documents (404, never 403, for the rest).
 *   Non-contract: POST /__mock/reset -> 204 (restores the whole seed, counters included).
 *   Demo mode (default on; `demo: true` in /v1/meta, `runtime: "embedded"`): POST /v1/demo/reset
 *   ("start over") deletes ALL of the acting employee's batches, documents and claims, seeded ones
 *   included, and answers {batches, documents, claims}; the approver (DEMO-RAVI) deletes
 *   everyone's. With MOCK_DEMO=0 it is a 404 problem `demo_disabled` ("Start over is for the demo
 *   only"). The statistics follow, being derived from what is left.
 *
 * PERSONAS (GET /v1/employees, in this order; the UI defaults to the first non-approver)
 *   DEMO-ASHA   Asha Menon      L3  Pune        (default demo persona)
 *   P001        Advika Hayer    L4  Hyderabad
 *   P005        Advik Kaur      L4  Panaji
 *   DEMO-MEERA  Meera Shah      L2  Mumbai
 *   DEMO-RAVI   Ravi Iyer       L5  Bengaluru   (approver)
 *
 * SIMULATION
 *   POST /v1/batches parses multipart itself (field `files`, repeated), sniffs magic bytes and
 *   answers 202 {batch_id: bat-000001, status: "queued", documents: [{id: doc-000001, filename}],
 *   events_url}. The batch then plays on server-side timers whether or not anyone listens:
 *   batch_started (+250 ms), then per document 500..1500 ms apart document_extracted and 350 ms
 *   later document_checked, then claims_ready and batch_done. Late SSE clients get every missed
 *   event first (GET /v1/batches/{id}/events honours Last-Event-ID; /history?after=N returns the
 *   same events as JSON).
 *   Hooks in file names (case-insensitive): blur, corrupt or unreadable make that document fail
 *   (document_failed, no checked event; when all fail the batch still ends with an empty
 *   claims_ready and batch_done); batch-fail makes the whole batch fail (batch_failed).
 *   Files are recognised by the sha256 of their bytes (the ten files of mock-api/fixtures, whatever
 *   they are called) and read as scripted in samples.mjs; any other JPEG/PNG/WebP/PDF is read as a
 *   plausible receipt generated deterministically from its hash (generate.mjs). The same bytes
 *   read earlier in the run are `cached` (cost 0); a second upload of the same bytes by the same
 *   persona is flagged duplicate_exact and joins the original's claim. With MOCK_DEMO_STRICT=1
 *   every file that is not byte-identical to a sample fails (document_failed: "This demo reads
 *   only its recorded sample receipts. To read your own, run ClaimPilot with your own API key
 *   (see the README).").
 *   Costs are about $0.0004 to $0.0009 per read; engine is "jev", or "llm" under 0.7 confidence.
 *
 * SEED (restored by reset; documents doc-seed-NN, batches bat-seed-NN; live ids start at 1)
 *   P005   Hyderabad trip 7 Aug 2026         submitted FIN-2026-000001  auto_approve
 *   MEERA  Chandigarh trip 15-18 Aug 2026    submitted FIN-2026-000002  finance_review
 *   P001   Learning 18 Jul 2026              submitted FIN-2026-000003  finance_review
 *   P001   Fuel Jul 2026                     approved  FIN-2026-000004  auto_approve
 *   MEERA  WFH supplies Aug 2026             rejected  FIN-2026-000005  finance_review
 *   ASHA   Client dinner 6 Oct 2026          ready, both questions answered from the calendar
 *   The next submission is FIN-2026-000006. /v1/stats adds this history (47 documents, 9 claims ...)
 *   to what happens in the run.
 *
 * SAMPLE UPLOAD (the ten files plus a renamed copy of the cab receipt, as DEMO-ASHA)
 *   10 claims, in (start date, mode, title) order: Fuel Jul 2026; Miscellaneous
 *   Jul 2026 (confirm_personal); Client dinner 12 Jul 2026 (attendees, business_purpose);
 *   Bengaluru trip 12 Jul 2026 (business_purpose; two documents, the copy is duplicate_exact);
 *   Learning 18 Jul 2026; Local conveyance Jul 2026; Hyderabad trip 7 Aug 2026 (business_purpose);
 *   Chandigarh trip 15-18 Aug 2026 (business_purpose; items_subtotal_mismatch and hotel_over_cap
 *   for the persona's grade); WFH supplies Aug 2026; Mobile & internet Sep 2026. See samples.mjs.
 *
 * DEVIATIONS FROM THE REAL BACKEND (on purpose)
 *   - Duplicates are detected per persona only, and only among files uploaded in this run.
 *   - Claims of a batch are listed in grouping order (not by id); BatchView counters
 *     (processed, failed) are set when the batch is done, like the backend.
 *   - When ALL documents fail the stream still has claims_ready (empty) before batch_done.
 *   - Known samples follow the scripted scenario (claim titles and their grouping, the photo-date
 *     and prompt-injection findings); their arithmetic and GST findings are computed by a port of
 *     the backend's checks (trust/gst.py), which agrees with the script.
 *   - The reply splitter, the policy rules (4.1, 5.1, 5.2, 6.1, 8.1, 9.1, 10.1) and the calendar
 *     are deterministic re-implementations; rules 2.1, 3.1 and 7.x are not enforced. Like the
 *     backend, answers re-run the policy (answering the attendees of a client dinner can add the
 *     per-head finding and move the claim to finance review).
 *   - /readyz keeps reporting redis and postgres although /v1/meta says runtime "embedded".
 *   - PNG and PDF are recognised by their first 4 bytes (the backend checks 8 and 5).
 */

import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { LIMITS } from "./data.mjs";
import { handle } from "./routes.mjs";
import { loadSamples } from "./samples.mjs";
import { createState } from "./state.mjs";

/** @typedef {import("./types.mjs").Context} Context */

/**
 * @typedef {Object} MockApi
 * @property {http.Server} server
 * @property {string} url  `http://localhost:PORT`
 * @property {number} port
 * @property {() => void} reset  Back to the seed, counters included.
 * @property {() => Promise<void>} close  Stops every timer and stream and closes the server.
 */

/**
 * @typedef {Object} MockApiOptions
 * @property {number} [port]  0 (the default) picks a free port.
 * @property {number} [speed]  Divides every simulated delay (default 1).
 * @property {string} [samplesDir]  Where the sample files are (default ./fixtures).
 * @property {number} [maxFileBytes]  Per-file upload limit (default 15 MB).
 * @property {number} [maxFiles]  Files per upload (default 30).
 * @property {boolean} [categoryQuestions]  Ask what an unsure document (under 0.7 confidence)
 *   was for (default true).
 * @property {boolean} [demo]  Demo mode (default true): /v1/meta says `demo: true` and
 *   POST /v1/demo/reset ("start over") works; when false it answers 404 `demo_disabled`.
 * @property {boolean} [demoStrict]  Like the hosted demo, read ONLY the recorded sample receipts:
 *   any other file fails with `document_failed` (default false: other files get generated receipts).
 * @property {boolean} [log]  Print one line per request (METHOD path status time).
 * @property {() => Date} [now]  The clock (tests).
 */

const DEFAULT_SAMPLES_DIR = fileURLToPath(new URL("./fixtures/", import.meta.url));

/**
 * Start the mock API.
 * @param {MockApiOptions} [options]
 * @returns {Promise<MockApi>}
 */
export async function createMockApi(options = {}) {
  const port = options.port ?? 0;
  const speed = options.speed !== undefined && options.speed > 0 ? options.speed : 1;
  const samples = loadSamples(options.samplesDir ?? DEFAULT_SAMPLES_DIR);
  if (samples.missing.length) {
    console.warn(
      `mock-api: sample files not found in ${samples.dir}: ${samples.missing.join(", ")}`,
    );
  }

  /** @type {Context} */
  const ctx = {
    state: createState(samples),
    samples,
    speed,
    abort: new AbortController(),
    now: options.now ?? (() => new Date()),
    config: {
      maxFileBytes: options.maxFileBytes ?? LIMITS.maxUploadBytes,
      maxFiles: options.maxFiles ?? LIMITS.maxBatchFiles,
      categoryQuestions: options.categoryQuestions ?? true,
      demo: options.demo ?? true,
      demoStrict: options.demoStrict ?? false,
    },
    streams: new Set(),
  };

  const stopRunning = () => {
    ctx.abort.abort();
    for (const end of [...ctx.streams]) end();
  };
  const reset = () => {
    stopRunning();
    ctx.abort = new AbortController();
    ctx.state = createState(samples);
  };

  const server = http.createServer((req, res) => {
    if (options.log) {
      const started = Date.now();
      res.once("close", () => {
        console.log(`${req.method} ${req.url} ${res.statusCode} ${Date.now() - started}ms`);
      });
    }
    handle(ctx, req, res, reset).catch((error) => {
      console.error("mock-api: request failed", error);
      res.destroy();
    });
  });
  // Longer than the client's idle timeout, so a reused connection is never closed under a request.
  server.keepAliveTimeout = 65_000;
  server.headersTimeout = 66_000;
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    // No host: listens on `::` (dual stack), so localhost and 127.0.0.1 both work.
    server.listen(port, () => {
      server.off("error", reject);
      resolve(undefined);
    });
  });
  const address = server.address();
  const actualPort = typeof address === "object" && address ? address.port : port;

  return {
    server,
    url: `http://localhost:${actualPort}`,
    port: actualPort,
    reset,
    close: () =>
      new Promise((resolve) => {
        stopRunning();
        server.close(() => resolve());
        server.closeAllConnections();
      }),
  };
}

// --- command line --------------------------------------------------------------------------------------

/**
 * Was this file started directly (`node mock-api/server.mjs`) rather than imported?
 */
function isMain() {
  const entry = process.argv[1];
  if (!entry) return false;
  try {
    const normalise = (/** @type {string} */ p) => {
      const real = path.resolve(fs.realpathSync(p));
      return process.platform === "win32" ? real.toLowerCase() : real;
    };
    return normalise(entry) === normalise(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
}

/**
 * @param {string | undefined} value
 * @param {number} fallback
 */
function numberFrom(value, fallback) {
  const parsed = value ? Number(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : fallback;
}

async function main() {
  const api = await createMockApi({
    port: numberFrom(process.env.PORT, 8000),
    speed: numberFrom(process.env.MOCK_SPEED, 1),
    maxFileBytes: numberFrom(process.env.MOCK_MAX_FILE_BYTES, LIMITS.maxUploadBytes),
    categoryQuestions: process.env.MOCK_CATEGORY_QUESTIONS !== "0",
    demo: process.env.MOCK_DEMO !== "0",
    demoStrict: process.env.MOCK_DEMO_STRICT === "1",
    log: process.env.MOCK_LOG === "1",
  });
  console.log(`ClaimPilot mock API listening on ${api.url}`);
  const stop = () => {
    void api.close().then(() => process.exit(0));
  };
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
}

if (isMain()) {
  main().catch((error) => {
    console.error(`mock-api: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(1);
  });
}
