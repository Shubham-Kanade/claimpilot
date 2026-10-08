# syntax=docker/dockerfile:1
# Mock corporate systems (HR directory, calendars, expense policy): an MCP server over streamable
# HTTP on port 8102. Build with the REPO ROOT as context (infra/compose.yml does): the image bakes in
# the canonical policy file services/api/config/policy.yaml. Dockerfile.dockerignore, next to this
# file, trims that context to an allow-list of what is copied below.
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/
WORKDIR /app

FROM base AS deps
COPY services/mcp-corp/pyproject.toml services/mcp-corp/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

FROM deps AS runtime
COPY services/mcp-corp/README.md ./
COPY services/mcp-corp/src ./src
COPY services/mcp-corp/seed ./seed
# One copy of the policy exists, the API's. A plain COPY, so a missing file fails the build loudly.
COPY services/api/config/policy.yaml ./seed/policy.yaml
# /app stays root-owned and read-only to the app user: this service writes nothing.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev \
 && useradd --create-home --uid 10001 app
USER app
ENV PATH="/app/.venv/bin:$PATH" \
    SEED_DIR=/app/seed \
    POLICY_PATH=/app/seed/policy.yaml \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8102
EXPOSE 8102
HEALTHCHECK --interval=15s --timeout=3s --retries=5 --start-period=10s --start-interval=1s \
  CMD python -c "import sys,urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8102/healthz', timeout=2).status != 200)"
CMD ["python", "-m", "claimpilot_mcp_corp"]
