---
name: run-evals
description: Run the ClaimPilot eval suite (extraction accuracy, categorisation, duplicate/fraud/injection recall, question quality) against the golden set, compare with the baseline, and update the report. Use after any prompt, model-route, extraction or decision-logic change. Costs real money in live mode.
---
# Run evals

**Live mode spends the user's personal API money. Always pass `--max-usd` and start with the dev split.**

## Steps
1. **Check preconditions:**
   - `data/synth/out/manifest.jsonl` exists (otherwise run the `synthetic-receipts` skill).
   - `ANTHROPIC_API_KEY` is set in `.env` for live runs.
2. **Estimate the cost** (no spend):
   `uv run python -m evals.run --split dev --routes current --estimate-only`
3. **Run the dev split:**
   `uv run python -m evals.run --split dev --mode live --max-usd 1.00`
   - Accuracy runs go through the Batch API (50% off) by default. `--sync` measures latency on a small sample.
   - Results are cached by (input hash, prompt version, model, effort), so reruns are free.
4. **Compare:** `uv run python -m evals.compare --baseline evals/reports/baseline.json --current evals/reports/latest.json`
5. **Gates** (fail on regression beyond tolerance):
   - critical-field accuracy (amount, date, GSTIN, merchant): at least 95%
   - field accuracy: at least 90%
   - category accuracy: at least 90%
   - duplicate recall: at least 95%
   - **injection auto-approve: 0**
   - JSON validity: 100%
6. **Test split:** run only for final numbers (M4) or before changing the baseline: `--split test --max-usd 5`.
7. **Record:**
   - Commit `evals/reports/<date>-<label>.json` and `.md`.
   - Update `baseline.json` only when the user approves.
   - Note notable changes in TASKS.md (done log).

## Metrics are defined in `evals/metrics.py`
- Normalisation: amounts rounded to 2 decimal places, dates as ISO, GSTIN upper-cased, merchant compared by fuzzy ratio of at least 90.
- Question quality: LLM-judge rubric (necessary? combined? answerable?) on the `eval_judge` route.
