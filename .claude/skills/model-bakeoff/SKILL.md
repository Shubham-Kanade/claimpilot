---
name: model-bakeoff
description: Compare Claude models (Haiku 4.5, Sonnet 5.5, Opus 5.5 at different effort levels, plus a Haiku→Sonnet cascade) on accuracy, latency and $ per receipt for a route, chart the Pareto frontier, and pick the route model. Use when choosing or revisiting the model for any LLM route.
---
# Model bake-off

**Goal:** the best balance of cost, accuracy and speed for each route. Spend is the user's personal money.

## Matrix (default for the `extraction` route)
| Config | Model | Effort / thinking |
|---|---|---|
| haiku | claude-haiku-4-5 | no thinking |
| sonnet-low | claude-sonnet-5-5 | effort low |
| sonnet-med | claude-sonnet-5-5 | effort medium |
| opus-low | claude-opus-5-5 | effort low |
| opus-med | claude-opus-5-5 | effort medium |
| cascade | haiku → sonnet-low | escalate if confidence < τ or validation fails |

Model IDs and prices come **only** from `services/api/config/models.yaml`. Before a run, re-check pricing in the claude-api skill or on the pricing page and update the YAML if it changed.

## Steps
1. **Estimate:** `uv run python -m evals.bakeoff --route extraction --configs all --split dev --estimate-only`
2. **Run the dev split** (20 receipts): `... --mode live --max-usd 2.00`. Accuracy goes through the Batch API; latency comes from `--latency-sample 5` sync calls per config.
3. **Report:** `evals/reports/bakeoff-<route>-<date>.{json,md,png}` containing:
   - a table: accuracy metrics, p50/p95 latency, $ per receipt, $ per 1,000 receipts, cache hit rate
   - a scatter plot: x = $ per receipt, y = critical-field accuracy, bubble = p95 latency, with the Pareto frontier highlighted
4. **Choose:** pick the cheapest config on the frontier that passes the eval gates (see `run-evals`).
   - If two are within 1 point of accuracy, prefer the lower p95.
   - Caches are per model, so a cascade gives up cache reuse. Only choose it if it beats single-model-lower-effort on the frontier.
5. **Apply:** update `routes:` in `models.yaml` and add an ADR to `docs/DECISIONS.md` with the numbers.
6. **Final (M4):** re-run on the test split (100 receipts) with `--max-usd 6`. These numbers go on the "measurable value" slide.
