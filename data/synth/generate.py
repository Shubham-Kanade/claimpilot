"""ClaimPilot synthetic receipt dataset generator (ground truth first, then render).

Usage (from data/synth):
    uv run generate.py all --count 100 --seed 42
    uv run generate.py all --count 20 --seed 7 --only restaurant_bill,upi_payment
    uv run generate.py scenarios|render|degrade|adversarial|fixtures [options]

Stages:
    scenarios    personas + "a month of life" -> specs/ and truth/ (starts a fresh dataset)
    render       specs -> clean/ PNG (and PDF for gst_invoice, mobile_bill) via Playwright
    degrade      clean renders -> docs/ (scans, phone photos, faded thermal) + manifest.jsonl
    adversarial  (re)create adversarial docs on top of the scenario docs, render + degrade them
    all          everything above; --count is the total, ~20% of it adversarial
    fixtures     export a small committed subset of out/ into fixtures/

Same arguments + same seed => identical truths, renders and degradations.
Bump DATASET_VERSION whenever templates, builders or degradations change.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from claimpilot.domain import DocType

from synthgen.adversarial import KINDS, generate_adversarial_specs
from synthgen.builders import SUPPORTED_DOC_TYPES
from synthgen.manifest import ManifestEntry, is_adversarial, write_manifest
from synthgen.roster import write_roster
from synthgen.scenarios import generate_base_specs
from synthgen.spec import DocSpec, OutputLayout

DATASET_VERSION = "synth-2026.10.1"
SYNTH_DIR = Path(__file__).resolve().parent
ADVERSARIAL_SHARE = 0.2
DOC_TYPE_ALIASES = {"thermal_bill": "restaurant_bill", "upi": "upi_payment"}


def parse_doc_types(text: str | None) -> frozenset[DocType] | None:
    if not text:
        return None
    names = [DOC_TYPE_ALIASES.get(name.strip(), name.strip()) for name in text.split(",")]
    unknown = [name for name in names if name not in {d.value for d in SUPPORTED_DOC_TYPES}]
    if unknown:
        valid = ", ".join(d.value for d in SUPPORTED_DOC_TYPES)
        raise argparse.ArgumentTypeError(f"unknown doc type(s) {unknown}; choose from {valid}")
    return frozenset(DocType(name) for name in names)


def _next_index(specs: list[DocSpec]) -> int:
    return 1 + max((int(spec.id.rsplit("-", 1)[1]) for spec in specs), default=0)


# --- stages ----------------------------------------------------------------------------------


def stage_scenarios(
    layout: OutputLayout, seed: int, count: int, only: frozenset[DocType] | None
) -> list[DocSpec]:
    layout.clear()
    specs = generate_base_specs(seed, count, only)
    for spec in specs:
        layout.save(spec)
    return specs


def stage_render(layout: OutputLayout, specs: list[DocSpec], browser: str) -> None:
    from synthgen.render import render_specs  # Playwright import only when rendering

    render_specs(specs, layout, browser)


def stage_degrade(layout: OutputLayout, specs: list[DocSpec]) -> None:
    from synthgen.degrade import degrade_specs  # augraphy/numba import is slow

    degrade_specs(specs, layout)


def stage_adversarial(
    layout: OutputLayout, seed: int, count: int, only: frozenset[DocType] | None, browser: str
) -> list[DocSpec]:
    existing = layout.load_specs()
    for spec in existing:
        if is_adversarial(spec):
            layout.remove(spec.id)
    base = [spec for spec in existing if not is_adversarial(spec)]
    adversarial = generate_adversarial_specs(
        base, seed=seed, count=count, first_index=_next_index(base), only=only
    )
    for spec in adversarial:
        layout.save(spec)
    stage_render(layout, adversarial, browser)
    stage_degrade(layout, adversarial)
    return adversarial


def write_dataset_info(layout: OutputLayout, args: argparse.Namespace, seconds: float) -> dict:
    specs = layout.load_specs()
    entries = write_manifest(specs, layout)
    info = {
        "dataset_version": DATASET_VERSION,
        "seed": args.seed,
        "count": len(specs),
        "only": sorted(d.value for d in args.only) if args.only else None,
        "wall_time_seconds": round(seconds, 1),
        **summarise(entries),
        "size_on_disk_mb": round(_size(layout.root) / 1e6, 1),
    }
    (layout.root / "dataset.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    write_roster(layout.root / "personas.json", (s.truth.persona_id for s in specs), args.seed)
    return info


def summarise(entries: list[ManifestEntry]) -> dict[str, dict[str, int]]:
    return {
        "doc_types": dict(sorted(Counter(e["doc_type"] for e in entries).items())),
        "tags": dict(sorted(Counter(t for e in entries for t in e["tags"]).items())),
        "splits": dict(sorted(Counter(e["split"] for e in entries).items())),
    }


def _size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


# --- CLI ---------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("scenarios", "render", "degrade", "adversarial", "all", "fixtures", "golden"):
        command = sub.add_parser(name)
        command.add_argument("--out", type=Path, default=SYNTH_DIR / "out")
        command.add_argument("--seed", type=int, default=42)
        command.add_argument("--count", type=int, default=100)
        command.add_argument(
            "--only",
            type=parse_doc_types,
            default=None,
            help="comma-separated doc types, e.g. restaurant_bill,upi_payment",
        )
        command.add_argument(
            "--browser", default="auto", choices=("auto", "chromium", "msedge", "chrome")
        )
        if name == "fixtures":
            command.add_argument("--dest", type=Path, default=SYNTH_DIR / "fixtures")
        if name == "golden":
            command.add_argument("--dest", type=Path, default=SYNTH_DIR / "golden")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    layout = OutputLayout(args.out.resolve())
    started = time.perf_counter()

    if args.command == "scenarios":
        specs = stage_scenarios(layout, args.seed, args.count, args.only)
        print(f"{len(specs)} scenario documents -> {layout.specs}")
    elif args.command == "render":
        stage_render(layout, layout.load_specs(), args.browser)
    elif args.command == "degrade":
        stage_degrade(layout, layout.load_specs())
        write_dataset_info(layout, args, time.perf_counter() - started)
    elif args.command == "adversarial":
        made = stage_adversarial(layout, args.seed, args.count, args.only, args.browser)
        write_dataset_info(layout, args, time.perf_counter() - started)
        print(f"{len(made)} adversarial documents ({', '.join(KINDS)})")
    elif args.command == "all":
        n_adversarial = round(args.count * ADVERSARIAL_SHARE)
        base = stage_scenarios(layout, args.seed, args.count - n_adversarial, args.only)
        stage_render(layout, base, args.browser)
        stage_degrade(layout, base)
        stage_adversarial(layout, args.seed, n_adversarial, args.only, args.browser)
        info = write_dataset_info(layout, args, time.perf_counter() - started)
        print(json.dumps(info, indent=2))
    elif args.command == "golden":
        from synthgen.fixtures import export_golden

        count = export_golden(layout, args.dest.resolve())
        print(f"{count} ground-truth records -> {args.dest}")
    elif args.command == "fixtures":
        from synthgen.fixtures import export_fixtures

        exported = export_fixtures(layout, args.dest.resolve(), args.seed)
        print(f"{len(exported)} fixtures -> {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
