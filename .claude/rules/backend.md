---
paths:
  - "services/**/*.py"
  - "services/**/pyproject.toml"
---
# Backend rules (Python / FastAPI)

- **Python 3.13, managed by uv.** Add dependencies with `uv add <pkg>` (dev: `uv add --dev`). Never use pip directly.
- **Layout:** `services/api/src/claimpilot/<module>/`. Respect the module boundaries in `docs/ARCHITECTURE.md`. Import across modules only through each module's public `__init__` and service functions.
- **Pydantic v2 models are the source of truth** for API schemas, LLM structured outputs and Jev question schemas. Shared domain models go in `claimpilot/domain/`.
- **Async everywhere** on I/O paths: FastAPI handlers, DB (SQLAlchemy 2 async + asyncpg), Redis, httpx. No blocking calls in handlers. Offload CPU work (image hashing, OCR) to the worker.
- **Config** goes through `claimpilot.config.Settings` (pydantic-settings, env vars). Never read `os.environ` directly elsewhere.
- **Errors:** raise domain exceptions and map them to HTTP in one exception handler. Return problem+json shaped errors.
- **API conventions:**
  - Versioned under `/v1`. Nouns for resources.
  - Idempotency-Key header on `POST /v1/claims/{id}/submit`.
  - Paginated lists.
  - Liveness on `/healthz`, dependency checks on `/readyz`.
- **DB:** Alembic migrations for every schema change. Never use `create_all` outside tests.
- **Logging:** structlog JSON. Include `request_id` and `claim_id` where known. Never log file contents or keys.
- **Style:** ruff (lint + format) and pyright in standard mode. Type-annotate public functions.
- **After an API change**, follow the `api-contract` skill: regenerate OpenAPI and the TS types, then update tests.
