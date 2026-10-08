"""Configuration from the environment and the process entrypoint."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import uvicorn
from starlette.applications import Starlette

from claimpilot_mcp_corp import __main__ as entrypoint
from claimpilot_mcp_corp.settings import REPO_POLICY, SERVICE_ROOT, Settings

ENV_VARS = ("SEED_DIR", "POLICY_PATH", "MCP_HOST", "MCP_PORT", "LOG_LEVEL")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_point_at_the_service_seed_and_the_monorepo_policy():
    settings = Settings()
    assert settings.seed_dir == SERVICE_ROOT / "seed"
    assert (settings.seed_dir / "employees.json").is_file()
    assert settings.policy_path is None
    assert settings.policy_candidates() == (SERVICE_ROOT / "seed" / "policy.yaml", REPO_POLICY)
    assert REPO_POLICY.parts[-3:] == ("api", "config", "policy.yaml")
    assert (settings.mcp_host, settings.mcp_port, settings.log_level) == ("127.0.0.1", 8102, "info")


def test_an_explicit_policy_path_has_no_fallbacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("POLICY_PATH", str(tmp_path / "policy.yaml"))
    assert Settings().policy_candidates() == (tmp_path / "policy.yaml",)


def test_environment_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEED_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "9999")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    settings = Settings()
    assert settings.seed_dir == tmp_path
    assert settings.policy_candidates() == (tmp_path / "policy.yaml", REPO_POLICY)
    assert (settings.mcp_host, settings.mcp_port, settings.log_level) == (
        "0.0.0.0",
        9999,
        "WARNING",
    )


def test_main_serves_the_app_with_the_configured_seed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEED_DIR", str(SERVICE_ROOT / "seed"))
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


def test_main_fails_fast_on_a_broken_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEED_DIR", str(tmp_path))  # no seed files in there
    with pytest.raises(FileNotFoundError, match="seed file not found"):
        entrypoint.main()
