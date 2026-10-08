"""Configuration from the environment and the process entrypoint."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import uvicorn
from starlette.applications import Starlette

from claimpilot_mcp_finance import __main__ as entrypoint
from claimpilot_mcp_finance.settings import Settings

ENV_VARS = ("FINANCE_DB", "MCP_HOST", "MCP_PORT", "LOG_LEVEL")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_local_and_in_memory():
    settings = Settings()
    assert settings.finance_db == ":memory:"
    assert settings.mcp_host == "127.0.0.1"
    assert settings.mcp_port == 8101
    assert settings.log_level == "info"


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FINANCE_DB", "/data/finance.db")
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "9999")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    settings = Settings()
    assert settings.finance_db == "/data/finance.db"
    assert settings.mcp_host == "0.0.0.0"
    assert settings.mcp_port == 9999
    assert settings.log_level == "WARNING"


def test_main_serves_the_app_with_the_configured_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    db = tmp_path / "finance.db"
    monkeypatch.setenv("FINANCE_DB", str(db))
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "8123")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    captured: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    entrypoint.main()

    assert isinstance(captured["app"], Starlette)
    assert (captured["host"], captured["port"], captured["log_level"]) == (
        "0.0.0.0",
        8123,
        "warning",
    )
    assert db.exists()  # the store opened the configured file
