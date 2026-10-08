---
name: api-contract
description: Change or add a ClaimPilot API endpoint or schema end-to-end (Pydantic model → FastAPI route → OpenAPI → generated TypeScript types → tests). Use whenever request/response shapes, endpoints or SSE event types change.
---
# API contract change

Contract-first: the Pydantic models and OpenAPI are what the backend and frontend agents agree on.

1. **Model:** edit or add Pydantic models in `services/api/src/claimpilot/domain/` (shared) or `<module>/schemas.py`. Use explicit `Field(description=...)`, since descriptions flow into OpenAPI and the LLM schemas.
2. **Route:**
   - Add or edit it in `<module>/router.py` under `/v1` with `response_model`.
   - Write the error cases as problem+json.
   - For SSE events, add the event model to `domain/events.py` (a discriminated union on `type`).
3. **Export OpenAPI:** `cd services/api && uv run python -m claimpilot.export_openapi > ../../apps/web/openapi.json`
4. **Generate TS types:** `cd apps/web && npm run gen:api`, which writes `src/lib/api/schema.d.ts`. Never edit that file by hand.
5. **Tests:**
   - API test in `services/api/tests/api/` (happy path, validation error, auth).
   - Update MSW handlers in `apps/web/src/test/handlers.ts`.
   - Run `npx tsc --noEmit` to catch frontend breakage.
6. **CI drift check:** CI regenerates both files and fails on any diff. Commit `openapi.json` and `schema.d.ts` together with the change.
