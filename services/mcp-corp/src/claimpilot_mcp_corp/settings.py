"""Runtime configuration, read from environment variables (12-factor)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# services/mcp-corp in a checkout; /app in the Docker image (both are editable installs).
SERVICE_ROOT = Path(__file__).resolve().parents[2]
# Monorepo fallback so a local run needs no copy of the policy file.
REPO_POLICY = SERVICE_ROOT.parent / "api" / "config" / "policy.yaml"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Directory with employees.json and calendar.json.
    seed_dir: Path = SERVICE_ROOT / "seed"
    # The policy file. Unset: seed/policy.yaml, then services/api/config/policy.yaml.
    policy_path: Path | None = None
    # Bind address. Loopback by default; the Docker image sets MCP_HOST=0.0.0.0.
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8102
    log_level: str = "info"

    def policy_candidates(self) -> tuple[Path, ...]:
        if self.policy_path is not None:
            return (self.policy_path,)
        return (self.seed_dir / "policy.yaml", REPO_POLICY)
