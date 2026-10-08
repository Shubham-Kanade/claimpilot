---
name: add-decision
description: Add a new System One decision (a Jev Choice / Score / Noul question) together with its LLM-adapter equivalent and a parity test. Use when ClaimPilot needs a new fast typed judgment, e.g. a category, policy flag, risk level or routing.
---
# Add a System One decision

Background:
- Jev (TypeSafe AI) is text/JSON-in, typed-answer-out, with a probability distribution and confidence. Endpoint: `POST /v1/systemone`, model `jev-latest`. Context is 64k tokens (32k for the state plus the longest question).
- Primitives:
  - **Choice:** one of up to 255 unordered options
  - **Score:** an ordered scale of 2–10 levels
  - **Noul:** the probability that a statement is true
- Access is waitlisted, so every question also needs an `LLMEngine` implementation.

## Steps
1. **Define the question** in `services/api/src/claimpilot/decisions/questions.py` as a `Question` model with:
   - `key` (e.g. `expense_category`)
   - `kind` (`choice` | `score` | `noul`)
   - `prompt` (the question text)
   - `options` for choice, or `levels` for score
   - `version`
   Keep the wording identical for both engines.
2. **State builder:** a function that turns domain objects into the compact JSON state Jev evaluates. Include only the fields the question needs (privacy and tokens).
3. **LLM adapter:** `LLMEngine` maps the question to a structured-output schema (an enum for choice, an int range for score, a float 0–1 for noul, plus a `confidence` field). It goes through the capability shim on the `decision_fallback` route.
4. **Thresholds:** set the `min_confidence` used to route answers (auto vs. ask the user vs. escalate) in the question definition.
5. **Tests:**
   - unit test with `FakeEngine`
   - **parity test** (`tests/replay/test_decision_parity.py`): both engines on the same fixture states, asserting agreement ≥ X% and recording latency and cost
6. **Cache key:** `(sha256(state), question.key, question.version, engine)` in Redis. Bump `version` when the wording changes.
