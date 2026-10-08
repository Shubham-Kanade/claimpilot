# Decision log (ADR-lite)

Each entry gives the decision, the reason, and its consequences. Add new entries at the bottom. To change a decision, supersede it with a new entry rather than editing the old one.

### ADR-001: Build the Expense & Reimbursement case, not Onboarding (2026-10-07)
- **Decision:** the Expense case scored 43 vs 33 against impact, production-grade feasibility in 5 days, how central AI is, fit for Jev, web-first, standout potential, demo value, measurability, synthetic-data ease, and differentiation.
- **Why:** turning receipts into structured claims is the work AI is best at. The outcome is measurable (accuracy, $ per receipt, time saved). The scope fits 5 days.
- **Consequences:** it's a crowded idea, so differentiation comes from India-native checks, the trust score (C2PA, prompt injection), the System One/Two hybrid, and policy-as-code with citations.

### ADR-002: System Two (Claude) + System One (Jev) hybrid (2026-10-07)
- **Decision:** the Claude API handles perception (images/PDFs → JSON) and conversation. Jev (TypeSafe AI, text/JSON only) makes the fast typed per-item decisions: category, policy flags, duplicate likelihood, risk, routing.
- **Why:** Jev cannot read images. Its 70–500 ms latency and very low cost suit high-volume decisions.
- **Consequences:** a `DecisionEngine` interface with `JevEngine` and `LLMEngine`. Jev is waitlisted, so the LLM adapter is the fallback, and parity tests are required.

### ADR-003: Modular monolith; no Kubernetes or microservices (2026-10-07)
- **Decision:** one FastAPI codebase with strict module boundaries. Separate processes only for the worker, the MCP servers and the web app. Docker Compose for dev and prod.
- **Why:** one developer, 5 days, demo-scale load.
- **Consequences:** stay k8s-ready (12-factor config, health endpoints, stateless containers, GHCR images). Scale-out is a slide, not code.

### ADR-004: Redis for the queue, pub/sub and caching (2026-10-07)
- **Decision:** Arq on Redis for bulk uploads, Redis pub/sub for SSE progress, a `sha256(file)` extraction cache, and rate and cost limits. Three caching layers: Claude prompt caching, the extraction cache, and a Jev decision cache.

### ADR-005: Claude API as the System Two provider; model registry and bake-off (2026-10-07)
- **Decision:** the Claude API on the user's personal account. Haiku 4.5, Sonnet 5.5 and Opus 5.5 are listed in `services/api/config/models.yaml`, with per-route assignment that env vars can override. A capability shim handles per-model API differences.
- **Why:** runtime spend is personal money, so the model for each route is chosen by a bake-off on cost, accuracy and latency (Pareto frontier) rather than assumed.
- **Consequences:**
  - No model IDs are hardcoded.
  - CI and dev run in `LLM_MODE=replay`.
  - Live runs need a `--max-usd` budget.
  - Every call is logged in the `llm_calls` cost ledger.
  - Claude Code itself (the dev tooling) runs on the corporate account, so top models are fine for writing code.

### ADR-006: Hosting deferred to Day 4 (2026-10-07)
- **Decision:** build and test on local Docker Compose. Choose between a VM with Compose, Render/Railway, and Vercel + Fly on 11 Oct.

### ADR-007: No separate chat channel (Telegram etc.) (2026-10-07)
- **Decision:** dropped. The responsive PWA covers mobile, and the ClaimPilot MCP server (☆) covers "use it from another chat surface".

### ADR-008: Repo lives in `claimpilot/`, a subfolder of the workspace (2026-10-07)
- **Decision:** the challenge brief files stay in the parent folder, outside git, so internal documents never reach the personal GitHub.
- **Consequences:** open `claimpilot/` as the workspace or Claude Code root so `.claude/` settings, agents and hooks load.

### ADR-009: Local upload volume behind a storage interface; S3 chosen with hosting (2026-10-07)
- **Decision:** uploads go to a Docker volume (`UPLOAD_DIR`) behind a `Storage` interface. The plan had MinIO, but it is dropped for now.
- **Why:** MinIO's community Docker image distribution became unreliable in late 2025. One less service in 5 days.
- **Consequences:** add `S3Storage` (R2, S3 or the host's object store) on Day 4 if the chosen hosting needs it. Code depends only on the interface.

### ADR-010: The submission rules drive priorities (2026-10-08)
- **Context:** the second organiser email says missing any item means elimination. The required items:
  - a fully functional build by 12 Oct
  - a technical design document (overview with track, architecture and flow diagrams, setup and deployment, code explanations and assumptions)
  - a YouTube demo link
  - code on a personal GitHub, with no access requests
  - comprehensive unit tests
  - the hosted link, if hosted
  The rubric covers innovation, technical complexity, code quality, testing, documentation and the demo.
- **Decision:**
  - The repo is **public**.
  - `docs/TECHNICAL_DESIGN.md` is a living document, updated every milestone and exported to PDF at M5.
  - Coverage gates enforced in CI: api ≥ 85% (core modules aim for 90%), web ≥ 80% lines.
  - Hosting becomes ★, with a **no-login demo mode** (persona switcher) instead of real auth.
  - The video is public or unlisted.
  - A `submission-check` skill runs nightly.
- **Consequences:** if time runs short, cut ☆ features (A2A, ClaimPilot MCP server, impact meter). Never cut tests, the TDD, the video or hosting. The email itself stays out of the repo.

### ADR-011: Claude Haiku 5.5 is the default bulk model (2026-10-08)
- **Decision:** add `claude-haiku-5-5` as registry key `haiku`, the default for `extraction`, `question_draft` and `decision_fallback` (effort `low`). Keep Haiku 4.5 as `haiku45`, a bake-off baseline only.
- **Why:** released 2026-10-07. It is $0.10 / $0.50 per MTok (≤100K-token prompts), 10× cheaper than Haiku 4.5. Anthropic positions it for high-volume extraction and classification. It has a 1M context window, adaptive thinking (can be disabled), `effort`, forced `tool_choice`, structured outputs, image and PDF input, and a 512-token cache minimum. Verified via the Models API and the pricing page.
- **Consequences:**
  - The registry models a long-context price tier (>100K-token prompts: $0.50 / $2.50) and the 50% batch discount.
  - Sonnet 5.5 cache reads are corrected to $0.10/MTok.
  - The bake-off still decides the final routes (ADR-005).
  - The per-receipt cost estimate drops by about 10×.

### ADR-012: Self-hosted fonts instead of Google Fonts (2026-10-08)
- **Decision:** use the `geist` npm package (`next/font/local`) instead of `next/font/google`.
- **Why:** the Docker build could not reach fonts.googleapis.com (corporate proxy / TLS), so the web image failed to build. Self-hosting makes builds hermetic in Docker, CI and any hosting provider.
