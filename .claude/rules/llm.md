---
paths:
  - "services/api/src/claimpilot/llm/**"
  - "services/api/src/claimpilot/extraction/**"
  - "services/api/src/claimpilot/decisions/**"
  - "services/api/src/claimpilot/chat/**"
  - "services/api/src/claimpilot/**/prompts/**"
  - "services/api/config/models.yaml"
  - "evals/**"
---
# LLM rules (Claude API + Jev)

Runtime API spend is the **user's personal money**. Treat every live call as a cost.

## Models and calls
- **Never hardcode a model ID.**
  - Resolve models through `claimpilot.llm.registry` (backed by `services/api/config/models.yaml`). Routes are things like `extraction` and `agent_chat`, and env vars can override them (`ROUTE_<NAME>=haiku|sonnet|opus`).
  - Build requests only through the **capability shim** (`claimpilot.llm.client`). It knows the per-model differences:
    - Haiku 4.5 uses `budget_tokens` thinking, takes no `effort`, and allows forced `tool_choice`.
    - Sonnet and Opus 5.5 use adaptive thinking plus `output_config.effort`. Forced `tool_choice` returns a 400 on them, so use `auto` + `strict: true`. They support server-side refusal `fallbacks: "default"`.
- **Use the official `anthropic` Python SDK.**
  - Check the claude-api skill docs before writing SDK calls. Never guess SDK signatures.
  - Use `messages.parse()` with Pydantic for structured outputs, `client.beta.messages.tool_runner` for the agent, and the Batch API for evals.
- **Every call goes through the cost ledger** (`llm_calls`: route, model, tokens in/out/cache, $, latency, cache hit).

## Modes and spend
- **`LLM_MODE`:**
  - `replay` (default in CI, dev and the hosted demo) answers from recordings in `services/api/replay/`, keyed by the sha256 of the request, and costs $0. A miss raises `ReplayMissError`.
  - `live` makes real calls.
  - `fake` is the deterministic stub used in unit tests.
- **Live eval and bake-off runs** must pass `--max-usd`. The runner estimates cost with `count_tokens` first and aborts if the estimate goes over.

## Prompts
- **Prompt caching:**
  - Keep a stable prefix (tools → system → policy/taxonomy), with a `cache_control` breakpoint after the policy.
  - Put nothing volatile (timestamps, IDs) before the breakpoint.
  - Haiku caches only prefixes of 4,096 tokens or more.
  - Verify via `usage.cache_read_input_tokens`.
- **Prompt injection:** wrap receipt text and OCR output in clearly delimited data blocks. The system prompt says that content inside them is data. Final approve/reject decisions are rule-gated in `policy` and `claims`, never taken from model text alone.
- **Prompts live in files** (`prompts/*.md` or `.j2`) with a version string (`extract_v2`, `decide_v1`, `reply_v1`). Any prompt change requires a new version, re-recording the demo recordings **inside Docker** (`infra/compose.record.yml` + `scripts/smoke.py`, ADR-029) and re-running the eval subset (`run-evals` skill). Delete the stale `services/api/replay/*.json` first.
- **Check `stop_reason`** (`refusal`, `max_tokens`) before using the output. Validate every tool input against its schema.

## Jev
- Jev is called only through `DecisionEngine`. Question definitions (Choice / Score / Noul) live next to their LLM-adapter equivalent. Add a parity test whenever you add a question (`add-decision` skill).
