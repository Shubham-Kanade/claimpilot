"""Live smoke test: proves each registry model accepts the shim's request shape.

Costs real money (user's personal API key): about $0.01 for the whole file.
Run deliberately with: LLM_MODE=live uv run pytest -m live tests/live -s
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from claimpilot.config import Settings
from claimpilot.llm.anthropic_llm import AnthropicLLM
from claimpilot.llm.registry import ModelRegistry
from claimpilot.llm.types import ThinkingMode

pytestmark = pytest.mark.live

SYSTEM = "You read short receipt snippets and return the grand total as a number."
TEXT = "CHAI POINT EXPRESS\nMasala Dosa 180.00\nCGST 2.5% 4.50\nSGST 2.5% 4.50\nTOTAL Rs 189.00"


class Total(BaseModel):
    total: float = Field(description="Grand total payable")


@pytest.mark.parametrize(
    ("model_key", "thinking"),
    [("haiku", "off"), ("haiku", "auto"), ("haiku45", "off"), ("sonnet", "off"), ("opus", "off")],
)
async def test_model_accepts_shim_request(
    model_key: str,
    thinking: ThinkingMode,
    models_registry: ModelRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROUTE_EXTRACTION", model_key)
    llm = AnthropicLLM.from_settings(Settings(), models_registry)
    result = await llm.parse(
        "extraction",
        system=SYSTEM,
        content=[{"type": "text", "text": TEXT}],
        output_model=Total,
        thinking=thinking,
    )
    print(
        f"\n{model_key:8} thinking={thinking:4} effort={result.effort} "
        f"in={result.usage.input_tokens} out={result.usage.output_tokens} "
        f"${result.cost_usd:.5f} {result.latency_ms}ms stop={result.stop_reason}"
    )
    assert result.model_key == model_key
    assert result.parsed.total == pytest.approx(189.0)
