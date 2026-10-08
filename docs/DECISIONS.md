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

### ADR-013: Bake-off uses synchronous calls, not the Batch API (2026-10-08)
- **Decision:** the eval/bake-off runner (`claimpilot.evals.run`) makes synchronous calls (4 concurrent) and does not use the Message Batches API.
- **Why:** at Haiku 5.5 prices a 20-receipt run costs well under $0.01, so the 50% batch discount saves almost nothing. Synchronous calls also give the real latency the Pareto choice needs. Batch remains an option for 1,000+ receipt runs.
- **Consequences:** there are two spend guards. A free `count_tokens` estimate checks against `--max-usd` before the run, and a running-spend check aborts mid-run.

### ADR-014: Live client hard-guarded; default tests ignore `.env` (2026-10-08)
- **Decision:** `AnthropicLLM.from_settings` refuses unless `LLM_MODE=live`. The default pytest run ignores the developer's `.env`, and only `-m live` runs read it. `Settings` reads the repo-root `.env` first, then a local `.env`.
- **Why:** these came from the reviewer agent's findings. Spend must be impossible without an explicit opt-in, and tests must be hermetic. A `REPO_ROOT` bug that crashed the container at `/app` was also fixed, with a regression test.

### ADR-015: Synthetic data follows GST 2.0 rates (2026-10-08)
- **Decision:** hotel rooms up to ₹7,500 a night are 5% GST (GST 2.0, effective 22 Sep 2025), and 18% above. The other rates are unchanged: restaurants, cabs, economy flights and AC train fares 5%; telecom and professional services 18%; fuel and informal bills no GST. Trust checks still accept the legacy 12% and 28% slabs, because older bills exist.
- **Labelling conventions** (from data/synth, mirrored in extraction prompt v1):
  - ticket `date` and `time` are the journey departure, and the PNR is `invoice_number`
  - a hotel folio's `date` is the checkout date
  - `travel_from` and `travel_to` are empty for local rides
- **Generator deviations:**
  - Edge/Chrome fallback when Playwright's Chromium download is blocked by the proxy
  - `opencv-python` instead of the headless build, because Augraphy needs it
  - Augraphy fold noise disabled because it can't be seeded
  - phone photos saved as JPEG

### ADR-016: PDFs are rasterised to page images before extraction (2026-10-08)
- **Decision:** `prepare_document` renders each PDF page with pdfium (`pypdfium2`) at the same long-edge cap as photos and sends image blocks only. Raw `document` blocks are no longer sent.
- **Why:** the corporate network resets connections whose body contains a PDF (a 48 KB PDF fails with `ConnectError` while a 227 KB image succeeds). Rasterising also gives one uniform image path for extraction, cost and click-to-verify, and exact page counts.
- **Measured:** dev-split receipts are about 4.7–6.5K input tokens each, so the per-receipt estimates are Haiku 5.5 about $0.0009, Haiku 4.5 $0.008, Sonnet 5.5 $0.019 and Opus 5.5 $0.037.

### ADR-017: A flat "wire" schema for structured outputs (2026-10-08)
- **Context:** the first bake-off failed 40/40 with `400 Schema is too complex` / `Grammar compilation timed out`, at $0 cost. Claude structured outputs allow at most 24 optional and 16 union-typed parameters per request. `ExtractedReceipt` has about 25 nullable fields.
- **Decision:** the LLM fills `extraction.schema.WireReceipt`: every field is required, "not printed" is an empty string, and amounts are number strings, so there are no unions. `to_domain` maps it to `ExtractedReceipt`, parsing amounts and flagging unparseable values as low-confidence. A test asserts the wire schema has 0 optional and 0 union parameters.
- **Result:** one live check on the hardest dev receipt (degraded handwritten bill with an injection) gave 100% field accuracy with the injection flagged, at $0.0009 on Haiku 5.5.
- **Lesson:** the smoke test's one-field schema could not catch this. A wire-schema live check now precedes any bake-off.

### ADR-018: Extraction runs on Haiku 5.5 alone; escalation is opt-in (2026-10-08)
- **Evidence** (dev split, 20 receipts, prompt `extract_v1`, report `evals/reports/2026-10-08-bakeoff-dev-haiku.md`):

  | Config | Critical acc | Field acc | JSON valid | Injection recall | $ / 1k receipts | p50 / p95 |
  |---|---|---|---|---|---|---|
  | haiku-low | 100% | 95.0% | 100% | 100% | **$0.42** | 3.1 s / 3.9 s |
  | cascade (→ Sonnet on unsure critical fields; 7/20 escalated) | 100% | 96.9% | 100% | 100% | $4.22 | 3.3 s / 7.5 s |

- **Decision:** the `extraction` route stays on `haiku` (effort low, thinking off). `ReceiptExtractor(escalate=...)` now defaults to False. The cascade costs 10× for +1.9 pp on non-critical fields only.
- **Caveats:** 20 receipts with one injection case. Final numbers come from the 80-receipt test split in M4 (about $0.03 on Haiku). Sonnet, Opus and Haiku 4.5 were not run, by the user's choice: Haiku met every gate first. Top residual errors are `doc_type` (2), `subtotal` (2) and `travel_from`/`travel_to` (2); these are prompt-tuning candidates.
- **Total M1 API spend so far:** about $0.10.
