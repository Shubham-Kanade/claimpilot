# Tasks

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked · ★ must · ☆ stretch (cut first)
Rule: milestones are listed up front. Low-level subtasks are added only when a milestone starts.

**Current focus:** M2 closed, M3 and M4 under way (2026-10-08 evening). A (MCP), B (trust) and C (policy + claims) are done and committed; the backend stack was verified end to end in Docker (upload, SSE, claims, one combined question, submit through MCP, approver decision; $0.01 spent). Still running: **F** (web app, `apps/web`) with its mock-API helper, and **G** (the coherent demo receipt pile for DEMO-ASHA, `data/synth/demo`). Next: commit the arithmetic/prompt-v2 fixes, build the demo mode (reset, duplicate scope, friendly replay miss), switch the sample pack to G's pile, record the cassettes in Docker with `infra/compose.record.yml`, then hosting (ask the user which account they have), CI e2e job, README + TDD numbers, demo script, submission check.

### Resume notes (read first after a restart)
- **Windows restarts abruptly.** Everything is on disk; agents' transcripts survive. Resume a stopped agent with SendMessage to its id. Commit coherent chunks often.
- **Committed so far** (local; the user pushes, do not push without being asked): M0, M1, decisions (ADR-021), the API + persistence, reply/stats/boxes/CORS + OpenAPI, pipeline/worker/wiring, MCP (`3e1b0de`), policy + claims (`96e9c72`), trust (`91e22b1`), re-finalize + wiring tests (`a9c2503`). **Not committed yet:** `apps/web/*` (F), `data/synth/demo` (G), the arithmetic/prompt-v2 fixes (`trust/gst.py`, `assess.py`, `extract_v2.md`), `infra/compose.record.yml`, `scripts/smoke.py`, docs. HEAD is runnable.
- **Agent ids:** A(MCP)=acf706e619b9a11a4 (done), B(trust)=a1ce6f46beb89dfb6 (done), C(policy+claims)=a0703e0f866b2dcd7 (done), F(frontend)=ab6ec34cd73e0097d, mock-API helper=a12ff279360023eef, G(demo pile)=a591734c0fe05b652.
- **C2PA:** the pure-Python detector is the default; the native `c2pa-python` lib is opt-in (`C2PA_NATIVE=1`, `c2pa` extra) because it raises a native access violation on Windows. Still run tests with `-p no:faulthandler`.
- **Shell gotcha:** the tool layer collapses `\` in heredocs; use the Edit tool for strings containing backslashes.
**Deadline:** submit by 12 Oct 2026. Aim for the morning of the 12th.

## Submission checklist (missing ANY item means elimination; run the `submission-check` skill)
- [ ] Fully functional build by 12 Oct
- [~] `docs/TECHNICAL_DESIGN.md`: overview + **track**, architecture/flow diagrams, setup & deployment, code explanations & assumptions (PDF export at M5)
- [ ] YouTube demo link (public or unlisted), playable logged-out
- [ ] Code on **public** personal GitHub with docs + video link; no access requests needed
- [~] Comprehensive unit tests: CI coverage gates (api ≥ 85%, web ≥ 80% lines)
- [ ] Hosted link, working logged-out (no-login demo personas)

## M0 Setup (7–8 Oct) [x]
- [x] Repo init (`claimpilot/` subfolder, ADR-008), layout, plan copied to `docs/PLAN.md`
- [x] Context docs: CLAUDE.md, TASKS.md, DECISIONS.md (ADR-001…009), ARCHITECTURE.md, README
- [x] Claude Code config: 5 rules, 8 skills, 6 agents, hooks (format-on-edit, commit guard). Both hooks tested
- [x] API skeleton: uv + Python 3.13, FastAPI `/healthz` `/readyz` `/v1/meta`, Arq worker, **model registry** (haiku/sonnet/opus + route overrides). 13 tests; ruff and pyright clean
- [x] Web skeleton: Next.js 16.4 (standalone output), Vitest + RTL + MSW, Playwright installed, prettier, typed API client generated from OpenAPI. 8 tests; lint and tsc clean
- [x] Dockerfiles (api/worker, web), `infra/compose.yml` (validated with `docker compose config`)
- [x] `ci.yml` (api, web, OpenAPI and TS drift, Docker build + boot probe, gitleaks), pre-commit, `.env.example`
- [x] Full stack boots: api/worker/postgres/redis healthy, readyz green over the compose network, web 200 (after switching to self-hosted fonts, ADR-012)
- [x] **User action:** Jev + Anthropic API keys in `.env`; public repo pushed (github.com/Shubham-Kanade/claimpilot)
- [~] First CI run failed: web typecheck (`LayoutProps` needs `next typegen`) and the gitleaks-action wrapper (a local full-history scan finds no leaks). Both fixed; verify on next push
- [x] Second email (8 Oct) folded in: ADR-010, TDD skeleton, `submission-check` skill, coverage gates in CI (+ entrypoint and page tests)
- [ ] Deferred: `evals.yml` (lands with the eval harness in M1) and `cd.yml` (M4, with hosting)

## M1 Data & extraction (8 Oct) [x]
- [x] M1.0 Model registry: **Claude Haiku 5.5** added (Models API + pricing page verified), Haiku 4.5 kept as a baseline, Sonnet cache-read price fixed, long-context tier + batch pricing (ADR-011)
- [x] M1.1 Domain contract: `ExtractedReceipt` / `ReceiptTruth` schema + GSTIN validate/generate (`claimpilot.domain`), 41 tests
- [x] M1.2 Synthetic generator `data/synth/`: scenarios → ground truth → render (≥8 doc types incl. Hindi/handwritten/UPI) → degrade → adversarial set → manifest (dev 20 / test 80) + 10 committed fixtures. 100 docs, 10 types, 5 adversarial kinds; 80 tests; GST 2.0 rates (ADR-015)
- [x] M1.3 Capability shim `llm/client.py` (per-model request params), `LLMClient` with live / record-replay / fake modes, cost ledger (`llm_calls` + Alembic), snapshot tests. Reviewed (request-changes → all fixed, ADR-014). Live smoke passed on all 4 models ($0.003)
- [x] M1.4 Extraction: versioned prompt + `messages.parse(ExtractedReceipt)`, image downscale, Redis content-hash cache, injection-safe framing; Haiku→Sonnet escalation on unsure critical fields. Worker job moves to M2 (needs Storage + claims)
- [x] M1.5 Eval harness: metrics, Pareto, dataset loader, runner with cost estimate + `--max-usd` (sync, ADR-013), `evals.yml` (manual, paid)
- [x] M1.6 First bake-off (dev 20, $0.093): Haiku 5.5 alone = 100% critical / 95% field / 100% injection recall at $0.42 per 1k receipts; cascade 10× cost for +1.9 pp → Haiku chosen (ADR-018). Wire schema fix first (ADR-017)
- [ ] M1.7 OCR word boxes for click-to-verify (may slide to M3)
- [ ] M1.8 TDD §3–4 + §7 numbers updated

## M2 Intelligence (8–9 Oct) [~]
Contract frozen first (ADR-020), then four parallel streams. **Owners are workstreams, not people.**
- [x] M2.0 Contract: `domain.claims`, extended `Finding`, golden dataset + roster, `evals.golden`; Jev API verified, TLS via OS trust store (ADR-019)
- [x] Trust: GST/arithmetic/GSTIN checks with evidence-carrying `Finding`s (16 tests)
- [x] **A. MCP:** `services/mcp-finance` + `services/mcp-corp` (MCP streamable HTTP, seed data, Dockerfiles, compose) and the API-side client bridge `claimpilot.mcp` (typed clients, role-scoped Claude tool bridge, port adapters). 86 + 130 server tests (100% cov each) + 168 API-side
- [~] **B. Trust:** pHash + field-fingerprint duplicates, EXIF/metadata forensics, C2PA / AI-generated signals, prompt-injection heuristics, `TrustReport` score *(agent)*
- [x] **C. Policy + claims:** `policy/` (clause-cited rules in `config/policy.yaml`, calibrated on the golden set: 0/80 false positives, 4/4 over-policy caught), `claims/` grouping into trip/period/event/allowance (F1 1.000), state machine, one-combined-question generation, routing. 608 tests, 99% cov
- [x] **D. Decisions:** `DecisionEngine` protocol, `JevEngine` (retries, truststore), `LLMEngine` (Haiku 5.5, flat schema), `CascadeEngine`; Jev-vs-LLM benchmark incl. held-out seed (ADR-021). 40 tests, 99% cov. Jev = LLM accuracy at 3× speed and cost
- [x] **E. Pipeline:** Storage (atomic local volume), tables + Alembic 0002 with a drift test, typed events + SSE bus (resume, keep-alive), repository, claim actions (answer / reply / submit with explicit confirmation + idempotency / approver decision with required reason), `DbDuplicateIndex`, `process_batch` (3 phases, per-document failure isolation, idempotent), Arq worker + wiring, `/v1` API (20 endpoints) + OpenAPI contract, CORS. 1,353 API tests at 95% coverage (all modules currently on disk)
- [x] E2. Stack verification in Docker (backend): Postgres, Redis, worker, both MCP servers; `scripts/smoke.py` drives upload → SSE → claims → one question → submit via MCP → approver. Found and fixed: flight tickets blocked by skipped fee rows and misread-GSTIN false alarms (ADR-028), duplicated question on one bill (ADR-027). The web container still to verify once F is done
- [ ] E3. Demo data: G builds the coherent pile (`data/synth/demo`); record cassettes with `infra/compose.record.yml` + `scripts/smoke.py` (ADR-029; prompt v2, so the earlier recordings were deleted); sample pack in the web app switches to the pile
- [x] E4. Wiring tests (`wiring.py`, worker lifecycle, lifespan) with patched Redis/Arq/MCP
- [x] E5. Answers re-run policy: `pipeline/finalize.py` (`refinalize` keeps the pipeline's category questions and every answer, recomputes findings and the route), so a client-dinner headcount answer can flip `auto_approve` / `finance_review`. 11 unit + 3 API tests
- [ ] M1.7 Click-to-verify boxes: `ProcessedDocument.boxes` contract exists; filling it (vision-model localisation, evaluated on dev) is optional polish
## M3 Experience (8–10 Oct) [~]
- [~] **F. Web app** *(agent, ~2 h)*: persona switcher, upload + camera, live SSE progress, claim review with assistant chat, click-to-verify, submit dialog, approvals, impact meter; mock API + Playwright E2E + axe
★ PWA drop zone + camera · streaming progress (SSE) · claim cards · click-to-verify · chat agent with question flow + approval gate · Playwright E2E

## M4 Standouts & ship (11 Oct) [ ]
★ Jev-vs-LLM benchmark · **hosting + `cd.yml` + no-login demo personas** (+ S3 storage if needed) · final bake-off (100 receipts), routes locked · live eval run
☆ approver view · impact meter · ClaimPilot MCP server · A2A hand-off

## M5 Submit (12 Oct AM) [ ]
★ polish · 3–4 min demo video on YouTube (public or unlisted) · finish the TDD + PDF · README with metrics + links · `submission-check` · submit

**Every milestone:** append its TDD sections · keep coverage gates green · reviewer agent pass before commit

## Done log
- 2026-10-07: chose the Expense case and approved the plan (ADR-001…008)
- 2026-10-07: M0 scaffold. API + web skeletons green (13 + 8 tests), model registry, CI, compose, Claude Code config. MinIO swapped for a local volume (ADR-009)
- 2026-10-08: second email's submission rules adopted (ADR-010): TDD skeleton, submission-check skill, coverage gates
- 2026-10-08: Haiku 5.5 in registry (ADR-011); CI fixes; self-hosted fonts (ADR-012); domain contract + GSTIN (41 API tests, 96.6% cov)
- 2026-10-08: M1.3/M1.4 done + eval runner; reviewer blocker (REPO_ROOT crash in Docker) fixed; 216 API tests, 98.5% cov; stack healthy with migrations
- 2026-10-08: M1.2 synthetic dataset committed (reviewed + GST 2.0 fix); trust GST checks; synth CI job; evals.yml
- 2026-10-08: M1 done. Bake-off picks Haiku 5.5 ($0.42/1k receipts, 100% critical-field accuracy on dev); PDF rasterisation (ADR-016), wire schema (ADR-017), routes (ADR-018)
- 2026-10-08: M2.D done: System One decisions + benchmark (held-out 85% category accuracy, Jev 3× faster/cheaper than LLM at equal accuracy); wiring still to do: calendar context
- 2026-10-08: pipeline end-to-end on fixtures with fake models (9 tests): tampered + injected docs flagged, duplicates, retries; API contract exported; 4 agents in flight
