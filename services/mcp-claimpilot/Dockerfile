# syntax=docker/dockerfile:1
# ClaimPilot as an MCP server over streamable HTTP on port 8103 (stdio is for local use: see the
# README). It acts as one demo persona on the ClaimPilot REST API at CLAIMPILOT_API_URL and has no
# authentication of its own, so never expose it publicly. It reads no files from the host unless
# CLAIMPILOT_UPLOAD_ROOT names a folder (mount one read-only).
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/
WORKDIR /app

FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

FROM deps AS runtime
COPY README.md ./
COPY src ./src
# /app stays root-owned: the service keeps no state and never writes there.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev \
 && useradd --create-home --uid 10001 app
USER app
ENV PATH="/app/.venv/bin:$PATH" \
    CLAIMPILOT_API_URL=http://api:8000 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8103
EXPOSE 8103
HEALTHCHECK --interval=15s --timeout=3s --retries=5 --start-period=10s --start-interval=1s \
  CMD python -c "import sys,urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8103/healthz', timeout=2).status != 200)"
CMD ["python", "-m", "claimpilot_mcp", "--transport", "http"]
