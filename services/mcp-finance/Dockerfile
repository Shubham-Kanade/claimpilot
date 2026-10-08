# syntax=docker/dockerfile:1
# Mock finance / reimbursement system: an MCP server over streamable HTTP on port 8101. Claims live
# in a SQLite file under /data, so mount a volume there to keep them across restarts.
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
# /app stays root-owned (the service never writes there); only /data must belong to the app user.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev \
 && useradd --create-home --uid 10001 app \
 && mkdir -p /data && chown app /data
# Declared after the chown above, so a new named volume inherits /data's ownership (uid 10001).
VOLUME /data
USER app
ENV PATH="/app/.venv/bin:$PATH" \
    FINANCE_DB=/data/finance.db \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8101
EXPOSE 8101
HEALTHCHECK --interval=15s --timeout=3s --retries=5 --start-period=10s --start-interval=1s \
  CMD python -c "import sys,urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8101/healthz', timeout=2).status != 200)"
CMD ["python", "-m", "claimpilot_mcp_finance"]
