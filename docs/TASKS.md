# Tasks

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[-]` skipped on purpose · `[!]` blocked · ★ must · ☆ stretch (cut first)
Rule: milestones are listed up front. Low-level subtasks are added only when a milestone starts.

**Current focus (12 Oct, midday):** everything is built and verified; **hosting is skipped on purpose** (ADR-037: Hugging Face Docker Spaces became paid, the free alternatives were poor, the rules ask for a link only "if hosted"); the demo image runs with one Docker command (README). **Video:** the captured video (hosted-demo flow, full stack with live Claude + Jev, 3 slides) and the user's Claude Desktop MCP clip are merged by the user (`Video Project.mp4`, 5:17); Claude replaced the closing slides that mentioned a hosted demo (`Video Project - final.mp4`, same folder as the original; originals untouched). **Left, in order:** (1) the user checks `Video Project - final.mp4` and uploads it to YouTube (Unlisted or Public), (2) Claude puts the YouTube link in the README table and the TDD header and rebuilds the PDF, (3) the user pushes everything and checks the repo and the YouTube link in a private window, (4) Claude tags `v1.1.0` (publishes the demo image to GHCR), (5) submit.

### Resume notes (read first after a restart)
- **Windows restarts abruptly.** Everything is on disk; agents' transcripts survive. Resume a stopped agent with SendMessage to its id. Commit coherent chunks often.
- **Committed so far** (the user pushes, do not push without being asked): everything is committed; the user's last push was before the final docs, A5 results, PDF fix and capture-tool commits (5 local commits). `docs/TECHNICAL_DESIGN.pdf` is current (rebuild with `cd docs/tools && npm install && npm run pdf` if the TDD changes).
- **Agents** (all finished): A(MCP), B(trust), C(policy+claims), G(demo pile), F(frontend), H(ClaimPilot MCP server), reviewer. Resume one with SendMessage if its transcript is needed.
- **C2PA:** the pure-Python detector is the default; the native `c2pa-python` lib is opt-in (`C2PA_NATIVE=1`, `c2pa` extra) because it raises a native access violation on Windows. Still run tests with `-p no:faulthandler`.
- **Shell gotcha:** the tool layer collapses `\` in heredocs; use the Edit tool for strings containing backslashes.
**Deadline:** submit by 12 Oct 2026. Aim for the morning of the 12th.

## Submission checklist (missing ANY item means elimination; run the `submission-check` skill)
- [x] Fully functional build by 12 Oct: built and verified (CI green, smoke OK, demo image tested in CI and from a clean clone)
- [~] `docs/TECHNICAL_DESIGN.md`: overview + **track**, architecture/flow diagrams, setup & deployment, code explanations & assumptions (PDF rebuilt; **left:** look at the diagrams in the rendered PDF, final test counts)
- [~] YouTube demo link (public or unlisted), playable logged-out: video made (`Video Project - final.mp4`); **user: upload, then check in a private window**
- [~] Code on **public** personal GitHub with docs + video link; no access requests needed (pushed, CI visible; **user:** confirm the repo opens logged-out; Claude adds the video link)
- [x] Comprehensive unit tests: CI coverage gates enforced and green (api 98.7%, web 98.1%, three MCP servers ≥ 90%)
- [-] Hosted link: **skipped on purpose** (ADR-037; the rules say "if hosted"). README: one-command demo image; the video shows the parts the image cannot

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
- [x] First CI run failed (web typecheck, gitleaks-action wrapper); both fixed, CI green since run #3
- [x] Second email (8 Oct) folded in: ADR-010, TDD skeleton, `submission-check` skill, coverage gates in CI (+ entrypoint and page tests)
- [x] `evals.yml` (manual, paid) written in M1; `cd.yml` written in M4 (first real run happens when `v1.1.0` is tagged)

## M1 Data & extraction (8 Oct) [x]
- [x] M1.0 Model registry: **Claude Haiku 5.5** added (Models API + pricing page verified), Haiku 4.5 kept as a baseline, Sonnet cache-read price fixed, long-context tier + batch pricing (ADR-011)
- [x] M1.1 Domain contract: `ExtractedReceipt` / `ReceiptTruth` schema + GSTIN validate/generate (`claimpilot.domain`), 41 tests
- [x] M1.2 Synthetic generator `data/synth/`: scenarios → ground truth → render (≥8 doc types incl. Hindi/handwritten/UPI) → degrade → adversarial set → manifest (dev 20 / test 80) + 10 committed fixtures. 100 docs, 10 types, 5 adversarial kinds; 80 tests; GST 2.0 rates (ADR-015)
- [x] M1.3 Capability shim `llm/client.py` (per-model request params), `LLMClient` with live / record-replay / fake modes, cost ledger (`llm_calls` + Alembic), snapshot tests. Reviewed (request-changes → all fixed, ADR-014). Live smoke passed on all 4 models ($0.003)
- [x] M1.4 Extraction: versioned prompt + `messages.parse(ExtractedReceipt)`, image downscale, Redis content-hash cache, injection-safe framing; Haiku→Sonnet escalation on unsure critical fields. Worker job moves to M2 (needs Storage + claims)
- [x] M1.5 Eval harness: metrics, Pareto, dataset loader, runner with cost estimate + `--max-usd` (sync, ADR-013), `evals.yml` (manual, paid)
- [x] M1.6 First bake-off (dev 20, $0.093): Haiku 5.5 alone = 100% critical / 95% field / 100% injection recall at $0.42 per 1k receipts; cascade 10× cost for +1.9 pp → Haiku chosen (ADR-018). Wire schema fix first (ADR-017)
- [x] M1.7 Click-to-verify boxes: done with a vision-model localisation call (`extraction/locate.py`, route `locate`), not OCR; 14 of 15 demo documents have boxes
- [x] M1.8 TDD §3–4 + §7 numbers updated (final counts refreshed again at the end)

## M2 Intelligence (8–9 Oct) [x]
Contract frozen first (ADR-020), then four parallel streams. **Owners are workstreams, not people.**
- [x] M2.0 Contract: `domain.claims`, extended `Finding`, golden dataset + roster, `evals.golden`; Jev API verified, TLS via OS trust store (ADR-019)
- [x] Trust: GST/arithmetic/GSTIN checks with evidence-carrying `Finding`s (16 tests)
- [x] **A. MCP:** `services/mcp-finance` + `services/mcp-corp` (MCP streamable HTTP, seed data, Dockerfiles, compose) and the API-side client bridge `claimpilot.mcp` (typed clients, role-scoped Claude tool bridge, port adapters). 86 + 130 server tests (100% cov each) + 168 API-side
- [x] **B. Trust:** pHash + field-fingerprint duplicates, EXIF/metadata forensics, C2PA / AI-generated signals, prompt-injection heuristics, `TrustReport` score *(agent)*
- [x] **C. Policy + claims:** `policy/` (clause-cited rules in `config/policy.yaml`, calibrated on the golden set: 0/80 false positives, 4/4 over-policy caught), `claims/` grouping into trip/period/event/allowance (F1 1.000), state machine, one-combined-question generation, routing. 608 tests, 99% cov
- [x] **D. Decisions:** `DecisionEngine` protocol, `JevEngine` (retries, truststore), `LLMEngine` (Haiku 5.5, flat schema), `CascadeEngine`; Jev-vs-LLM benchmark incl. held-out seed (ADR-021). 40 tests, 99% cov. Jev = LLM accuracy at 3× speed and cost
- [x] **E. Pipeline:** Storage (atomic local volume), tables + Alembic 0002 with a drift test, typed events + SSE bus (resume, keep-alive), repository, claim actions (answer / reply / submit with explicit confirmation + idempotency / approver decision with required reason), `DbDuplicateIndex`, `process_batch` (3 phases, per-document failure isolation, idempotent), Arq worker + wiring, `/v1` API (20 endpoints) + OpenAPI contract, CORS. 1,353 API tests at 95% coverage (all modules currently on disk)
- [x] E2. Stack verification in Docker (backend): Postgres, Redis, worker, both MCP servers; `scripts/smoke.py` drives upload → SSE → claims → one question → submit via MCP → approver. Found and fixed: flight tickets blocked by skipped fee rows and misread-GSTIN false alarms (ADR-028), duplicated question on one bill (ADR-027). The web container still to verify once F is done
- [x] E3. Demo data: the coherent pile (`data/synth/demo`, 15 receipts), 31 recordings made in Docker (ADR-029; prompts extract_v3 and decide_v2), replay verified in the one-container image. Remaining: the web app's sample pack switches to the pile (F)
- [x] E4. Wiring tests (`wiring.py`, worker lifecycle, lifespan) with patched Redis/Arq/MCP
- [x] E5. Answers re-run policy: `pipeline/finalize.py` (`refinalize` keeps the pipeline's category questions and every answer, recomputes findings and the route), so a client-dinner headcount answer can flip `auto_approve` / `finance_review`. 11 unit + 3 API tests
- [x] Click-to-verify boxes filled (see M1.7)
## M3 Experience (8–10 Oct) [x]
- [x] **F. Web app** (done, committed e47e0ae): persona switcher, upload + camera, live SSE progress, claim review with assistant chat, click-to-verify, submit dialog, approvals, impact meter; mock API + Playwright E2E + axe
★ PWA drop zone + camera · streaming progress (SSE) · claim cards · click-to-verify · chat agent with question flow + approval gate · Playwright E2E

## M4 Standouts & ship (11 Oct) [~] (code done; hosting and video are the user's)
- [x] Hosting decided with the user: **Hugging Face Space, one container** (ADR-030). Free, no card, no model spend (answers replayed)
- [x] Backend for the demo: `RUNTIME=embedded` (in-process batches, in-memory events, SQLite WAL), `DEMO_MODE` (`POST /v1/demo/reset`, plain message for unrecorded receipts), `DUPLICATE_SCOPE=employee`, numbered replies parsed without a model. 1,625 API tests at 98.65%
- [x] `deploy/hf-space/`: Dockerfile (clones GitHub at build, or `--build-arg SOURCE=fetch-local`), Caddyfile, supervisor `start.py`, Space README, DEPLOY.md. Local build and run in progress; then add a CI job that builds it and runs `scripts/smoke.py` in replay mode (catches recordings that stop matching on another machine)
- [x] `cd.yml` written (GHCR images on a `v*` tag; optional Space rebuild with repo variable `HF_SPACE` + secret `HF_TOKEN`); exercised when `v1.1.0` is tagged
- [x] Web (F): relative API base `/api`, demo banner, Start over, default persona DEMO-ASHA; Playwright e2e job in `ci.yml`
- [x] Demo pile (G): `data/synth/demo` (14-15 receipts for DEMO-ASHA incl. 4 adversarial), offline expectation test; switch the web sample pack to it; record cassettes in Docker with `infra/compose.record.yml` (prompt v2); commit `services/api/replay`
- [x] Final bake-off on the 80-receipt test split (done, $0.23 incl. second opinion) to replace the dev numbers in the TDD; Jev-vs-LLM slide
- [x] ☆ ClaimPilot MCP server (ADR-032) · A2A not built (decided: future work) · approver polish done (only if time remains; never at the cost of the checklist)

## M5 Submit (12 Oct AM) [~]
★ polish · 3–4 min demo video on YouTube (public or unlisted) · finish the TDD + PDF · README with metrics + links · `submission-check` · submit

**Every milestone:** append its TDD sections · keep coverage gates green · reviewer agent pass before commit

## Post-review work (9-11 Oct) — status
Legend as above. **Push points** (P0...) are where the user pushes; each leaves `main` green and shippable.
- [x] **Step 0 CI fixes** (P0 pushed, run #3 on `3bc2977`: all 9 jobs green incl. demo 17 min and docker; tag `baseline-2026-10-09` = rollback point). Known, harmless: 2 annotations from a pytest `PytestUnhandledThreadExceptionWarning` (an earlier test leaves an aiosqlite connection undisposed; surfaces in `test_runtime_checks.py`); Node 20 action deprecation warnings
- [x] **Step 1 B1 sandboxes** (P1+P2, one push when both halves are green): backend [x] committed (`b80d896`: ORM + Alembic 0003, repo/API/pipeline/dup index/locks/reset/stats per sandbox, ledger sandbox+batch_id); `smoke.py` fresh sandbox [x]; backend tests (agent T: isolation, hash-invariance, repository, migration) [x]; web sandbox header, StartOver copy, mock CORS, e2e `isolation.spec.ts`, record-demo script (agent F) [x] (`fdc03dd`; the video itself is not re-recorded)
- [x] **Step 2 A1, A2, B2, A4** (P3): backend [x] (`4929ec0`: meta has llm_record, daily_llm_budget_usd, max_batch_files, max_upload_mb; hybrid profile keeps paced replay; start.py copies recordings to /data when LLM_RECORD=1; friendly "budget used up" message), MCP reads limits from meta [x] (`a99a1bb`); web banner/drop-zone/limits from meta + mock meta (agent F) [x]; DEPLOY.md profiles [x]
- [x] **Step 3 D1-D2** (P7): backend [x] (`228c512`: Alembic 0004, request-id middleware, JSON logs, trace/batch/document/claim ids on every ledger row); tests (agent T2) [x] (found and fixed: 500 responses lacked X-Request-ID)
- [x] **Step 4 D3** (P8): `GET /v1/ops/llm` [x] (`436235c`); tests (agent T3) [x]; `/operations` page + mock route (agent F) [x]
- [x] **Step 5 docs G1-G4**: README, DEMO_SCRIPT (full-stack segments), demo-pile README, TDD wording and sections, ADR-033..036, DEPLOY.md
- [-] **Step 6** dormant `POST /v1/auth/token`: **skipped by the user's decision (9 Oct)**; the production auth design is ADR-033
- [~] **Step 7 A5 + G5**: hybrid-mode test run [x] (ADR-036), PDF render check [x] (fixed a diagram that left a blank page), counts [x], `submission-check` automatable items [x] (the repo-public check needs the user: GitHub is unreachable from the dev machine), clean-clone build + smoke [x], **captured demo video [x]** (decided with the user: captured video + the user's own clips joined in Clipchamp; the video is never recorded or re-recorded by Claude without agreement), tag `v1.1.0` [ ] (after the links are in).
- Deferred until the user nods: E1/E2 (Jev recording and ledger), F1 (finance dashboard). Not doing: C1 login, A3, OpenTelemetry/Langfuse, MCP token mode.
- **Cut order if behind:** step 6, then D3 down to tables, then D3, then A5 to a manual check. Never cut B1, step 2 or the docs.

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
- **2026-10-08 (night), submission-check, automatable items:** gitleaks over the full history: no leaks (one allowlisted fake persona sentinel); no brief files in history; hosted image built from a clean clone of HEAD and `scripts/smoke.py` printed SMOKE OK; API 1,742 tests 98.66%; web 539 tests 97.98% lines, lint/types/format/build clean; Playwright 100 passed + 9 phone-only skipped on desktop, 1 console-error race once under load; MCP servers 86 + 130 + 350 tests, synth 111; demo video recorded (3 min 5 s, `docs/demo/claimpilot-demo.webm`, git-ignored); TDD PDF rebuilt. **Not checkable by me (user):** repo public and pushed, CI green on GitHub, Space URL logged-out, YouTube link logged-out, links in README/TDD header.
- 2026-10-09: first GitHub CI run (web Prettier, synth PDF size floor on Linux) fixed; per-visitor demo sandboxes (ADR-034), meta/hybrid hosting profile (ADR-036), request ids + JSON logs + trace ids on every LLM call (ADR-035), `/v1/ops/llm` + AI-ops page, MCP server reads limits from meta; API 2,073 tests at 98.7%, web 581 at 98.1%; 20 commits pushed, run #4 green except synth (generated README edited by hand, fixed in the template)
- 2026-10-11: step 7 verification: hybrid profile tested live (47 recorded calls at $0, unseen receipt 3 live calls $0.0013 then replayed, tiny cap gives the plain message), TDD PDF rendered page by page (diagram 2.6 overflow fixed), final counts, secret scan, clean-clone build + smoke; **captured demo video recorded** (3 min 59 s; Jev needs the API on the host behind the corporate TLS proxy); production-auth design recorded instead of a login (ADR-033)
