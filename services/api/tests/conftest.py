from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from claimpilot.config import API_ROOT, Settings
from claimpilot.llm.registry import ModelRegistry, load_registry
from claimpilot.main import create_app

# Never let the default test run spend money.
os.environ.setdefault("LLM_MODE", "fake")
# Hermetic by default: ignore the developer's .env (real keys, docker hostnames). Live tests
# (LLM_MODE=live) still read it so they can find ANTHROPIC_API_KEY.
if os.environ["LLM_MODE"] != "live":
    Settings.model_config["env_file"] = None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("LLM_MODE") == "live":
        return
    skip_live = pytest.mark.skip(reason="live test: set LLM_MODE=live (costs money)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(scope="session")
def models_registry() -> ModelRegistry:
    return load_registry(API_ROOT / "config" / "models.yaml")


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
