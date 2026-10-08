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

### ADR-019: Jev calls use the OS trust store; Jev API shape confirmed (2026-10-08)
- **Problem:** `api.typesafe.ai` failed TLS verification (`unable to get local issuer certificate`). The corporate proxy (Zscaler) re-signs traffic to that host, but passes `api.anthropic.com` through untouched. Python bundles its own CA list, which doesn't contain the proxy's root CA, while the OS certificate store (and so the browser) does.
- **Decision:** `claimpilot.net.ssl_context()` verifies against the OS trust store via `truststore` on Windows and macOS, and uses the default bundle elsewhere (Linux containers, hosting). Verification is never disabled.
- **Jev wire format** (from docs.typesafe.ai, verified live: HTTP 200 in 760 ms): `POST {base}/v1/systemone` with `Authorization: Bearer <key>`. Body `{state, model: "jev-latest", questions: {id: {type: choice|score|noul, instructions, criteria}}}`. Response `{model, answers: {id: {type, choice|score|noul, confidence, probabilities, legend}}, usage}`. Choice takes up to 255 options and Score 2 to 10 levels, with criteria as a map or a list. 429 and 529 get exponential-backoff retries.

### ADR-020: Golden dataset and persona roster committed (2026-10-08)
- **Decision:** `data/synth/golden/` (ground-truth JSON, manifest and `personas.json` with grade and base city; no images; ~0.3 MB) is committed, so policy, grouping and decision evals run in CI without rendering anything or spending money. `claimpilot.evals.golden` loads it and builds `ProcessedDocument`s as the pipeline would with perfect extraction.
- **Contract frozen** for parallel M2 work: `claimpilot.domain.claims` (Employee, Decisions, ProcessedDocument, Claim, ClaimMode, ClaimStatus, OpenQuestion) and the extended `Finding` (source, clause_id, clause_text, document_id).

### ADR-021: System One decisions: Jev first, LLM fallback, confidence-gated (2026-10-08)
- **Decision:** per document we ask three typed questions: `category` (choice, 14 options), `alcohol_present` and `personal_expense` (noul). `DECISION_ENGINE=jev` runs a cascade: Jev answers everything; any choice answer under 0.7 confidence is re-asked on Claude Haiku 5.5; if Jev is down, everything goes to the LLM. Answers below the gate become questions for the employee, never silent guesses. `DECISION_ENGINE=llm` runs the LLM alone.
- **Evidence** (`evals/reports/2026-10-08-jev-vs-llm*.md`; ground-truth receipts as input, so extraction errors don't leak in):

  | Set | Engine | Category acc | Acc when confident (coverage) | ECE | p50 | $/1k docs |
  |---|---|---|---|---|---|---|
  | seed 42, 100 docs (wording tuned here) | Jev | 90.0% | 97.8% (92%) | 0.060 | 610 ms | $0.040 |
  | | LLM | 90.0% | 100% (80%) | 0.078 | 1,875 ms | $0.125 |
  | **seed 7, 80 docs (held out)** | Jev | **85.0%** | 94.4% (89%) | 0.085 | 546 ms | $0.040 |
  | | LLM | 85.0% | 97.0% (82%) | 0.087 | 1,655 ms | $0.117 |

  Alcohol: 100% accuracy and recall on the seed-42 set (including the 4 `over_policy` dinners) for both engines. Personal-expense recall on 8 probes: 100% for both; false-alarm rate 9–12.5%, all on genuinely ambiguous kirana and UPI-to-person documents.
- **What changed it:** the first run had Jev at 77% category accuracy and a 57% personal-expense false-alarm rate. Both traced to question wording, not the model: "personal purchase" made restaurant bills look personal, and my own category text put parking under fuel. Question v2 defines the yes/no answers with explicit `criteria`, and describes group dinners by their receipt signature (7+ dishes, over about ₹2,500), which took client-dinner detection from 0/9 to 9/9.
- **Honest caveats:** the wording was tuned on seed 42, so 90% is optimistic and the held-out 85% is the number to quote. The remaining errors are documents the receipt cannot explain (a ₹120 UPI payment to a person's name; kirana bills). Those arrive with confidence under 0.7 and become questions. The real fix for client dinners is calendar context from `mcp-corp`, now an optional `document_state` input.
- **Not built:** a decision cache. Jev costs $0.04 per 1,000 documents, so caching saves nothing (ADR-004 layer 3 dropped).

### ADR-022: Deterministic orchestration, the model only at the edges (2026-10-08)
- **Decision:** the pipeline is plain, explicit code (read → decide → trust → policy → group → ask), not a free-running tool-using agent. Models are used where language or vision is genuinely needed: reading receipts (Claude Haiku 5.5), typed decisions (Jev, LLM fallback), and parsing the employee's one free-text reply into answers (Haiku, flat structured output, skipped when only one question is open).
- **Why:** money moves at the end, so the path to it must be auditable and rule-gated. Routing (`auto_approve` vs `finance_review`), policy verdicts, duplicate and tamper findings and the confirmation gate are deterministic functions that never read model prose. Cost and latency are predictable (a first read is about $0.0005 and 2 s; the whole pipeline costs $0.0024 per receipt on the demo pile) and every step is unit-testable. An open-ended agent would add cost, variance and an injection surface for no gain in this flow.
- **MCP is still first-class:** the finance system, HR directory, calendar and policy are real MCP servers consumed through typed adapters; the pipeline uses them for submission, personas, and calendar-based answers. A tool-using chat agent on top of the same MCP tools is a possible extension, not required for the flow.
- **Consequences:** prompt injection in a receipt can change what is extracted and what System One is asked, which are then checked (second read, scan for instructions, arithmetic, GSTIN) and can never approve anything: a decision below the confidence gate becomes a question, and approvals depend only on rules.

### ADR-023: The mocked enterprise systems are MCP servers with typed clients and a role-scoped tool bridge (2026-10-08)
- **Decision:** finance (`mcp-finance`, :8101) and the corporate systems (`mcp-corp`, :8102: employee directory, calendar, the policy as a resource) are real MCP servers over stateless streamable HTTP, each its own package with its own tests and Dockerfile. The API consumes them through typed clients (`claimpilot.mcp`: every failure is an `McpError` subclass) and port adapters (`McpDirectory`, `McpCalendar`, `McpFinance`) that implement the pipeline's small protocols. `McpBridge` exposes MCP tools to Claude as strict tool definitions with a **per-role allow-list** (`EMPLOYEE_AGENT_TOOLS`: an employee agent may read the calendar and submit, never decide a claim). `submit_claim` is idempotent on its key; `decide_claim` refuses a rejection without a comment.
- **Why:** a real protocol boundary exactly where the real systems would sit, so swapping them in changes adapters, not the pipeline; stateless HTTP means a server restart loses no session.
- **Consequences:** the servers are **unauthenticated** and published on loopback only in Compose; they model systems behind a company network, and a real deployment would put authentication in front. `/readyz` checks Postgres and Redis only; Compose health checks gate the MCP containers.

### ADR-024: Policy as code: numbered YAML clauses bound to rule classes, every finding cites its clause (2026-10-08)
- **Decision:** `services/api/config/policy.yaml` holds 14 clauses of a fictional company policy. Each clause has plain-English `text` (shown verbatim when a finding cites it) and `params` (what the code enforces); a rule clause is bound to exactly one rule class by `rule:`. Unknown keys are rejected at load time, and tests assert that text and parameters agree. Evaluation is deterministic: no model runs at evaluation time (the `policy_compile` route is reserved for compiling a PDF policy later).
- **Details that matter:** hotel caps (grade × city tier) compare the pre-GST room rate, because the folio prints it per night; the per-head entertainment cap runs only once the headcount is known (a question answers it); every claim-level finding carries the document it is about.
- **Evidence:** calibrated on the golden set so every legitimate receipt passes (0 of 80 false positives) and every injected over-policy case is caught (4 of 4). Caveat: the synthetic generator draws hotel tariffs from a grade band that does not depend on city tier, so the Tier-2 caps sit just under Tier-1; a production policy would widen the gap.

### ADR-025: Grouping is conservative; every open question goes into one message (2026-10-08)
- **Decision:** grouping is pure and policy-free. A **trip** is anchored by tickets and hotel folios away from the base city; meals, cabs and fuel join only when dated inside the trip window (± 1 day) *and* bought in a trip city (or an airport/station transfer). An **event** is one client dinner, course or conference. A **period** claim is one category in one calendar month. A wrongly merged trip is worse than a separate claim, so ambiguity stays apart.
- **Questions** are built only when the claim cannot complete without them; answers already known (given, read from the calendar when exactly one client event fits the date, or printed on the receipt) are never asked again. All open questions of a claim are merged into one message by `combined_prompt`; ids are stable (`q-<kind>-<hash>`).
- **Routing:** `auto_approve` only for a claim of ₹10,000 or less with no high or warn finding and nothing open; everything else is `finance_review`. Findings never prevent submission; they decide who looks.
- **Evidence:** grouping F1 = 1.000 on the golden set's ground-truth trips.

### ADR-026: The trust layer: candidates plus verification, a minimal C2PA reader, no untrusted text in findings (2026-10-08)
- **Duplicates:** a 64-bit text-tuned dHash *proposes*, a field fingerprint (`v1:<merchant hash>|<date>|<total>|<reference hash>`) *verifies*. Distance 8 with a fingerprint, 0 without. On 4,950 document pairs the hash alone finds 1 of 4 true duplicates with 13 to 779 false positives (different bills from one template sit 0-4 bits apart, while real re-photographs are 20-35 bits away); with verification it finds 4 of 4 with none. A conflicting date, total or reference vetoes an image-only match.
- **C2PA:** a dependency-free reader is the default. It reports content credentials as present but unverified and raises a finding only for an AI declaration; it never claims a signature is valid. The native `c2pa-python` is **opt-in** (`C2PA_NATIVE=1` plus the `c2pa` extra): on Windows every read raised a native access violation, and by default it fetches a remote manifest URL named in the file's XMP, which is server-side request forgery from an upload. Native mode switches both that and OCSP off.
- **Findings carry no free untrusted text** (metadata strings, manifest fields, injected sentences): they carry fixed labels or short values cut to one clean line (`domain/text.plain_text`), so a hostile file cannot write into what a person or a model reads next. The independent review found three places that broke this (a city in claim titles, a GSTIN in a finding, the System One state) and they were fixed.
- **Score and verdict:** 100 minus 40 per high, 15 per warn, 3 per info. `block` for a *high* finding with a conclusive code (prompt injection, AI-generated declarations, the bill's own arithmetic contradicting itself) or a score under 30; `review` for a score under 80 or any high finding; else `clean`. A check that raises degrades to a `trust_check_failed` warning instead of failing the document.
- **Evidence** (100 synthetic documents, ground-truth receipts as the extraction): duplicates 4/4, injection 4/4 (text scan 16/16 on the generator's lines, 0/96 other documents), tampered 4/4 blocked, 0 false positives with a high finding on 88 untagged documents.

### ADR-027: Every answer re-runs policy; the pipeline's own questions survive (2026-10-08)
- **Decision:** `pipeline/finalize.refinalize` recomputes a claim's findings, questions, status and route from its stored documents after every answer. The "what was this for?" questions the pipeline adds for categories the models were unsure of (`q-category-*`) and all earlier answers are kept. A document that already carries a "looks personal" or self-declaration question gets no second category question, because both already ask what it was for.
- **Why:** the per-head entertainment cap needs the headcount, so one answer can turn `auto_approve` into `finance_review` (or back). Question ids are derived from document ids, so claims are always built from the stored documents.

### ADR-028: Extraction prompt v2, direction-aware arithmetic, findings softened where the reader was unsure (2026-10-08)
- **Problem (first full-stack run):** a genuine flight ticket came out `block` because the model listed only the base fare, not the five fee rows, so the line items fell short of the printed subtotal; a degraded bill whose GSTIN checksum character was misread was flagged `high`, although the model had itself listed `merchant_gstin` as low confidence.
- **Decision:** (1) `extract_v2.md`: fee and surcharge rows are line items, and `subtotal` is described. (2) Line items adding up to *less* than the subtotal give `items_incomplete` (warn: a row may be unread); *more* than the subtotal stays `items_subtotal_mismatch` (high: a figure was changed). (3) The GST rate check accepts any plausible tax base: the subtotal before or after the discount, with or without the service charge, or any single row (a ticket taxes the base fare only). (4) A finding that rests on a field the reader marked low-confidence drops from high to warn ("compare it with the original"), and only *high* findings with conclusive codes block.
- **Evidence:** dev bake-off on Haiku 5.5, v1 → v2: critical-field accuracy 100% → 100%, field accuracy 95.0% → 96.0%, line-item F1 0.95 → 0.93, cost $0.42 → $0.52 per 1,000 receipts (more rows to write). The flight ticket now reconciles exactly. Trust evaluation: tampered documents still 4/4 blocked; false `gst_rate_mismatch` warnings on the 88 untagged documents 10 → 1 (a dinner with liquor, which GST does not tax).
- **Residual risk:** a bill with no printed subtotal whose rows were not all read still raises `total_mismatch`; the prompt is the main defence.

### ADR-029: Demo recordings are made inside Docker with the LLM engine (2026-10-08)
- **Decision:** the replay cassettes for the demo are recorded by running the real stack with `infra/compose.record.yml` (`LLM_MODE=live LLM_RECORD=1 DECISION_ENGINE=llm`) and `scripts/smoke.py`, with the host's `services/api/replay/` mounted into the container.
- **Why:** the request hash covers the rasterised page images, which depend on the Pillow and pdfium builds: recording in the image that will host the demo guarantees the hosted replay hits. Jev is a plain HTTP call, not an LLM request, so it cannot be recorded; replay mode therefore uses the LLM engine, which answers the same typed questions. The local Jev run is shown in the video.
- **Cost:** about $0.001 per receipt on Haiku 5.5; a recording that already exists is replayed, not paid again.

### ADR-030: The hosted demo is one container: embedded runtime, SQLite, replayed answers (2026-10-08)
- **Decision:** the public demo runs as a single Hugging Face Space (Docker SDK; the project owner's choice: free, no card, public URL). One image holds Caddy (:7860), the Next.js server, the API with `RUNTIME=embedded` (each batch runs as a task of the API process, with an in-memory event bus and no Redis or Arq), the two mock MCP servers on loopback, and SQLite in `/data`. `LLM_MODE=replay` with `DECISION_ENGINE=llm` serves every model answer from the recordings committed in `services/api/replay/`; `DEMO_MODE=1` adds "start over" and plain-language messages; `DUPLICATE_SCOPE=employee` keeps visitors from flagging each other; `DEMO_TODAY` pins the clock inside the policy's submission window. A supervisor (`deploy/hf-space/start.py`) starts the pieces in dependency order and exits if any dies, so the host restarts the container.
- **Why:** free to run and to review, nothing to configure, no model spend, deterministic. The Compose stack stays the reference deployment (Postgres, Redis, a separate worker); both runtimes run the same pipeline code and differ only in `wiring.py`.
- **Build:** the Space repo holds just `Dockerfile` and `README.md`; the build clones the public GitHub repo (`SOURCE=fetch-git`, `REF=main`), so GitHub is the single source of truth. A local build uses `--build-arg SOURCE=fetch-local`.
- **Consequences:** state resets on every restart (intended); visitors share one sandbox per persona, so "Start over" exists; a receipt that was never recorded cannot be read in the demo (the UI says so, and the video shows live reading on a laptop); CI builds this image and runs `scripts/smoke.py` against it in replay mode, which also catches recordings that stop matching on another machine.

### ADR-031: A second opinion before anyone is accused; the calendar informs the decision (2026-10-08)
- **Problem (first run of the demo pile with the real models):** the models made the mistakes models make, and the trust layer turned them into accusations. A genuine rail ticket was blocked as "prompt injection" (the model set `contains_instructions` for a printed subtitle), a second ticket had its convenience-fee and IGST rows swapped (a rate warning), a handwritten "Rs 260/-" was read as 2601 (2,341 rupees on a claim, no check tripped), and the model's own hedge ("total is hard to read") on a cab receipt whose total really was edited turned the high finding into a quiet warning. Separately, client dinners came out at 0.60 category confidence, so every one raised an extra question, and an ordinary meal on the trip was filed as client entertainment.
- **Decision:** (1) **Second opinion.** A first read is re-read by the stronger `extraction_retry` model (Sonnet 5.5, low effort) when it says the document talks to an AI, when it is unsure of the total, or when its own arithmetic or GST figures do not hold together. The stronger read replaces the first; where both reads agree on the disputed figures the hedge is dropped, so a mismatch that survives is real and keeps its severity (and a forged document is accused with two reads behind it). Every other receipt is read once. (2) **Prompt v3:** `contains_instructions` is defined by what it must catch (a sentence telling an AI to approve, skip checks or ignore instructions) with the ordinary printed notices it must not (terms, "carry a photo ID", "computer generated", disclaimers); tax rows and fee rows are told apart; a trailing `/-` is "only", not a digit. (3) **Calendar-aware System One.** The decision state carries what the employee's calendar shows on the receipt's date, as words without names ("client dinner with 3 guests", or "nothing relevant"); the category question (v3) and the LLM prompt (`decide_v2`) use it. A client dinner that day separates hosting clients from a meal, and an empty calendar pushes a restaurant bill toward ordinary meals. (4) First-pass claims are finalised again after the calendar fills in the attendees (already in ADR-027's code path).
- **Evidence (dev split, 20 receipts, Haiku 5.5, `evals/reports/2026-10-08-bakeoff-dev-v3.md`):** the raw cheap read (`haiku-low`) gets 100% of critical fields and 96.5% of all fields, line-item F1 0.89, **1 of 20 receipts with an arithmetic false alarm** (the trust checks complaining where the document's own figures are consistent), $0.43 per 1,000 receipts; with the second opinion (`haiku-2nd`) 100% critical, **99.0%** of fields, F1 0.90, **0 of 20 false alarms**, 3 of 20 receipts re-read, $1.77 per 1,000, p50 2.4 s and p95 5.7 s. Injection recall is 100% and injection false alarms 0% in both. Caveat: n = 20 and the same configuration moves by about two points between runs, so these are indications; the 80-receipt test split replaces them at M4. On the demo pile the effect is unambiguous: the rail tickets are clean, the edited total, duplicate, injected note and alcohol bill are all caught, the handwritten fare is 260, and the client dinner needs no question.
- **Cost:** about $0.0013 more per receipt on average, only on receipts that earn it. Sonnet-only reading costs about 25 times the cheap read; this costs about 4 times. The bake-off's Pareto pick on field accuracy alone is still the raw cheap read; the second opinion is chosen for precision of the trust verdicts, which that metric does not measure.
- **Consequences:** `ReceiptExtractor(second_opinion=True)` is the default; `escalate=True` (re-read any unsure critical field) remains an opt-in. Recordings and the extraction cache are keyed by prompt version, so v3 and `decide_v2` required re-recording the demo.
