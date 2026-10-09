"""Runtime configuration, read from environment variables (12-factor)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# An employee id as the demo directory writes them (DEMO-ASHA, P001): never a place for a header
# injection, whatever ends up in the environment.
PERSONA_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,63}")


class Settings(BaseSettings):
    # An empty variable counts as unset (an empty CLAIMPILOT_UPLOAD_ROOT must not mean "here").
    # Validation messages never echo the offending value: the persona is sent as a header.
    model_config = SettingsConfigDict(
        extra="ignore", env_ignore_empty=True, hide_input_in_errors=True
    )

    # The ClaimPilot REST API this server fronts (compose: http://api:8000; the hosted demo serves
    # it under /api).
    claimpilot_api_url: str = "http://localhost:8000"
    # Who the server acts as, sent as X-Persona. The demo has no login: personas are synthetic
    # employees and DEMO-RAVI is the approver.
    claimpilot_persona: str = "DEMO-ASHA"
    claimpilot_timeout_s: float = Field(default=30.0, gt=0)
    claimpilot_upload_timeout_s: float = Field(default=120.0, gt=0)
    # Upload limits, used only when GET /v1/meta cannot be read or does not publish them (it
    # normally does). The API enforces its limits either way; checking first saves sending files
    # it would refuse. The defaults are the API's own.
    claimpilot_max_files: int = Field(default=30, ge=1)
    claimpilot_max_file_mb: int = Field(default=15, ge=1)
    # The only folder upload_receipts may read from. Unset: any absolute path over stdio (your own
    # machine) and no files at all over HTTP (the server is not your machine).
    claimpilot_upload_root: Path | None = None
    # Bind address and port of the HTTP transport. Loopback by default; the Docker image sets
    # MCP_HOST=0.0.0.0.
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8103, ge=1, le=65535)
    log_level: str = "info"

    @field_validator("claimpilot_api_url")
    @classmethod
    def _api_url(cls, value: str) -> str:
        url = value.strip()
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.query:
            raise ValueError("CLAIMPILOT_API_URL must be an http(s) URL like http://localhost:8000")
        return url.rstrip("/")

    @field_validator("claimpilot_persona")
    @classmethod
    def _persona(cls, value: str) -> str:
        persona = value.strip()
        if not PERSONA_PATTERN.fullmatch(persona):
            raise ValueError("CLAIMPILOT_PERSONA must be an employee id such as DEMO-ASHA")
        return persona

    @field_validator("claimpilot_upload_root")
    @classmethod
    def _upload_root(cls, value: Path | None) -> Path | None:
        return value.expanduser().resolve() if value else None
