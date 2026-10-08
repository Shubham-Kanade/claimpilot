"""Load the synthetic dataset produced by ``data/synth`` (manifest.jsonl + truth JSON + docs)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from claimpilot.domain import ReceiptTruth


@dataclass(frozen=True, slots=True)
class EvalCase:
    id: str
    doc_path: Path
    truth: ReceiptTruth
    doc_type: str
    tags: tuple[str, ...]
    split: str

    def read_bytes(self) -> bytes:
        return self.doc_path.read_bytes()


def load_cases(
    manifest: Path, *, split: str | None = None, limit: int | None = None
) -> list[EvalCase]:
    """Read ``manifest.jsonl``. ``path`` and ``truth_path`` are relative to its folder."""
    root = manifest.parent
    cases: list[EvalCase] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if split is not None and row["split"] != split:
            continue
        truth = ReceiptTruth.model_validate_json((root / row["truth_path"]).read_text("utf-8"))
        cases.append(
            EvalCase(
                id=row["id"],
                doc_path=root / row["path"],
                truth=truth,
                doc_type=row["doc_type"],
                tags=tuple(row.get("tags", ())),
                split=row["split"],
            )
        )
        if limit is not None and len(cases) >= limit:
            break
    return cases
