# ClaimPilot (working name): AI Expense & Reimbursement Agent, AI Innovation Lab S2

## Context
- **Challenge:** the AI Innovation Lab Season 2 challenge asks for an AI app built on one of two business cases. **The deadline is 12 Oct 2026; today is 7 Oct (about 5 days).**
- **Rules from the email:** show "measurable value" and go "beyond a simple demo". Use synthetic or public data only. Personal accounts are allowed, but no internal Jio documents go to them.
- **Decision: the Expense & Reimbursement Agent.** It scored 43 against 33 for Onboarding, on these criteria:
  - more AI-central
  - fits Jev better
  - doable at production quality in 5 days
  - more measurable
  - better demo
  - **Risk:** it's a crowded idea. The standout features below are the counter.
- **Your requirements:** production-grade engineering:
  - Docker, git and CI/CD
  - real tests, especially for the LLM
  - MCP and other LLM protocols
  - multi-agent development
  - a living task tracker, plus rules, skills and agents that let any new Claude session pick up the context
- **Settled choices:**
  - **LLM:** Claude API (personal account)
  - **System One:** Jev, from TypeSafe AI
  - **Hosting:** decide on Day 4. Everything runs on Docker Compose until then.
- **Cost constraint:** the app's **runtime API spend comes out of your own pocket**. Claude Code runs on the corporate account, so using top models for writing code is fine.
  - The app keeps Haiku, Sonnet and Opus in a **model registry**.
  - Every route can switch models through config.
  - A **model bake-off eval** picks the best balance of cost, accuracy and speed for each route of the MVP.

---

## 1. What we build (product scope)
**Flow:**
1. The user drops a pile of receipts: photos, PDFs, UPI screenshots.
2. The app extracts every field, categorises items, checks policy and trust, and groups items into claims.
3. It asks only the questions that are needed.
4. The user confirms, and the claim is submitted to a mocked finance system.
5. An approver view shows risk-ranked claims.

**Claim taxonomy:**
- Categories are config-driven: domestic and international travel, accommodation, local conveyance (including mileage), meals/DA, client entertainment, fuel/vehicle, mobile/internet, relocation, learning/certification, conference/offsite, medical (if allowed), WFH supplies, misc.
- Claim modes: **trip**, **period**, **event**, **allowance**.

**Must-build (★)**:
- pile-to-claim grouping
- System One/System Two hybrid with live latency and cost readout
- India-native checks: GSTIN checksum, CGST/SGST/IGST math, UPI proof, Hindi and handwritten bills, receipt-less self-declaration
- trust score: duplicates via pHash plus fingerprint; tampering; AI-generated receipts via C2PA; **prompt-injection receipts**
- policy-as-code with clause citations
- "ask only what's needed", using a mock calendar
- click-to-verify: OCR word boxes matched to extracted fields

**Stretch (☆), cut first:**
- approver view with an auto-approve KPI
- expense agent exposed as an MCP server for Claude Desktop/Teams
- A2A hand-off from a "Travel Desk agent"
- impact meter

The responsive PWA covers mobile, and the MCP server covers "use it from another chat app", so no separate chat channel is needed.

---

## 2. Architecture
**A modular monolith plus a few processes, where each split has a real reason.**

```
apps/web (Next.js PWA) ──SSE/REST──► api (FastAPI, modular monolith)
                                       ├─ ingest · extraction · decisions · policy · trust · claims · chat
                                       ├─ LLM layer: Claude API (System Two)   ─┐
                                       ├─ DecisionEngine: Jev | LLM adapter     │ OpenTelemetry → Langfuse
                                       ▼                                        │
                         Redis ◄── worker (Arq: async receipt pipeline) ────────┘
                         Postgres (claims, items, audit log, pHash)   MinIO/S3 (files)
                         mcp-finance (mock finance: submit/status)   mcp-corp (calendar, directory, policy resource)
```

### Do you need Kubernetes, microservices or Redis?
| Item | Verdict | Why |
|---|---|---|
| **Kubernetes** | **No** | One developer, 5 days, demo-scale traffic. It would cost days and judges won't see it. Instead, make it **k8s-ready**: 12-factor config, `/healthz` and `/readyz` endpoints, stateless containers, images published to GHCR. Put the scale-out path on a slide: HPA on worker queue depth. |
| **Microservices** | **No, use a modular monolith** | One FastAPI codebase with strict module boundaries. Separate processes only where justified: **worker** (a different latency profile), **MCP servers** (a protocol boundary that simulates external corporate systems), **web**. |
| **Redis** | **Yes, it's small and useful** | Four uses: the Arq job queue for bulk uploads; SSE progress pub/sub; a content-hash result cache, so re-uploads and eval reruns don't pay the LLM twice; per-user rate and cost limits. |
| **Caching** | **Yes, in 3 layers** | (1) Claude prompt caching on the stable prefix (tools, system prompt, policy doc, taxonomy). (2) Redis cache keyed by `sha256(file)` for extraction results. (3) Jev decisions cached by (state hash, question-set version). |
| Postgres | Yes | Claims and state machine, audit log, pHash index. Alembic migrations. |
| Object storage | Yes | MinIO locally, S3-compatible storage in prod. Uploads auto-delete after N days (the privacy story). |

### LLM layer (Claude API; implementation follows the claude-api skill's Python docs)
- **Model registry + per-route routing.** No model ID is hardcoded anywhere. Everything comes from `services/api/config/models.yaml`, and any route can be overridden by env, e.g. `ROUTE_EXTRACTION=sonnet`:
```yaml
# USD per 1M tokens. Re-check the pricing page before each bake-off.
models:
  haiku:  { id: claude-haiku-4-5,  context: 200k, max_output: 64k,
            price: {in: 1.00, out: 5.00, cache_read: ~0.10},
            thinking: budget_tokens, effort: unsupported, forced_tool_choice: ok,
            structured_outputs: yes, min_cache_prefix: 4096 }
  sonnet: { id: claude-sonnet-5-5, context: 1M,   max_output: 128k,
            price: {in: 2.00, out: 10.00, cache_read: 0.20},
            thinking: adaptive (off = between_tools), effort: low..max (default high),
            forced_tool_choice: 400, structured_outputs: yes, min_cache_prefix: 512,
            fallbacks: "default" }
  opus:   { id: claude-opus-5-5,   context: 1M,   max_output: 128k,
            price: {in: 4.00, out: 20.00, cache_read: 0.20},
            thinking: adaptive (cannot disable), effort: low..max (default medium),
            forced_tool_choice: 400, structured_outputs: yes, min_cache_prefix: 512,
            fallbacks: "default" }
routes:   # starting guesses; the bake-off decides
  extraction:        { model: haiku }
  extraction_retry:  { model: sonnet, effort: low }   # low confidence or failed validation
  agent_chat:        { model: sonnet, effort: low }
  question_draft:    { model: haiku }
  policy_compile:    { model: opus, effort: high }    # runs once per policy doc, then cached
  eval_judge:        { model: sonnet, effort: low }
```
- **Capability shim (`llm/client.py`):** the models differ in what they accept. The shim builds each request from the registry so a model switch never causes a 400:
  - Haiku takes `budget_tokens`, has no `effort` and allows forced `tool_choice`.
  - Sonnet and Opus 5.5 take adaptive thinking and `effort`, and return a 400 on forced `tool_choice`.
  - `fallbacks` applies only on Sonnet and Opus.
  - Every model's request is snapshot-tested.
- **Extraction:** image and PDF content blocks go through `client.messages.parse()` with a Pydantic schema (structured outputs), so the JSON is guaranteed valid.
- **Chat agent:**
  - Built on the Tool Runner (`client.beta.messages.tool_runner` + `@beta_tool`).
  - Per-turn hooks: an **approval gate** (no `submit_claim` without the user's confirmation) and audit logging.
  - `strict: true` tools. Forced `tool_choice` returns a 400 on Opus 5.5, so use `auto` plus prompt steering.
- **Prompt caching:** keep the prefix stable (tools → system → policy → messages) with a breakpoint after the policy. Check `usage.cache_read_input_tokens` in observability.
- **Refusal handling:** **server-side refusal fallback is enabled by default** (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`). Always check `stop_reason`.
- **Batch API:** 50% off, used for eval runs. `count_tokens` feeds the cost-per-claim metric.

### Model bake-off (how we choose the best cost, accuracy and speed balance)
- **Configurations compared:**
  - Haiku
  - Sonnet at `low` and `medium` effort
  - Opus at `low` and `medium` effort
  - a **cascade**: Haiku first, escalating to Sonnet when confidence is low or a validation fails (GST math, missing total)
- **Data:** the golden set. Start with 20 receipts (dev), then 100 (final).
- **Metrics:**
  - accuracy: field accuracy, critical-field accuracy (amount, date, GSTIN), category accuracy, JSON validity
  - speed: p50 and p95 latency
  - cost: **$ per receipt and per 1,000 receipts**, including cache effects
- **How it runs:** accuracy runs go through the **Batch API** (half price). Latency is measured on a small synchronous sample, because batch timing isn't representative.
- **Output:** a table plus a scatter plot (accuracy vs. $ per receipt, bubble size = latency) with the **Pareto frontier**. Pick per route and record each choice in `DECISIONS.md`. The same chart becomes a "measurable value" slide for the judges.
- **Caveat:** caches are per model, so a cascade gives up cache reuse. That's why the plain "one model at lower effort" option is measured too.

### API cost guardrails (your money)
- **Spend cap:** a **dedicated workspace and API key** in your personal Claude Console, with a **monthly spend limit** (e.g. $25–30).
- **Default to free:** dev and CI default to `LLM_MODE=replay` (recorded responses, $0). Live dev uses Haiku. Opus only runs in the bake-off and in escalation.
- **Eval budget:** the eval and bake-off runner **estimates cost first** (`count_tokens` × registry prices) and refuses to run over `--max-usd`.
- **Fewer tokens per call:**
  - Extraction results are cached in Redis by content hash, so no receipt is paid for twice.
  - Prompt caching is on. **Haiku only caches prefixes of 4,096 tokens or more**, so keep the stable prefix (system + policy + taxonomy) above that.
  - Images are downscaled before sending.
- **Ledger:** every call writes to the `llm_calls` table (model, tokens, cache hits, $, latency). This feeds a cost dashboard and the impact meter.
- **Rough estimate, to verify in the first run:** about $0.005 per receipt on Haiku, $0.01 on Sonnet and $0.02 on Opus, plus thinking tokens. A 100-receipt × 5-config bake-off comes to about $2–5. **The whole project should fit in about $15–30.** Jev is negligible ($0.042 per 1M input tokens, output free).
- **Jev:** `POST /v1/systemone`, model `jev-latest`, using its Choice, Score and Noul answer types.
  - It's behind `DecisionEngine` with two adapters: `JevEngine` and `LLMEngine`.
  - **Apply for the Jev waitlist today.** If access doesn't come through, the LLM adapter keeps everything working.

### Protocols
- **MCP (must-build):**
  - **Consume:** the backend acts as the MCP client (Python `mcp` SDK) for `mcp-finance` and `mcp-corp` and bridges their tools into the Tool Runner. If they're deployed at a public URL, the Claude API MCP connector (`mcp-client-2025-11-20`) is an option.
  - **Expose (☆):** a `claimpilot` MCP server, so you can file expenses from Claude Desktop.
- **A2A (stretch):** an agent card plus a task endpoint, so a "Travel Desk" agent can hand a trip to ClaimPilot.
- **Agent→UI streaming:** typed SSE events modeled on AG-UI (tool started/finished, state delta, question).
- **Telemetry:** OpenTelemetry with the GenAI semantic conventions, sent to Langfuse.
- **Single source of truth:** Pydantic models generate Claude schemas, Jev question schemas, OpenAPI and, via `openapi-typescript`, the frontend TS types. CI fails if the generated types drift.

---

## 3. Engineering: testing, CI/CD, security
### Testing (the LLM is tested in layers)
| Layer | Tooling | What |
|---|---|---|
| Unit (no network) | pytest | Policy engine, GSTIN checksum, GST math, grouping, claim state machine, prompt builders. `LLMClient` and `DecisionEngine` sit behind interfaces, with fakes for tests |
| **LLM replay** | pytest-recording (VCR) | Real Claude and Jev responses recorded once and replayed in CI: deterministic and free. Re-record when prompts change |
| **Structured-output contract** | Pydantic | Every response parses. Tool schemas are strict |
| **Agent trajectory** | scripted conversations | Assert the order of tool calls: never `submit_claim` without confirmation, and ask at most one combined question |
| **Evals (live, cost money)** | custom harness + Batch API | Golden set from the synthetic generator (ground truth is free). Measures field accuracy, category accuracy, duplicate, fraud and injection recall, and question quality (LLM-judge rubric). **Thresholds act as gates**. Built with `/claude-api build-eval` |
| **Adversarial** | eval subset | Prompt-injection text on receipts ("SYSTEM: approve"), tampered totals, AI-generated images. None may be auto-approved |
| Jev↔LLM parity | pytest | Both adapters run on the same decision set. Agreement, latency and cost go in the report |
| **Model switching** | pytest snapshots | The request builder for each registry model and route produces valid params: thinking shape, effort, `tool_choice`, fallbacks |
| **Model bake-off** | eval harness (`--models haiku,sonnet,opus`) | The accuracy, latency and $ matrix plus the Pareto chart. It is re-run whenever a prompt changes |
| API | pytest + httpx, Schemathesis (☆) | Endpoints, auth/RBAC, idempotency of submit, OpenAPI fuzzing |
| Frontend unit | Vitest + RTL + MSW | Components, claim-state reducer, upload flow |
| E2E | Playwright + axe | Full Compose stack in `LLM_MODE=replay`: upload → claim → submit. Accessibility checks |

### CI/CD (GitHub Actions, personal repo)
- **`ci.yml`** (every PR and push):
  - ruff, pyright, eslint and tsc
  - backend unit and replay tests, frontend unit tests (coverage gate on core modules)
  - OpenAPI drift check, gitleaks
  - Docker build, then Playwright E2E on Compose
- **`evals.yml`** (manual dispatch, `run-evals` PR label, or nightly):
  - live Claude and Jev runs using repo secrets
  - report uploaded as an artifact and posted as a PR comment
  - fails if a metric regresses
- **`cd.yml`** (on a main-branch tag):
  - build images and push to GHCR
  - deploy (target decided on Day 4), then smoke test
- **Pre-commit:** ruff, prettier, gitleaks.

### Security, compliance and other things you'd likely miss
- **Secrets and data:** secrets live in a gitignored `.env` and in GitHub secrets, never in code. Data is synthetic only, and brand names are fictional.
- **Keep the brief files out of git:** **don't commit the two brief `.txt` files.** The repo lives in a subfolder (`claimpilot/`) for exactly this reason.
- **Prompt injection:** receipts are untrusted input. Extracted text is treated as data, final decisions are deterministic or rule-gated, and these cases are in the eval set.
- **Auth:** demo auth with roles (employee/approver/finance) and RBAC enforced in the API.
- **Abuse limits:** upload validation (MIME sniffing, size and page caps), a per-user daily token budget, idempotency keys on submit.
- **Observability:** Langfuse traces, structured JSON logs, Sentry free tier.
- **Demo support:** seed and "reset demo" scripts, a Mermaid architecture diagram, a README with the eval and impact numbers.
- **Environment notes:**
  - Docker 29 is installed. If it's Docker Desktop on a corporate laptop, confirm the licence or IT approval, or switch to Rancher Desktop.
  - Python is 3.10, so `uv python install 3.12`.
  - Optional installs: `gh` (CLI checks of CI/PRs) and pnpm via `corepack`.

---

## 4. Dev workflow: Claude Code setup, multi-agent, task tracking
**Repo layout** (`AI-Innovation-Lab/claimpilot/`):
```
CLAUDE.md                     # <200 lines; imports @docs/TASKS.md
.claude/settings.json         # hooks + permissions
.claude/rules/                # path-scoped rules
.claude/skills/<name>/SKILL.md
.claude/agents/<name>.md
.claude/hooks/*.py            # cross-platform hook scripts (uv run)
docs/PLAN.md  docs/TASKS.md  docs/ARCHITECTURE.md  docs/DECISIONS.md (ADR log)
apps/web/                     # Next.js PWA
services/api/                 # FastAPI app + worker entrypoint (same package)
services/mcp-finance/  services/mcp-corp/
data/synth/                   # generator, templates, fonts; outputs gitignored except fixtures
evals/                        # golden sets, runners, reports
infra/                        # compose.yml, compose.prod.yml, Caddyfile
.github/workflows/            # ci.yml, evals.yml, cd.yml
```

**Rules** (`.claude/rules/`; verified: a `paths:` frontmatter makes a rule load on demand when matching files are touched):
- `data-compliance.md`: no `paths`, so it's always loaded. Synthetic data only, no secrets, no real brands, never commit the brief files.
- `backend.md`: `services/**/*.py`. Module boundaries, async conventions, Pydantic as the source of truth.
- `frontend.md`: `apps/web/**`. Generated API types only, accessibility, streaming UI patterns.
- `llm.md`: `**/llm/**`, `evals/**`, `**/prompts/**`.
  - No hardcoded model IDs: everything goes through the registry and the capability shim.
  - Structured outputs, cache-safe prompt order.
  - Every call is logged to the cost ledger.
  - Live calls only behind `LLM_MODE=live`.
  - Every prompt change gets re-recorded and re-evaluated.
- `testing.md`: test files. The test-layer table above, plus the rule to never call live APIs in CI unit tests.

**Skills** (`.claude/skills/`):
- `task-tracking`
- `synthetic-receipts` (bundled generator scripts)
- `run-evals` (run, compare to baseline, update the report)
- `model-bakeoff` (run the model and effort matrix within a `--max-usd` budget, chart the Pareto frontier, update the routes and DECISIONS.md)
- `api-contract` (endpoint → OpenAPI → TS types → tests)
- `add-decision` (Jev question + LLM adapter + parity test)
- `add-mcp-tool`
- `deploy` (`disable-model-invocation: true`, so it runs only when you invoke it)

**Agents** (`.claude/agents/`; verified: the `skills:` field preloads skills, and `isolation: worktree` gives an agent its own worktree for parallel work). No agent gets `bypassPermissions`.
| Agent | Skills | Notes |
|---|---|---|
| `backend-dev` | api-contract, add-decision, add-mcp-tool | worktree when parallel |
| `frontend-dev` | api-contract | worktree when parallel |
| `llm-engineer` | synthetic-receipts, run-evals, model-bakeoff, add-decision | prompts, extraction, evals, model choice |
| `test-engineer` | run-evals | writes tests from the contract, not the implementation |
| `devops` | deploy | Docker, CI, infra |
| `reviewer` | (none) | read-only tools; checks correctness, security and compliance before each merge |

**Multi-agent usage:**
- Freeze the **contract first**: Pydantic schemas plus OpenAPI on Day 1.
- Then run `backend-dev` and `frontend-dev` in parallel worktrees, while `test-engineer` writes tests from the contract.
- `reviewer` gates each merge.
- Use parallel agents only where the work is really independent; otherwise one thread is faster.

**Hooks** (`.claude/settings.json`):
- PostToolUse on `Edit|Write`: run ruff or prettier on the edited file.
- PreToolUse on `Bash`: block `git commit` if `gitleaks protect --staged` finds secrets.

**Task tracking** (`docs/TASKS.md`, imported into CLAUDE.md):
- High-level milestones go in first.
- Low-level subtasks are added **only when a milestone starts**.
- Legend: `[ ]` todo, `[~]` in progress, `[x]` done, `[!]` blocked.
- A "Current focus" line sits at the top, with a dated done-log at the bottom.
- Decisions go to `docs/DECISIONS.md` (one ADR paragraph each).

**Will a new chat have the context? Yes:**
- `CLAUDE.md`, the imported `TASKS.md` and the always-on rules load automatically.
- Skills and agents are discovered automatically.
- `DECISIONS.md` stops a new session from reopening settled choices.
- This plan gets copied to `docs/PLAN.md`, because the plans folder isn't auto-loaded.
- I'll also save a project memory with the deadline and the repo path.
- To resume, start a new session with: *"Read CLAUDE.md, continue the current focus in TASKS.md."*

---

## 5. Milestones (these become TASKS.md; ★ = must, ☆ = stretch)
- **M0 Setup (7 Oct, ≤ half a day)**:
  - git init, repo layout, Compose (api, worker, web, postgres, redis, minio, mcp-*)
  - CI skeleton, pre-commit
  - Claude Code config (CLAUDE.md, rules, skills, agents, hooks), TASKS.md, DECISIONS.md
  - **apply for the Jev waitlist**
- **M1 Data & extraction (7–8 Oct)**:
  - synthetic receipt generator: Faker `en_IN`, valid GSTINs, Jinja2 templates rendered by Playwright, Augraphy degradation, adversarial set
  - **model registry, capability shim, cost ledger**
  - extraction pipeline, OCR word boxes, Redis cache
  - eval harness with baseline numbers
  - **first bake-off on 20 receipts** to set the initial routes
- **M2 Intelligence (9 Oct)**:
  - DecisionEngine (Jev and LLM adapters)
  - taxonomy and policy-as-code with citations
  - trust score (pHash, math, EXIF, C2PA, injection)
  - grouping and the claim state machine
  - `mcp-finance` and `mcp-corp`
- **M3 Experience (10 Oct)**:
  - PWA: drop zone and camera, streaming progress, claim cards, click-to-verify
  - chat agent with question flow and approval gate
  - Playwright E2E
- **M4 Standouts & ship (11 Oct)**:
  - approver view ☆, impact meter ☆, Jev-vs-LLM benchmark
  - **pick hosting** and complete CD
  - **final bake-off on 100 receipts, routes locked**, live eval run
  - ClaimPilot MCP server ☆, A2A ☆
- **M5 Submit (12 Oct, morning)**:
  - polish, 3-minute demo video, README with metrics
  - **submit early**

If the schedule slips, cut ☆ items first. Never cut evals or tests: they are the "measurable value" proof.

## 6. Verification
- `docker compose up` brings up the full stack. `/healthz` is green on every service.
- CI is green: lint, types, unit, replay, E2E and gitleaks.
- `evals.yml` live run: field accuracy, category accuracy, and duplicate, fraud and injection recall at or above their thresholds. The report is committed.
- Manual E2E on desktop and phone: 15 mixed receipts produce correct claims, flags and a single question, and the claim submits to `mcp-finance`.
- Adapter parity: switching `DECISION_ENGINE=jev|llm` gives equivalent claims.
- Model switching: setting `ROUTE_EXTRACTION` to `haiku`, then `sonnet`, then `opus` runs end to end with no 400s.
- The bake-off report shows the Pareto chart and the chosen routes. Spend in the ledger matches the Console within about 5%.
- A fresh Claude session given only *"continue"* picks the correct next task from TASKS.md.
