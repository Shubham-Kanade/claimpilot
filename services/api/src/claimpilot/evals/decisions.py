"""System One benchmark: Jev vs the LLM fallback on identical questions.

    uv run python -m claimpilot.evals.decisions --engines jev,llm --max-usd 0.10

Both engines answer the same ``DOCUMENT_QUESTIONS`` over the committed golden set (the state is
built from ground-truth receipts, so extraction errors do not leak in). We report, per engine:
category accuracy, alcohol accuracy / recall (vs the line items), personal-expense false-positive
rate (no golden receipt is personal), **calibration** (expected calibration error of the
reported confidence), latency and cost. Calibration is the point: a confidence you can trust is
what makes confidence-gated routing (the cascade) safe.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from claimpilot.decisions import DOCUMENT_QUESTIONS, DecisionEngine, document_state
from claimpilot.decisions.questions import PERSONAL
from claimpilot.decisions.types import DecisionError, DecisionResult
from claimpilot.evals.golden import GOLDEN_DIR, GoldenCase, has_alcohol, load_golden
from claimpilot.evals.pareto import p95

ALCOHOL_THRESHOLD = 0.5
GATE_CONFIDENCE = 0.7  # the cascade / pipeline threshold: below it we ask or escalate

# Items nobody could mistake for a business expense (fictional merchants). The golden set is all
# business spend, so these probes are what lets us measure personal-expense *recall*, not just
# false alarms. They live in a data file to keep the module readable.
PERSONAL_PROBES: tuple[dict[str, object], ...] = tuple(
    json.loads((Path(__file__).parent / "personal_probes.json").read_text(encoding="utf-8"))
)
PERSONAL_THRESHOLD = 0.5
CALIBRATION_BINS = 5
# Rough LLM cost guard before any spend: ~1k input and ~150 output tokens per document.
ESTIMATED_LLM_IN, ESTIMATED_LLM_OUT = 1000, 150


@dataclass
class EngineReport:
    engine: str
    documents: int = 0
    failures: int = 0
    category_correct: int = 0
    alcohol_correct: int = 0
    alcohol_positive: int = 0
    alcohol_caught: int = 0
    personal_flagged: int = 0
    probes: int = 0
    probes_caught: int = 0
    category_confidence: list[tuple[float, bool]] = field(default_factory=list)
    latencies_ms: list[int] = field(default_factory=list)
    cost_usd: float = 0.0
    category_errors: list[tuple[str, str, str, float]] = field(default_factory=list)

    @property
    def category_accuracy(self) -> float:
        return self.category_correct / self.documents if self.documents else 0.0

    @property
    def alcohol_accuracy(self) -> float:
        return self.alcohol_correct / self.documents if self.documents else 0.0

    @property
    def alcohol_recall(self) -> float | None:
        return self.alcohol_caught / self.alcohol_positive if self.alcohol_positive else None

    @property
    def personal_false_positive_rate(self) -> float:
        return self.personal_flagged / self.documents if self.documents else 0.0

    @property
    def personal_recall(self) -> float | None:
        return self.probes_caught / self.probes if self.probes else None

    @property
    def coverage(self) -> float:
        """Share of documents decided on its own (category confidence at or above the gate)."""
        c = self.category_confidence
        return sum(conf >= GATE_CONFIDENCE for conf, _ in c) / len(c) if c else 0.0

    @property
    def confident_accuracy(self) -> float:
        """Category accuracy on the documents it decided on its own."""
        sure = [ok for conf, ok in self.category_confidence if conf >= GATE_CONFIDENCE]
        return sum(sure) / len(sure) if sure else 0.0

    @property
    def calibration_error(self) -> float:
        return expected_calibration_error(self.category_confidence)

    @property
    def mean_confidence(self) -> float:
        c = self.category_confidence
        return statistics.mean(x for x, _ in c) if c else 0.0

    @property
    def cost_per_1k(self) -> float:
        return self.cost_usd / self.documents * 1000 if self.documents else 0.0


def expected_calibration_error(pairs: Sequence[tuple[float, bool]], bins: int = 5) -> float:
    """ECE: the weighted gap between stated confidence and actual accuracy, over equal bins."""
    if not pairs:
        return 0.0
    total, error = len(pairs), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        bucket = [(c, ok) for c, ok in pairs if lo <= c < hi or (b == bins - 1 and c == 1.0)]
        if bucket:
            confidence = statistics.mean(c for c, _ in bucket)
            accuracy = sum(ok for _, ok in bucket) / len(bucket)
            error += len(bucket) / total * abs(confidence - accuracy)
    return error


def record(report: EngineReport, case: GoldenCase, result: DecisionResult) -> None:
    truth = case.truth
    category = result["category"]
    ok = category.as_str == truth.category.value
    report.documents += 1
    report.category_correct += ok
    report.category_confidence.append((category.confidence, ok))
    if not ok:
        report.category_errors.append(
            (truth.id, truth.category.value, category.as_str, category.confidence)
        )
    truth_alcohol = has_alcohol(truth)
    said_alcohol = result["alcohol_present"].as_float >= ALCOHOL_THRESHOLD
    report.alcohol_correct += said_alcohol == truth_alcohol
    report.alcohol_positive += truth_alcohol
    report.alcohol_caught += truth_alcohol and said_alcohol
    report.personal_flagged += result["personal_expense"].as_float >= PERSONAL_THRESHOLD
    report.latencies_ms.append(result.latency_ms)
    report.cost_usd += result.cost_usd


class BudgetExceededError(RuntimeError):
    pass


async def evaluate_engine(
    engine: DecisionEngine,
    cases: Sequence[GoldenCase],
    *,
    concurrency: int = 4,
    budget_usd: float | None = None,
    probes: Sequence[dict[str, object]] = PERSONAL_PROBES,
) -> EngineReport:
    report = EngineReport(engine=engine.name)
    gate = asyncio.Semaphore(concurrency)

    async def one(case: GoldenCase) -> None:
        async with gate:
            if budget_usd is not None and report.cost_usd > budget_usd:
                raise BudgetExceededError(f"{engine.name}: spent ${report.cost_usd:.4f}")
            try:
                result = await engine.decide(document_state(case.truth.receipt), DOCUMENT_QUESTIONS)
            except DecisionError:
                report.failures += 1
                return
            record(report, case, result)

    async def probe(state: dict[str, object]) -> None:
        async with gate:
            try:
                result = await engine.decide(state, [PERSONAL])
            except DecisionError:
                return
            report.probes += 1
            report.probes_caught += result["personal_expense"].as_float >= PERSONAL_THRESHOLD
            report.cost_usd += result.cost_usd

    await asyncio.gather(*(one(c) for c in cases), *(probe(p) for p in probes))
    return report


def render_markdown(reports: Sequence[EngineReport], meta: dict[str, object]) -> str:
    lines = [
        f"# System One benchmark: {meta['label']}",
        "",
        f"- Date: {meta['date']} · documents: {meta['documents']} (golden set, ground-truth "
        "receipts as input) + 8 personal-expense probes · questions: category (choice, 14 "
        "options), alcohol_present (noul), personal_expense (noul) · 'confident' = category "
        f"confidence ≥ {GATE_CONFIDENCE}; the rest are asked of the employee",
        "",
        "| Engine | Category acc | Acc when confident (coverage) | Alcohol acc | Alcohol recall"
        " | Personal FP | Personal recall | Calibration error (ECE) | p50 ms | p95 ms"
        " | $/1k docs | Failures |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        recall = "n/a" if r.alcohol_recall is None else f"{r.alcohol_recall:.0%}"
        personal = "n/a" if r.personal_recall is None else f"{r.personal_recall:.0%}"
        p50 = statistics.median(r.latencies_ms) if r.latencies_ms else 0
        p95_ms = p95(r.latencies_ms) if r.latencies_ms else 0
        lines.append(
            f"| {r.engine} | {r.category_accuracy:.1%}"
            f" | {r.confident_accuracy:.1%} ({r.coverage:.0%}) | {r.alcohol_accuracy:.1%}"
            f" | {recall} | {r.personal_false_positive_rate:.1%} | {personal}"
            f" | {r.calibration_error:.3f} | {p50:.0f} | {p95_ms:.0f}"
            f" | ${r.cost_per_1k:.3f} | {r.failures} |"
        )
    lines.append("")
    for r in reports:
        if r.category_errors:
            shown = [f"{i}: {t}→{p} ({c:.2f})" for i, t, p, c in r.category_errors[:8]]
            lines.append(f"- `{r.engine}` category errors ({len(r.category_errors)}): {shown}")
    return "\n".join(lines) + "\n"


def write_report(out: Path, reports: Sequence[EngineReport], meta: dict[str, object]) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{meta['date']}-{meta['label']}.md"
    path.write_text(render_markdown(reports, meta), encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI wiring
    parser = argparse.ArgumentParser(description="Jev vs LLM decision benchmark")
    parser.add_argument("--engines", default="jev,llm")
    parser.add_argument("--golden", default=str(GOLDEN_DIR))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-usd", type=float, required=True)
    parser.add_argument("--label", default="jev-vs-llm")
    parser.add_argument("--out", default="../../evals/reports")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args))


async def _main(args: argparse.Namespace) -> int:  # pragma: no cover - CLI wiring
    from claimpilot.config import Settings
    from claimpilot.decisions import JevEngine, LLMEngine
    from claimpilot.llm.anthropic_llm import AnthropicLLM
    from claimpilot.llm.registry import get_registry

    settings = Settings()
    cases = load_golden(Path(args.golden))[: args.limit]
    names = args.engines.split(",")
    registry = get_registry()
    estimate = 0.0
    if "llm" in names:
        model = registry.resolve("decision_fallback").model.key
        estimate = len(cases) * registry.estimate_cost(
            model, input_tokens=ESTIMATED_LLM_IN, output_tokens=ESTIMATED_LLM_OUT
        )
    print(f"{len(cases)} documents; estimated LLM spend ${estimate:.4f} (cap ${args.max_usd:.2f})")
    if estimate > args.max_usd:
        print("refusing to run: estimate exceeds --max-usd", file=sys.stderr)
        return 3

    reports = []
    if "jev" in names:
        jev = JevEngine.from_settings(settings)
        reports.append(await evaluate_engine(jev, cases))
        await jev.aclose()
    if "llm" in names:
        llm = LLMEngine(AnthropicLLM.from_settings(settings, registry))
        reports.append(await evaluate_engine(llm, cases, budget_usd=args.max_usd))
    meta = {
        "label": args.label,
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "documents": len(cases),
    }
    path = write_report(Path(args.out), reports, meta)
    print(path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
