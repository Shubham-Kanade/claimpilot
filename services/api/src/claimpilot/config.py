"""Application settings: the only place that reads environment variables (12-factor)."""

from __future__ import annotations

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

    # Infra
    database_url: str = "postgresql+asyncpg://claimpilot:claimpilot@localhost:5432/claimpilot"
    redis_url: str = "redis://localhost:6379/0"
    # Local volume until hosting is chosen; an S3-compatible backend plugs in behind the same
    # storage interface (ADR-009).
    upload_dir: Path = API_ROOT / ".data" / "uploads"
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None

    # Guardrails
    daily_user_token_budget: int = 200_000
    max_upload_mb: int = 15
    max_pdf_pages: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
