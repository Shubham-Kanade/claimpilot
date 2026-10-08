# Tasks

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked · ★ must · ☆ stretch (cut first)
Rule: milestones are listed up front. Low-level subtasks are added only when a milestone starts.

**Current focus:** M1. M1.2 (synthetic generator) and M1.3 (capability shim + ledger) are running in parallel; next is M1.4 (extraction).
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

## M1 Data & extraction (8 Oct) [~]
- [x] M1.0 Model registry: **Claude Haiku 5.5** added (Models API + pricing page verified), Haiku 4.5 kept as a baseline, Sonnet cache-read price fixed, long-context tier + batch pricing (ADR-011)
- [x] M1.1 Domain contract: `ExtractedReceipt` / `ReceiptTruth` schema + GSTIN validate/generate (`claimpilot.domain`), 41 tests
- [~] M1.2 Synthetic generator `data/synth/`: scenarios → ground truth → render (≥8 doc types incl. Hindi/handwritten/UPI) → degrade → adversarial set → manifest (dev 20 / test rest) + committed fixtures *(agent)*
- [~] M1.3 Capability shim `llm/client.py` (per-model request params), `LLMClient` with live / record-replay / fake modes, cost ledger (`llm_calls` + Alembic), snapshot tests *(agent)*
- [ ] M1.4 Extraction: versioned prompt + `messages.parse(ExtractedReceipt)`, image downscale, Redis content-hash cache, worker job, injection-safe framing
- [ ] M1.5 Eval harness `evals/` (metrics, cost estimate + `--max-usd`, Batch API), `evals.yml`, baseline on dev split
- [ ] M1.6 First bake-off (dev 20): haiku · haiku45 · sonnet-low · opus-low (+ cascade), Pareto chart → routes ADR
- [ ] M1.7 OCR word boxes for click-to-verify (may slide to M3)
- [ ] M1.8 TDD §3–4 + §7 numbers updated

## M2 Intelligence (9 Oct) [ ]
★ DecisionEngine (Jev + LLM adapters) · taxonomy + policy-as-code with citations · trust score (pHash, math, EXIF, C2PA, prompt injection) · grouping + claim state machine · `mcp-finance` + `mcp-corp` · Storage interface (local volume)

## M3 Experience (10 Oct) [ ]
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
