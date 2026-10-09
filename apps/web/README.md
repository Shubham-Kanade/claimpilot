# ClaimPilot web (`apps/web`)

The employee and approver UI for ClaimPilot, the AI expense and reimbursement agent: drop a pile
of receipts, watch them being read and checked, answer one combined question, confirm, submit.
Approvers review the evidence and decide.

**Stack:** Next.js 16.4 (App Router, `cacheComponents` + `partialPrefetching` on) · React 19 ·
TypeScript (strict) · Tailwind CSS v4 · TanStack Query · Vitest + React Testing Library + MSW ·
Playwright + axe. Only two UI dependencies beyond that: `lucide-react` (icons) and the Geist fonts.

> Next.js 16.4 differs from older versions. Before using a Next API read the matching guide in
> `node_modules/next/dist/docs/` (see `AGENTS.md`).

## Quick start

```bash
cd apps/web
npm install

# 1. The real thing, one container (app + API + recorded model answers; costs nothing):
#    from the repository root
docker build -f deploy/hf-space/Dockerfile --build-arg SOURCE=fetch-local -t claimpilot-demo .
docker run -d --name cpdemo -p 7860:7860 claimpilot-demo        # http://localhost:7860

# 2. The app in dev mode against that API (the API allows http://localhost:3000):
NEXT_PUBLIC_API_BASE_URL=http://localhost:7860/api npm run dev

# 3. No backend at all (in-memory mock API + the app, for UI work):
npm run dev:mock          # UI http://localhost:3000, mock API http://localhost:8000
```

Open the app and press **Try with sample receipts**. There is no login: the persona picker in the
header chooses who is acting (**Asha Menon**, an L3 employee, by default; **Ravi Iyer** is the
approver). The 15 sample receipts are Asha's week and the hosted demo replays recorded model
answers for her only, so in the demo the button first switches to her (with a toast), clears her
earlier uploads and then uploads the pile.

`npm run dev:mock` starts the mock API on `:8000`. If the real API (or Docker compose) already uses
that port, run `MOCK_PORT=8001 npm run dev:mock`.

### Environment variables

| Variable                   | Used by              | Default                                                | Meaning                                                                                                                      |
| -------------------------- | -------------------- | ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `NEXT_PUBLIC_API_BASE_URL` | the app (build time) | `http://localhost:8000`                                | API base. Absolute (`http://localhost:8000`) or **relative** (`/api`: one origin behind a reverse proxy that strips `/api`). |
| `NEXT_PUBLIC_README_URL`   | the app (build time) | the repository README                                  | Where the demo banner's "See the README" link goes.                                                                          |
| `MOCK_PORT` / `WEB_PORT`   | `dev:mock`           | `8000` / `3000`                                        | Ports for the mock API and `next dev`.                                                                                       |
| `MOCK_SPEED`               | mock API             | `1`                                                    | Divides every simulated delay (`0.5` = slower for a calm demo recording, `10` = test speed).                                 |
| `MOCK_DEMO`                | mock API             | on                                                     | `0` makes `/v1/meta` report `demo: false` (no banner, no "Start over").                                                      |
| `MOCK_DEMO_STRICT`         | mock API             | off                                                    | `1`: only the bundled sample receipts can be read, others fail with the hosted demo's message.                               |
| `E2E_BASE_URL`             | `npm run e2e`        | `http://localhost:7860`                                | The running demo stack to test: the app at `/`, the API at `/api`.                                                           |
| `E2E_API_URL`              | `npm run e2e`        | `<E2E_BASE_URL>/api`                                   | Only when the API is elsewhere (e.g. a dev server on :3000 talking to a container).                                          |
| `E2E_ALLOW_LIVE`           | `npm run e2e`        | unset                                                  | `1` lets the suite run against a stack with `LLM_MODE=live` (it refuses by default: it would spend money).                   |
| `PW_CHANNEL`               | Playwright           | `msedge` (else `chrome`); `CI=true` → bundled chromium | Which browser runs the E2E tests locally.                                                                                    |

`NEXT_PUBLIC_*` values are inlined at **build** time. The hosted demo builds with
`NEXT_PUBLIC_API_BASE_URL=/api`; a relative base is resolved against the page origin in the
browser (server rendering never calls the API), including for the SSE stream.

### Scripts

| Script                    | What it does                                                                             |
| ------------------------- | ---------------------------------------------------------------------------------------- |
| `npm run dev`             | `next dev`                                                                               |
| `npm run dev:mock`        | mock API + `next dev` together (`scripts/dev-mock.mjs`)                                  |
| `npm run mock`            | only the mock API (`node mock-api/server.mjs`)                                           |
| `npm run build` / `start` | production build / server (the Docker image uses the standalone output)                  |
| `npm run lint`            | ESLint (Next + React rules, including the React Compiler rules)                          |
| `npm run typecheck`       | `next typegen && tsc --noEmit`                                                           |
| `npm run format[:check]`  | Prettier (with the Tailwind class sorter)                                                |
| `npm test`                | Vitest, once (unit + component + mock-API contract tests)                                |
| `npm run test:coverage`   | the same with coverage; **fails below 80 % lines** (thresholds in `vitest.config.mts`)   |
| `npm run e2e`             | runs the Playwright suite against a running demo stack (`E2E_BASE_URL`, see Testing)     |
| `npm run e2e:install`     | `playwright install chromium` (CI; locally the installed Edge/Chrome is used instead)    |
| `npm run gen:api`         | regenerates `src/lib/api/schema.d.ts` from `openapi.json` (never edit that file by hand) |

## The API contract

`openapi.json` is exported by the backend; `src/lib/api/schema.d.ts` is generated from it. **API
types are never hand-written**: `src/lib/api/types.ts` only gives the generated types friendly
names, and `src/lib/api/client.ts` is a thin typed client. A change in the API therefore breaks
compilation (and the mock API's contract test) instead of breaking the UI at run time.

Errors are `application/problem+json`. They surface as one typed `ApiError` (`status`, `type`,
`title`, `detail`), and **one table** (`src/lib/api/problems.ts`) turns every `type` code into
friendly copy (`claim_not_ready`, `confirmation_required`, `comment_required`, `file_too_large`,
`unsupported_files`, 401 "pick a persona", network failures, ...).

## Screens

| Route           | What it is                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/`             | Upload. Drag and drop, file picker, **Take photo** on phones, limits mirrored from the API (30 files, 15 MB each), **Try with sample receipts** (Asha's week: 15 files, listed under "What is in the sample pile?"), how it works, impact so far, which AI does what.                                                                                                                                                                                                   |
| `/batches/[id]` | Live processing over SSE: one card per receipt (waiting → reading → read → checked), overall progress, elapsed time, running LLM cost; reload-safe (snapshot + history + stream from the last event).                                                                                                                                                                                                                                                                   |
| `/claims`       | My claims: cards with mode, dates, city, total, status, flags by severity, routing hint; filter by status; empty state.                                                                                                                                                                                                                                                                                                                                                 |
| `/claims/[id]`  | The review screen: the assistant (one combined question, one reply), the evidence (original file next to extracted fields, click-to-verify, decisions, findings with quoted policy clauses), submit bar.                                                                                                                                                                                                                                                                |
| `/approvals`    | Approvers only: submitted / approved / rejected, riskiest first; the same evidence read-only; approve, or reject (a reason is required).                                                                                                                                                                                                                                                                                                                                |
| `/impact`       | The impact meter from `GET /v1/stats`, with honest "estimated" labels.                                                                                                                                                                                                                                                                                                                                                                                                  |
| `/operations`   | AI ops (every persona): how the AI calls are going from `GET /v1/ops/llm`. Window 1 h / 24 h / 7 days, KPI tiles (calls, live vs recorded, errors, live cost, cache share), a route x model table with live and recorded columns kept apart (recorded = replays, "as recorded", never money spent), this session's recent failures (not recorded / budget used up / error) and a trace panel. Totals are system-wide; failures and traces are the caller's own sandbox. |

Principles the UI shows on purpose: it asks only what it must; it never submits without an explicit
confirmation; it shows **why** something was flagged (message, expected vs actual, the quoted
policy clause); it shows confidence per field; it is honest about which engine did what (System One
"Jev" for fast typed decisions, the LLM for reading and conversation) and what it cost.

## Architecture

```
src/
  app/                  routes (server components: metadata + Suspense around a client screen)
  components/
    ui/                 design system: Button, Chip, Card, Dialog (focus trap), Tabs, badges, feedback
    layout/             header, persona switcher, demo banner
    upload/ batch/ claims/ evidence/ approvals/ impact/ demo/   one folder per screen area
  lib/
    api/                client.ts (typed fetch client), problems.ts, sse.ts, types.ts, schema.d.ts (generated)
    batch/              batchReducer.ts (pure) + useBatchStream.ts (SSE hook)
    claims/             pure helpers: severities, finding values, extracted-field rows, boxes
    hooks/              React Query hooks keyed by persona; blob URLs
    persona/            persona store (localStorage), provider, ordering and defaults
    upload/             client-side limits, the sample pack
    toast.ts            tiny store behind the toasts (outside React, so one survives a navigation)
  test/                 MSW server, typed fixtures, render helpers
mock-api/               in-memory mock of the whole API (plain Node, no dependencies; for dev:mock)
  fixtures/             the ten receipts the mock recognises (by sha256)
e2e/                    Playwright specs, against the real demo stack
scripts/                dev-mock.mjs, e2e.mjs, make-icons.mjs
public/samples/         the 15 sample receipts, byte-identical to data/synth/demo/docs
```

**Data flow.** All data is fetched in the browser (the persona lives in `localStorage`, so there
is nothing to render on the server): server components only provide the static shell and a
`<Suspense>` boundary; each screen is a client component. Server state is TanStack Query, keyed
`["p", personaId, ...]`, so switching persona refetches and never shows another persona's data.
UI state is local state or a unit-tested reducer.

**Live progress (SSE).** `EventSource` cannot send the `X-Persona` header, so `useBatchStream`
reads the stream with `fetch` + `ReadableStream` (`src/lib/api/sse.ts` parses `id:`/`event:`/`data:`
frames and ignores `: keepalive`). On load it fetches the batch snapshot and `/history`, applies
those events, then streams from `Last-Event-ID`. It reconnects with back-off from the last index
seen, stops at `batch_done`/`batch_failed`, and aborts cleanly when the screen is hidden (Cache
Components keeps recent routes mounted but hidden). The reducer ignores any event index it has
already applied, so replays never double-count.

**Hydration.** The server HTML of every screen is rendered with no data, so the first client
render must see no data either. TanStack Query answers from its cache, which may already hold an
answer when React hydrates a later part of the page, and that printed "demo mode" or a list where
the server had a skeleton (React error #418). `useApiQuery`/`useApiQueries`
(`src/lib/hooks/hydration.ts`) report "pending" for the hydration render and the real result
right after; the browser-only stores (persona, banner, toasts) use `useSyncExternalStore` with a
server snapshot. `hydration.test.tsx` hydrates the page with a warm cache and fails on any mismatch,
and `e2e/no-errors.spec.ts` fails on any page error or console error on every screen.

**Demo sandboxes.** Every request carries `X-Sandbox: <id>` (`src/lib/sandbox/store.ts`): 32 random URL-safe characters created on first use and kept in localStorage (`claimpilot.sandbox`; in memory for the tab if storage is blocked). In demo mode the API confines everything to that sandbox, so two visitors acting as `DEMO-ASHA` never see or delete each other's batches, claims, approvals or stats; "Start over" clears only the caller's own sandbox (an approver clears all personas' data in it, never another visitor's). Outside demo mode the header is ignored. The e2e suite pins one sandbox (`E2E_SANDBOX` in `e2e/env.ts`); `e2e/isolation.spec.ts` proves two visitors are isolated.

**The sample pile.** `public/samples` holds the 15 files of `data/synth/demo/docs` (Asha Menon's
week: eleven honest receipts, a duplicate, an edited total, a note aimed at an AI reviewer and an
alcohol bill) **byte for byte**: the hosted demo replays recorded answers keyed by each file's
sha256, so a re-encoded photo would not replay. `samples.test.ts` compares every file's sha256
with the original. They are uploaded in file-name order (the second copy of a bill is the one that
gets flagged).

**Original files.** `GET /v1/documents/{id}/file` needs the persona header, so `<img src>` cannot
point at it: the file is fetched as a Blob, shown from an object URL (revoked on unmount), and a
PDF uses the browser's own viewer (a button to open it where the browser cannot embed one).
Click-to-verify draws `document.boxes[field]` (normalised 0–1) as an overlay. Recorded replays
carry no locations: then the fields are plain text (no buttons that do nothing), "Show on the
receipt" is not offered, and the viewer says why.

**Honest numbers.** When `GET /v1/meta` says `llm_mode: "replay"` every cost is labelled
"(recorded)": it is the cost that was recorded with the replayed answer, not money spent now. Claims
the policy engine calls `auto_approve` are shown as **low risk: can be approved in one click**:
nothing is approved without the approver's click.

**Submitting.** "Confirm & submit" only opens a confirmation dialog. Confirming sends
`{confirmed: true}` with an `Idempotency-Key` generated once per claim view and reused on every
retry.

**Accessibility.** Semantic landmarks and a skip link; labelled controls; visible focus
everywhere; every status is icon + text (never colour alone); `aria-live` regions for streaming
progress and results; a real focus-trapped dialog (Esc, inert background, focus returns); WAI-ARIA
tabs; text/background pairs at AA; `prefers-reduced-motion` respected; 44 px touch targets for
primary actions; works from 360 px up. Playwright runs axe on every screen at both widths and
requires **zero** violations.

**Performance.** Skeletons and fixed-height cards keep layout stable while events stream in;
images are lazy; the originals are only fetched when a receipt is opened; no UI kit.

## Testing

| Layer               | Where                                | How                                                                                                                          |
| ------------------- | ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| Unit and components | `src/**/*.test.ts(x)`                | Vitest + RTL + MSW. No real network (an unhandled request fails the test).                                                   |
| Mock API contract   | `mock-api/mock-api.contract.test.ts` | Boots the mock and validates **every** response against `openapi.json` with Ajv, so the mock cannot drift from the contract. |
| End to end          | `e2e/*.spec.ts`                      | Playwright against the real demo stack (the container image); `npm run e2e`.                                                 |
| Accessibility       | `e2e/accessibility.spec.ts`          | axe on every screen, desktop and phone.                                                                                      |

```bash
npm run lint && npm run typecheck && npm run format:check && npm run test:coverage   # the gates

# end to end: start the demo image first (see Quick start), then
npm run e2e                                  # E2E_BASE_URL defaults to http://localhost:7860
E2E_BASE_URL=http://localhost:7861 npm run e2e -- --project=mobile -g "approve"   # args pass through
SCREENSHOTS=1 npm run e2e -- screenshots     # PNGs of every screen at 360 and 1280 px in test-results/screens/
```

The suite never starts anything: it tests the stack at `E2E_BASE_URL` (the app at `/`, the API at
`/api`). Before the first test it checks that the stack is the public demo and replays recorded
answers (it refuses a live-LLM stack: that would spend money), and it starts every test from "Start
over" and leaves the stack empty at the end (so `scripts/smoke.py` can run next). It runs at
360 px (phone) and 1280 px (desktop). The specs, all against the 15-document pile (7 claims):

| Spec                   | What it proves                                                                                                                                     |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `upload-flow`          | samples → live progress for 15 receipts → 7 claims; reload-safe; a second trial starts clean                                                       |
| `claims`               | the list (what needs you first, why each flagged claim is flagged), filters, and each trap: duplicate, edited total, injection, alcohol (6.1), UPI |
| `questions-and-submit` | one reply answers the question, an explicit confirmation, a finance reference; the clean claim goes straight through                               |
| `approvals`            | employees are told, switch to the approver, reject needs a reason, approve, outcomes seen by the employee; each persona sees only their own        |
| `demo-mode`            | Asha by default, the banner, the persona switch + toast, Start over (both roles), a non-sample receipt explained                                   |
| `no-errors`            | no page error or console error (React #418) on any screen, for a first-time and a returning visitor                                                |
| `responsive`           | no sideways scrolling at 360/390/1280 px, the sticky bar never hides the last receipt, tap targets, the toast                                      |
| `accessibility`        | axe (WCAG 2.x A/AA + best practices): zero violations on every screen and state, at both widths                                                    |

In CI use the bundled browser (the demo job builds the image and starts it on :7860):

```bash
npx playwright install --with-deps chromium
CI=true npm run e2e          # only E2E_BASE_URL (default http://localhost:7860) is needed
```

## The mock API (`npm run dev:mock`: UI work without a backend)

`mock-api/server.mjs` is a faithful, in-memory implementation of the whole contract in plain Node:
personas, uploads (multipart, size and type rules, 413/422 problems), simulated batches streamed
over SSE with realistic timing, claims with questions and replies, idempotent submit, approvals,
stats, meta, the demo "Start over", and the original files (it serves the sample images). It
follows the real backend's semantics (policy clauses quoted from `policy.yaml`, trust findings,
question wording). `POST /__mock/reset` restores the seed state. It is **not** used by the E2E
suite any more (that tests the real stack); its own contract test still runs in `npm test`.

Handy hooks: a file name containing `blur`, `corrupt` or `unreadable` fails that document; one
containing `batch-fail` fails the whole batch. The mock recognises ten receipts of its own
(`mock-api/fixtures`, by sha256) and plays them as scripted scenarios (a hotel over its cap with the
quoted clause 4.1, a tampered invoice, a prompt-injection receipt, ...); any other file, including
the 15-file demo pile, is read as one plausible generated receipt, so `dev:mock` is for building
screens, not a replica of the hosted demo. See the header comment of `mock-api/server.mjs` for every
endpoint, option and scenario.

## Docker

`Dockerfile` builds the standalone Next.js server; pass `--build-arg NEXT_PUBLIC_API_BASE_URL=...`
(for the hosted demo: `/api`, with the reverse proxy forwarding `/api/*` to the API).
