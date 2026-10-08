# Tasks

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked · ★ must · ☆ stretch (cut first)
Rule: milestones are listed up front. Low-level subtasks are added only when a milestone starts.

**Current focus:** M1. Next task: the synthetic receipt generator (`synthetic-receipts` skill), then the capability shim.
**Deadline:** submit by 12 Oct 2026. Aim for the morning of the 12th.

## Submission checklist (missing ANY item means elimination; run the `submission-check` skill)
- [ ] Fully functional build by 12 Oct
- [~] `docs/TECHNICAL_DESIGN.md`: overview + **track**, architecture/flow diagrams, setup & deployment, code explanations & assumptions (PDF export at M5)
- [ ] YouTube demo link (public or unlisted), playable logged-out
- [ ] Code on **public** personal GitHub with docs + video link; no access requests needed
- [~] Comprehensive unit tests: CI coverage gates (api ≥ 85%, web ≥ 80% lines)
- [ ] Hosted link, working logged-out (no-login demo personas)

## M0 Setup (7 Oct) [~]
- [x] Repo init (`claimpilot/` subfolder, ADR-008), layout, plan copied to `docs/PLAN.md`
- [x] Context docs: CLAUDE.md, TASKS.md, DECISIONS.md (ADR-001…009), ARCHITECTURE.md, README
- [x] Claude Code config: 5 rules, 8 skills, 6 agents, hooks (format-on-edit, commit guard). Both hooks tested
- [x] API skeleton: uv + Python 3.13, FastAPI `/healthz` `/readyz` `/v1/meta`, Arq worker, **model registry** (haiku/sonnet/opus + route overrides). 13 tests; ruff and pyright clean
- [x] Web skeleton: Next.js 16.4 (standalone output), Vitest + RTL + MSW, Playwright installed, prettier, typed API client generated from OpenAPI. 8 tests; lint and tsc clean
- [x] Dockerfiles (api/worker, web), `infra/compose.yml` (validated with `docker compose config`)
- [x] `ci.yml` (api, web, OpenAPI and TS drift, Docker build + boot probe, gitleaks), pre-commit, `.env.example`
- [!] Boot the full stack locally — Docker Desktop is not running (**User action:** start it and confirm the licence is OK on a corporate laptop, then `docker compose -f infra/compose.yml up --build`)
- [ ] **User action:** apply for the Jev early-access waitlist (typesafe.ai)
- [ ] **User action:** create the personal GitHub repo and set a repo-local git email (global is the corporate address), then push
- [ ] **User action:** create a Claude Console workspace + API key with a monthly spend limit (e.g. $25–30) and put the key in `.env`
- [x] Second email (8 Oct) folded in: ADR-010, TDD skeleton, `submission-check` skill, coverage gates in CI (+ entrypoint and page tests)
- [ ] Deferred: `evals.yml` (lands with the eval harness in M1) and `cd.yml` (M4, with hosting)

## M1 Data & extraction (7–8 Oct) [ ]
★ Synthetic receipt generator (ground truth first, then render) · capability shim + cost ledger · extraction pipeline (structured outputs) · OCR word boxes · Redis content-hash cache · eval harness + `evals.yml` with baseline · first bake-off (20 receipts)

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
