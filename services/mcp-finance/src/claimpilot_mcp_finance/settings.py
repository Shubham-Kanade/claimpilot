"""Runtime configuration, read from environment variables (12-factor)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # SQLite file holding all claims; ":memory:" (the default) forgets them on restart.
    finance_db: str = ":memory:"
    # Bind address. Loopback by default; the Docker image sets MCP_HOST=0.0.0.0.
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8101
    log_level: str = "info"
