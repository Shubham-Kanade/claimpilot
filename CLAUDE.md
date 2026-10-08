# ClaimPilot: AI Expense & Reimbursement Agent

ClaimPilot is the entry for AI Innovation Lab Season 2 (Jio Platforms Academy). **Hard deadline: 12 Oct 2026.**
An employee drops a pile of receipts (photos, PDFs, UPI screenshots). ClaimPilot extracts the fields, categorises each item, checks policy and trust, groups items into claims, and asks only the questions it needs. It then submits to a *mocked* finance system.

- Full plan and rationale: `docs/PLAN.md`. Read it on demand; it is not loaded automatically.
- Settled decisions: `docs/DECISIONS.md`. **Do not reopen a decision recorded there unless the user asks.**
- System overview: `docs/ARCHITECTURE.md`
- **Technical design doc** (a judged deliverable; update it every milestone): `docs/TECHNICAL_DESIGN.md`
- **Submission rules:** missing any item means elimination. See the checklist at the top of TASKS.md and the `submission-check` skill.

## Current tasks
@docs/TASKS.md

## Working agreement
- **Track work in `docs/TASKS.md`.** Mark a task `[~]` when you start it and `[x]` when it's done. When a milestone starts, add its low-level subtasks. Append a line to the dated done-log. The `task-tracking` skill describes the format.
- **Record decisions in `docs/DECISIONS.md`**: one short ADR per decision.
- **Definition of done:**
  - lint and type checks pass
  - tests for the change pass
  - evals have not regressed (only when LLM or prompt code changed)
  - TASKS.md is updated
- Prefer small, reviewable commits. Commit only when the user asks. Conventional-commit style (`feat:`, `fix:`, `chore:`, `test:`, `docs:`).

## Architecture in one breath
- **Web:** Next.js PWA (`apps/web`). It talks REST and SSE to the **api**, a FastAPI modular monolith in `services/api`.
- **Worker:** an Arq worker runs the async receipt pipeline. Same package as the api, different entrypoint.
- **Storage:** Postgres (state, audit log, LLM cost ledger), Redis (queue, pub/sub, content-hash cache), a local upload volume (S3 later, ADR-009).
- **MCP servers:** mocked enterprise systems (`services/mcp-finance`, `services/mcp-corp`).
- **System Two:** the Claude API, for perception and conversation.
- **System One:** Jev (TypeSafe AI), for fast typed decisions, behind a `DecisionEngine` interface with Jev and LLM adapters.

## Hard rules (details in `.claude/rules/`)
- **Synthetic or public data only.** Never real bills, real employee or customer data, or Jio/Reliance documents. The challenge brief files live *outside* this repo and must never be copied in.
- **No secrets in code.** Use `.env` (gitignored) and GitHub Actions secrets.
- **Runtime LLM spend is the user's personal money:**
  - Model IDs come only from `services/api/config/models.yaml` through the capability shim.
  - CI and dev default to `LLM_MODE=replay`.
  - Live eval runs need a `--max-usd` budget.
- Pydantic models are the single source of truth. OpenAPI and the frontend TS types are generated from them, never hand-written.

## Commands
| Task | Command |
|---|---|
| Full stack | `docker compose -f infra/compose.yml up --build` |
| API dev server | `cd services/api && uv run uvicorn claimpilot.main:app --reload` |
| API tests | `cd services/api && uv run pytest` |
| API lint/types | `cd services/api && uv run ruff check . && uv run ruff format --check . && uv run pyright` |
| Web dev | `cd apps/web && npm run dev` |
| Web tests/lint | `cd apps/web && npm test && npm run lint && npx tsc --noEmit` |

## Environment notes
- Windows 11. Shells: PowerShell and Git Bash. Use `uv` for Python (project pinned to 3.13) and `npm` for web.
- Docker 29 with Compose.
