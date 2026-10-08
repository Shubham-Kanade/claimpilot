# ClaimPilot

**From a pile of receipts to "ready to submit": an AI-native expense & reimbursement agent.**
Built for AI Innovation Lab Season 2. All data is synthetic, and the finance system is mocked.

Drop in photos, PDFs or UPI screenshots. ClaimPilot then:
1. extracts every field (Claude vision, structured outputs)
2. makes fast typed decisions per item: category, policy flags, duplicate likelihood, risk (Jev, a "System One" decision model)
3. checks Indian GST/GSTIN rules and receipt trust: duplicates, tampering, AI-generated images, prompt injection
4. groups items into trip, period, event or allowance claims
5. asks only the questions it must
6. submits to a mocked finance system over MCP once you confirm

> Status: early development. See [docs/TASKS.md](docs/TASKS.md).

## Quick start
```bash
cp .env.example .env                                   # LLM_MODE=replay by default ($0)
docker compose -f infra/compose.yml up --build         # api :8000 · web :3000 · postgres · redis · worker
curl http://localhost:8000/readyz
```

Local development without Docker:
```bash
cd services/api && uv sync && uv run uvicorn claimpilot.main:app --reload
cd apps/web && npm install && npm run dev
```

## Tests
```bash
cd services/api && uv run pytest            # unit + API (never calls paid APIs by default)
cd apps/web && npm test                     # Vitest + RTL + MSW
```

## Docs
- [Architecture](docs/ARCHITECTURE.md)
- [Decisions (ADRs)](docs/DECISIONS.md)
- [Plan](docs/PLAN.md)
- [Tasks](docs/TASKS.md)

## Model cost control
The Claude model for every route (extraction, chat and so on) comes from [services/api/config/models.yaml](services/api/config/models.yaml), and env vars can override it (`ROUTE_EXTRACTION=sonnet`). A bake-off eval picks the cheapest model that meets the accuracy and latency targets.
