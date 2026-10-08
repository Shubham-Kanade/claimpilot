# syntax=docker/dockerfile:1
# ClaimPilot hosted demo: ONE container for a Hugging Face Space (Docker SDK, port 7860).
#
#   Caddy :7860 --/api/*--> FastAPI :8000 (runs every batch in-process: RUNTIME=embedded)
#               \--others-> Next.js :3000
#   FastAPI --MCP--> mock finance :8101, mock corporate systems :8102 (loopback only)
#
# SQLite instead of Postgres, an in-memory queue and event bus instead of Redis, and LLM answers
# replayed from recordings (LLM_MODE=replay): nothing here costs money to run, and a restart gives
# a fresh demo. See docs/DECISIONS.md ADR-030.
#
# On the Space, the Space repo holds only this file and README.md and the build clones the public
# GitHub repo (SOURCE=fetch-git, REF=main). From a checkout, build with the local files:
#   docker build -f deploy/hf-space/Dockerfile --build-arg SOURCE=fetch-local -t claimpilot-demo .
#   docker run --rm -p 7860:7860 claimpilot-demo        # http://localhost:7860

ARG SOURCE=fetch-git

FROM alpine/git AS fetch-git
ARG REPO=https://github.com/Shubham-Kanade/claimpilot.git
ARG REF=main
RUN git clone --depth 1 --branch "$REF" "$REPO" /src

FROM scratch AS fetch-local
COPY . /src

FROM ${SOURCE} AS source

# ---- web: a standalone Next.js build whose API calls go to the same origin under /api -----------
FROM node:22-slim AS web-build
WORKDIR /web
COPY --from=source /src/apps/web/package.json /src/apps/web/package-lock.json ./
RUN npm ci
COPY --from=source /src/apps/web ./
ENV NEXT_PUBLIC_API_BASE_URL=/api \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---- everything else on one slim Python image ---------------------------------------------------
FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=caddy:2-alpine /usr/bin/caddy /usr/local/bin/caddy
WORKDIR /app

# Each Python service keeps its own virtualenv and lock file, exactly as in the Compose stack.
COPY --from=source /src/services/api /app/api
RUN cd /app/api && uv sync --locked --no-dev
COPY --from=source /src/services/mcp-finance /app/mcp-finance
RUN cd /app/mcp-finance && uv sync --locked --no-dev
COPY --from=source /src/services/mcp-corp /app/mcp-corp
COPY --from=source /src/services/api/config/policy.yaml /app/mcp-corp/seed/policy.yaml
RUN cd /app/mcp-corp && uv sync --locked --no-dev

COPY --from=web-build /web/.next/standalone /app/web
COPY --from=web-build /web/.next/static /app/web/.next/static
COPY --from=web-build /web/public /app/web/public

COPY --from=source /src/deploy/hf-space/Caddyfile /src/deploy/hf-space/start.py /app/

# Spaces run the container as user 1000; /data is the only place that is written to.
RUN useradd --create-home --uid 1000 user \
 && mkdir -p /data/uploads \
 && chown -R user /data /app/web
USER user

ENV RUNTIME=embedded \
    DEMO_MODE=1 \
    DUPLICATE_SCOPE=employee \
    LLM_MODE=replay \
    REPLAY_LATENCY_SCALE=0.6 \
    DECISION_ENGINE=llm \
    DEMO_TODAY=2026-10-12 \
    DATABASE_URL=sqlite+aiosqlite:////data/claimpilot.db \
    UPLOAD_DIR=/data/uploads \
    MCP_FINANCE_URL=http://127.0.0.1:8101/mcp \
    MCP_CORP_URL=http://127.0.0.1:8102/mcp \
    FINANCE_DB=/data/finance.db \
    SEED_DIR=/app/mcp-corp/seed \
    POLICY_PATH=/app/mcp-corp/seed/policy.yaml \
    MAX_UPLOAD_MB=6 \
    MAX_BATCH_FILES=20 \
    PORT=7860
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=60s \
  CMD python -c "import sys,urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:7860/api/healthz', timeout=4).status != 200)"
CMD ["python", "/app/start.py"]
