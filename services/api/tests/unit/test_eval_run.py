from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from claimpilot.domain import DocType, ExpenseCategory, ExtractedReceipt, ReceiptTruth
from claimpilot.evals.dataset import load_cases
from claimpilot.evals.run import (
    PRESETS,
    BudgetExceededError,
    RunConfig,
    estimate_cost,
    main_async,
    parse_args,
    render_markdown,
    run_config,
)
from claimpilot.llm.fake import FakeLLM
from claimpilot.llm.registry import ModelRegistry
from claimpilot.llm.types import Completion

TRUTH = ExtractedReceipt(
    doc_type=DocType.cab_receipt, merchant_name="Zip Cabs", date="2026-10-01", total=320.0
)


def _png(seed: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40 + seed, 60), "white").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    rows = []
    for i in range(3):
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "truth").mkdir(exist_ok=True)
        (tmp_path / f"docs/r{i}.png").write_bytes(_png(i))
        truth = ReceiptTruth(id=f"r{i}", receipt=TRUTH, category=ExpenseCategory.local_conveyance)
        (tmp_path / f"truth/r{i}.json").write_text(truth.model_dump_json(), "utf-8")
        rows.append(
            {
                "id": f"r{i}",
                "path": f"docs/r{i}.png",
                "truth_path": f"truth/r{i}.json",
                "doc_type": "cab_receipt",
                "tags": ["clean"],
                "split": "dev" if i < 2 else "test",
            }
        )
    path = tmp_path / "manifest.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n", "utf-8")
    return path


def factory(registry: ModelRegistry, response: ExtractedReceipt | Completion = TRUTH):
    def llm_for(config: RunConfig) -> FakeLLM:
        llm = FakeLLM(registry, env=config.env())
        llm.register(ExtractedReceipt, response)
        return llm

    return llm_for


def test_load_cases_filters_split_and_limit(manifest):
    assert [c.id for c in load_cases(manifest, split="dev")] == ["r0", "r1"]
    assert len(load_cases(manifest)) == 3
    assert len(load_cases(manifest, limit=1)) == 1
    assert load_cases(manifest, split="dev")[0].read_bytes().startswith(b"\x89PNG")


def test_preset_envs():
    assert PRESETS["haiku45"].env() == {"ROUTE_EXTRACTION": "haiku45"}
    assert PRESETS["sonnet-low"].env() == {
        "ROUTE_EXTRACTION": "sonnet",
        "ROUTE_EXTRACTION_EFFORT": "low",
    }
    assert PRESETS["cascade"].escalate


async def test_estimate_scales_with_price_and_cascade(manifest, models_registry):
    cases = load_cases(manifest)
    configs = [PRESETS["haiku-low"], PRESETS["opus-low"], PRESETS["cascade"]]
    est = await estimate_cost(cases, configs, factory(models_registry), models_registry)
    assert est["opus-low"] > est["haiku-low"] * 10
    assert est["cascade"] == pytest.approx(est["haiku-low"] * 1.3)


async def test_run_config_scores_and_accounts(manifest, models_registry):
    cases = load_cases(manifest, split="dev")
    config = PRESETS["haiku-low"]
    report = await run_config(cases, config, factory(models_registry)(config), budget_left=1.0)
    assert report.score.critical_field_accuracy == 1.0
    assert report.cost_usd > 0 and len(report.latencies_ms) == 2
    assert report.passes_gates
    assert report.as_result().name == "haiku-low"


async def test_run_config_counts_refusals_as_failures(manifest, models_registry):
    refusal = Completion(text=None, stop_reason="refusal", model_id="claude-haiku-5-5")
    config = PRESETS["haiku-low"]
    llm = factory(models_registry, response=refusal)(config)
    report = await run_config(load_cases(manifest), config, llm, budget_left=1.0)
    assert len(report.failures) == 3
    assert report.score.json_validity == 0.0
    assert not report.passes_gates


async def test_run_config_stops_at_budget(manifest, models_registry):
    config = PRESETS["opus-low"]
    llm = factory(models_registry)(config)
    with pytest.raises(BudgetExceededError):
        await run_config(load_cases(manifest), config, llm, budget_left=0.0, concurrency=1)


async def test_main_estimate_only_and_budget_refusal(manifest, models_registry, capsys):
    args = parse_args(["--manifest", str(manifest), "--max-usd", "1", "--estimate-only"])
    assert await main_async(args, factory(models_registry), models_registry) == 0
    assert "estimate TOTAL" in capsys.readouterr().out

    args = parse_args(["--manifest", str(manifest), "--max-usd", "0.0000001"])
    assert await main_async(args, factory(models_registry), models_registry) == 3


async def test_main_writes_reports(manifest, models_registry, tmp_path):
    out = tmp_path / "reports"
    args = parse_args(
        [
            "--manifest", str(manifest), "--max-usd", "1", "--out", str(out),
            "--configs", "haiku-low,sonnet-low", "--label", "t",
        ]
    )  # fmt: skip
    assert await main_async(args, factory(models_registry), models_registry) == 0
    md = next(out.glob("*-t.md")).read_text("utf-8")
    data = json.loads(next(out.glob("*-t.json")).read_text("utf-8"))
    assert "| haiku-low |" in md and "Chosen" in md
    assert [c["config"]["name"] for c in data["configs"]] == ["haiku-low", "sonnet-low"]
    assert data["meta"]["prompt"] == "extract_v1"


async def test_main_no_cases(tmp_path, models_registry):
    empty = tmp_path / "m.jsonl"
    empty.write_text("", "utf-8")
    args = parse_args(["--manifest", str(empty), "--max-usd", "1"])
    assert await main_async(args, factory(models_registry), models_registry) == 2


def test_render_markdown_with_no_passing_config(manifest, models_registry):
    assert "none passes the gates" in render_markdown([], {
        "label": "x", "date": "d", "split": "dev", "receipts": 0, "prompt": "p", "spend": 0.0,
    })  # fmt: skip
