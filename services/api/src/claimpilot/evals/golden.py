"""Ground-truth helpers over the committed golden dataset (``data/synth/golden``).

The golden set holds truth JSON only (no images), so policy, grouping and decision evals can
run in CI without rendering or paying for anything. ``to_processed`` builds the
``ProcessedDocument`` the pipeline would produce if extraction and decisions were perfect.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from claimpilot.config import REPO_ROOT
from claimpilot.domain import ReceiptTruth
from claimpilot.domain.claims import Decisions, Employee, ProcessedDocument

GOLDEN_DIR = REPO_ROOT / "data" / "synth" / "golden"

_ALCOHOL = re.compile(
    r"\b(beer|wine|whisk(?:e)?y|vodka|rum|gin|brandy|cocktail|lager|champagne)\b"
    r"|बीयर|वाइन|व्हिस्की|शराब",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class GoldenCase:
    truth: ReceiptTruth
    employee: Employee
    split: str
    tags: tuple[str, ...]


def load_employees(path: Path) -> dict[str, Employee]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {
        r["id"]: Employee(
            id=r["id"],
            name=r["name"],
            employee_id=r["employee_id"],
            grade=r["grade"],
            base_city=r["base_city"],
            base_state_code=r.get("base_state_code"),
        )
        for r in rows
    }


def load_golden(directory: Path = GOLDEN_DIR) -> list[GoldenCase]:
    employees = load_employees(directory / "personas.json")
    cases = []
    for line in (directory / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        truth = ReceiptTruth.model_validate_json(
            (directory / row["truth_path"]).read_text(encoding="utf-8")
        )
        cases.append(
            GoldenCase(
                truth=truth,
                employee=employees[truth.persona_id or ""],
                split=row["split"],
                tags=tuple(row["tags"]),
            )
        )
    return cases


def has_alcohol(truth: ReceiptTruth) -> bool:
    return any(_ALCOHOL.search(i.description) for i in truth.receipt.line_items)


def to_processed(truth: ReceiptTruth) -> ProcessedDocument:
    """The document as the pipeline would see it with perfect extraction and decisions."""
    return ProcessedDocument(
        id=truth.id,
        filename=f"{truth.id}.png",
        sha256=f"truth-{truth.id}",
        receipt=truth.receipt,
        decisions=Decisions(
            category=truth.category,
            category_confidence=1.0,
            alcohol_present=1.0 if has_alcohol(truth) else 0.0,
            personal_expense=0.0,
            engine="truth",
        ),
    )
