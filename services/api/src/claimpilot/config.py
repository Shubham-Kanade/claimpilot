"""Application settings: the only place that reads environment variables (12-factor)."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parents[2]  # services/api
# .parent.parent never raises (Path('/').parent == Path('/')): in the Docker image API_ROOT is /app.
REPO_ROOT = API_ROOT.parent.parent


class Settings(BaseSettings):
    # Repo-root .env (where keys live), then a local .env; real env vars override both.
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env", ".env"), extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"

    # LLM (System Two)
    llm_mode: Literal["replay", "live", "fake"] = "replay"
    anthropic_api_key: SecretStr | None = None
    models_config: Path = API_ROOT / "config" / "models.yaml"
    # With llm_mode=live, also write recordings (sha256 of the request) for replay mode.
    llm_record: bool = False
    replay_dir: Path = API_ROOT / "replay"

    # System One
    decision_engine: Literal["jev", "llm", "fake"] = "llm"
    jev_api_key: SecretStr | None = None
    jev_base_url: str = "https://api.typesafe.ai"
    # Cascade: choice/score answers below this confidence are re-asked on the LLM fallback.
    decision_min_confidence: float = 0.7

    # Infra
    database_url: str = "postgresql+asyncpg://claimpilot:claimpilot@localhost:5432/claimpilot"
    redis_url: str = "redis://localhost:6379/0"
    # Local volume until hosting is chosen; an S3-compatible backend plugs in behind the same
    # storage interface (ADR-009).
    upload_dir: Path = API_ROOT / ".data" / "uploads"
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None

    # Mocked enterprise systems (MCP, streamable HTTP). Compose overrides these with service names.
    mcp_finance_url: str = "http://localhost:8101/mcp"
    mcp_corp_url: str = "http://localhost:8102/mcp"

    # Demo personas allowed to act as approver (no real login in the demo: ADR-010).
    approver_ids: list[str] = ["DEMO-RAVI"]

    # Browser origins allowed to call the API (the web app). Same-origin hosting needs none.
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # Pins the 'current date' used by policy rules (late submission); the demo data is from 2026.
    demo_today: date | None = None

    # Guardrails
    daily_user_token_budget: int = 200_000
    max_upload_mb: int = 15
    max_pdf_pages: int = 10
    max_batch_files: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
