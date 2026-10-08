---
paths:
  - "services/**/tests/**"
  - "apps/web/**/*.test.ts"
  - "apps/web/**/*.test.tsx"
  - "apps/web/e2e/**"
  - "evals/**"
---
# Testing rules

| Layer | Where | Rule |
|---|---|---|
| Unit | `services/*/tests/unit` | No network. Use the `fake` LLM and Jev engines. Cover policy, GSTIN, GST math, grouping, the claim state machine and prompt builders |
| LLM replay | `services/api/tests/replay` | `pytest-recording` cassettes. Record only with `LLM_MODE=live --record-mode=once` and a scrubbed key. CI always replays |
| Contract | `tests/contract` | Every LLM structured output parses to its Pydantic model. Tool schemas are strict |
| Model switching | `tests/unit/test_llm_shim.py` | Snapshot the request params for each registry model × route |
| Agent trajectory | `tests/agent` | Scripted conversations. Assert the order of tool calls: never `submit_claim` before confirmation; at most one combined question |
| API | `tests/api` | httpx `AsyncClient` against the app. Auth/RBAC, idempotent submit, problem+json errors |
| Frontend unit | `apps/web/src/**/*.test.tsx` | Vitest + RTL + MSW. Test the reducer and components, not implementation details |
| E2E | `apps/web/e2e` | Playwright on the Compose stack with `LLM_MODE=replay`. Include axe checks |
| Evals | `evals/` | Live and costly. Run only through the `run-evals` / `model-bakeoff` skills with `--max-usd`. Never in the default CI job |

- **Never call live APIs from the default `pytest` or `npm test` run.** Live tests are marked `@pytest.mark.live` and skipped unless `LLM_MODE=live`.
- **Every bug fix gets a regression test.** Every adversarial case (injection, tampering, duplicates) found in evals becomes a fixture.
- **Write tests from the contract** (schemas, OpenAPI), not by mirroring the implementation.
- **Coverage gates (enforced in CI; unit tests are a judged rubric item, ADR-010):**
  - api: ≥ 85% overall (`fail_under` in `pyproject.toml`), aiming for ≥ 90% on `policy`, `trust`, `claims`, `llm` and `decisions`
  - web: ≥ 80% lines (`vitest.config.ts` thresholds)
  - Every new module ships with its unit tests in the same change. After each milestone, update the test-count and coverage table in `docs/TECHNICAL_DESIGN.md` §7.
