"""Extraction eval / model bake-off runner.

    uv run python -m claimpilot.evals.run --manifest ../../data/synth/out/manifest.jsonl \\
        --split dev --configs haiku-low,haiku45,sonnet-low,opus-low,cascade --max-usd 1.00

Spend is the user's personal money (ADR-005), so the runner:
* estimates cost first (free ``count_tokens`` on a sample, plus an assumed output size) and refuses
  to start when the estimate exceeds ``--max-usd``; ``--estimate-only`` stops there;
* also stops mid-run once actual spend crosses ``--max-usd``.
Calls are synchronous (not the Batch API) so latency is real; see ADR-013.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from claimpilot.domain import ExtractedReceipt
from claimpilot.evals.dataset import EvalCase, load_cases
from claimpilot.evals.metrics import AggregateScore, ReceiptScore, aggregate, score_receipt
from claimpilot.evals.pareto import ConfigResult, choose, p95, pareto_frontier
from claimpilot.extraction import ReceiptExtractor, prepare_document
from claimpilot.extraction.service import PROMPT_VERSION, USER_INSTRUCTION, system_prompt
from claimpilot.llm.client import LLMClient
from claimpilot.llm.errors import LLMError
from claimpilot.llm.registry import ModelRegistry

ASSUMED_OUTPUT_TOKENS = 700  # a typical ExtractedReceipt JSON; thinking is off for extraction
ESTIMATE_SAMPLE = 3
ESCALATION_ALLOWANCE = 1.3  # cascade: assume ~30% of receipts get a second, stronger read


@dataclass(frozen=True, slots=True)
class RunConfig:
    name: str
    model_key: str
    effort: str | None = None
    escalate: bool = False  # cascade: escalate low-confidence critical fields to extraction_retry

    def env(self) -> dict[str, str]:
        env = {"ROUTE_EXTRACTION": self.model_key}
        if self.effort:
            env["ROUTE_EXTRACTION_EFFORT"] = self.effort
        return env


PRESETS: dict[str, RunConfig] = {
    "haiku-low": RunConfig("haiku-low", "haiku", "low"),
    "haiku-medium": RunConfig("haiku-medium", "haiku", "medium"),
    "haiku45": RunConfig("haiku45", "haiku45"),
    "sonnet-low": RunConfig("sonnet-low", "sonnet", "low"),
    "sonnet-medium": RunConfig("sonnet-medium", "sonnet", "medium"),
    "opus-low": RunConfig("opus-low", "opus", "low"),
    "cascade": RunConfig("cascade", "haiku", "low", escalate=True),
}

# Gates from the run-evals skill; a config must pass all of them to be chosen.
GATES = {"critical_field_accuracy": 0.95, "field_accuracy": 0.90, "json_validity": 1.0}


@dataclass
class ConfigReport:
    config: RunConfig
    score: AggregateScore
    cost_usd: float
    latencies_ms: list[int]
    escalations: int
    failures: dict[str, str] = field(default_factory=dict)  # case id -> error

    @property
    def cost_per_receipt(self) -> float:
        n = len(self.latencies_ms)
        return self.cost_usd / n if n else 0.0

    @property
    def passes_gates(self) -> bool:
        s = self.score
        return (
            s.critical_field_accuracy >= GATES["critical_field_accuracy"]
            and s.field_accuracy >= GATES["field_accuracy"]
            and s.json_validity >= GATES["json_validity"]
            and (s.injection_recall is None or s.injection_recall >= 1.0)
        )

    def as_result(self) -> ConfigResult:
        return ConfigResult(
            name=self.config.name,
            accuracy=self.score.critical_field_accuracy,
            cost_per_receipt=self.cost_per_receipt,
            p95_latency_ms=p95(self.latencies_ms) if self.latencies_ms else 0.0,
            passes_gates=self.passes_gates,
        )


class BudgetExceededError(RuntimeError):
    pass


LLMFactory = Callable[[RunConfig], LLMClient]


async def estimate_cost(
    cases: Sequence[EvalCase],
    configs: Sequence[RunConfig],
    llm_for: LLMFactory,
    registry: ModelRegistry,
) -> dict[str, float]:
    """USD estimate per config: mean input tokens of a sample, times prices, plus assumed output.

    ``count_tokens`` is free, so estimating never spends money.
    """
    sample = [prepare_document(c.read_bytes()) for c in cases[:ESTIMATE_SAMPLE]]
    estimates: dict[str, float] = {}
    for config in configs:
        llm = llm_for(config)
        counts = [
            await llm.count_tokens(
                "extraction",
                system=system_prompt(),
                content=[*doc.blocks, {"type": "text", "text": USER_INSTRUCTION}],
                output_model=ExtractedReceipt,
                thinking="off",
            )
            for doc in sample
        ]
        mean_input = int(statistics.mean(counts)) if counts else 0
        per_receipt = registry.estimate_cost(
            llm.resolve("extraction").model.key,
            input_tokens=mean_input,
            output_tokens=ASSUMED_OUTPUT_TOKENS,
        )
        allowance = ESCALATION_ALLOWANCE if config.escalate else 1.0
        estimates[config.name] = per_receipt * len(cases) * allowance
    return estimates


async def run_config(
    cases: Sequence[EvalCase],
    config: RunConfig,
    llm: LLMClient,
    *,
    budget_left: float,
    concurrency: int = 4,
) -> ConfigReport:
    extractor = ReceiptExtractor(llm, escalate=config.escalate)
    gate = asyncio.Semaphore(concurrency)
    scores: list[ReceiptScore] = []
    latencies: list[int] = []
    failures: dict[str, str] = {}
    spent = 0.0
    escalations = 0

    async def one(case: EvalCase) -> None:
        nonlocal spent, escalations
        async with gate:
            if spent > budget_left:
                raise BudgetExceededError(f"{config.name}: spent ${spent:.4f} > ${budget_left:.4f}")
            try:
                result = await extractor.extract(prepare_document(case.read_bytes()))
            except LLMError as exc:
                failures[case.id] = f"{type(exc).__name__}: {exc}"
                if exc.call is not None:
                    spent += exc.call.cost_usd
                return
            spent += result.cost_usd
            escalations += result.escalated
            latencies.append(sum(c.latency_ms for c in result.calls))
            scores.append(score_receipt(case.id, case.truth.receipt, result.receipt))

    await asyncio.gather(*(one(c) for c in cases))
    return ConfigReport(
        config=config,
        score=aggregate(scores, parse_failures=len(failures)),
        cost_usd=spent,
        latencies_ms=latencies,
        escalations=escalations,
        failures=failures,
    )


def render_markdown(reports: Sequence[ConfigReport], meta: dict[str, object]) -> str:
    results = [r.as_result() for r in reports]
    frontier = {r.name for r in pareto_frontier(results)}
    chosen = choose(results)
    lines = [
        f"# Extraction bake-off: {meta['label']}",
        "",
        f"- Date: {meta['date']} · split: `{meta['split']}` · receipts: {meta['receipts']}"
        f" · prompt: `{meta['prompt']}` · total spend: ${meta['spend']:.4f}",
        "",
        "| Config | Critical acc | Field acc | Line-item F1 | JSON valid | Injection recall"
        " | $/receipt | $/1k | p50 ms | p95 ms | Gates | Pareto |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        s, res = r.score, r.as_result()
        inj = "n/a" if s.injection_recall is None else f"{s.injection_recall:.0%}"
        p50 = statistics.median(r.latencies_ms) if r.latencies_ms else 0
        lines.append(
            f"| {r.config.name} | {s.critical_field_accuracy:.1%} | {s.field_accuracy:.1%}"
            f" | {s.line_item_f1:.2f} | {s.json_validity:.0%} | {inj}"
            f" | ${res.cost_per_receipt:.5f} | ${res.cost_per_1k:.2f} | {p50:.0f}"
            f" | {res.p95_latency_ms:.0f} | {'✅' if r.passes_gates else '❌'}"
            f" | {'★' if res.name in frontier else ''} |"
        )
    lines += [
        "",
        f"**Chosen (cheapest frontier config passing gates):** "
        f"{chosen.name if chosen else 'none passes the gates'}",
        "",
    ]
    for r in reports:
        top = list(r.score.errors_by_field.items())[:5]
        if top or r.failures:
            lines.append(f"- `{r.config.name}` top errors: {top}; failures: {len(r.failures)}")
    return "\n".join(lines) + "\n"


def write_reports(out_dir: Path, label: str, reports: Sequence[ConfigReport], meta: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"{meta['date']}-{label}"
    payload = {
        "meta": meta,
        "configs": [
            {
                "config": asdict(r.config),
                "score": asdict(r.score),
                "cost_usd": r.cost_usd,
                "cost_per_receipt": r.cost_per_receipt,
                "latencies_ms": r.latencies_ms,
                "escalations": r.escalations,
                "passes_gates": r.passes_gates,
                "failures": r.failures,
            }
            for r in reports
        ],
    }
    stem.with_suffix(".json").write_text(json.dumps(payload, indent=2, default=str), "utf-8")
    stem.with_suffix(".md").write_text(render_markdown(reports, meta), "utf-8")
    return stem.with_suffix(".md")


async def main_async(args: argparse.Namespace, llm_for: LLMFactory, registry: ModelRegistry) -> int:
    configs = [PRESETS[name] for name in args.configs.split(",")]
    cases = load_cases(Path(args.manifest), split=args.split, limit=args.limit)
    if not cases:
        print("no cases found", file=sys.stderr)
        return 2

    estimates = await estimate_cost(cases, configs, llm_for, registry)
    total = sum(estimates.values())
    for name, usd in estimates.items():
        print(f"estimate {name:14} ${usd:.4f}")
    print(f"estimate TOTAL         ${total:.4f} (budget ${args.max_usd:.2f}, {len(cases)} cases)")
    if args.estimate_only:
        return 0
    if total > args.max_usd:
        print("refusing to run: estimate exceeds --max-usd", file=sys.stderr)
        return 3

    reports: list[ConfigReport] = []
    budget_left = args.max_usd
    for config in configs:
        report = await run_config(cases, config, llm_for(config), budget_left=budget_left)
        budget_left -= report.cost_usd
        reports.append(report)
        print(
            f"done {config.name:14} ${report.cost_usd:.4f} "
            f"critical={report.score.critical_field_accuracy:.1%}"
        )

    meta = {
        "label": args.label,
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "split": args.split,
        "receipts": len(cases),
        "prompt": PROMPT_VERSION,
        "spend": sum(r.cost_usd for r in reports),
    }
    path = write_reports(Path(args.out), args.label, reports, meta)
    print(f"report: {path}")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ClaimPilot extraction eval / model bake-off")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--split", default="dev")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--configs", default="haiku-low")
    parser.add_argument("--max-usd", type=float, required=True)
    parser.add_argument("--estimate-only", action="store_true")
    parser.add_argument("--label", default="bakeoff-extraction")
    parser.add_argument("--out", default="../../evals/reports")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI wiring
    from claimpilot.config import Settings
    from claimpilot.llm.anthropic_llm import AnthropicLLM
    from claimpilot.llm.registry import get_registry

    args = parse_args(argv)
    settings = Settings()

    def llm_for(config: RunConfig) -> LLMClient:
        return AnthropicLLM.from_settings(settings, get_registry(), env=config.env())

    return asyncio.run(main_async(args, llm_for, get_registry()))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
