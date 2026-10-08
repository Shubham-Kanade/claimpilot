"""Document specs: the ground truth plus everything else the template prints.

A ``DocSpec`` is the single input of the render and degrade stages, so a document can always be
re-rendered exactly from its JSON. ``truth.receipt`` holds only what is printed; ``extras`` holds
printed context that is not part of the extraction schema (addresses, PNRs, table numbers ...).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claimpilot.domain import DocType, ExpenseCategory, ExtractedReceipt, ReceiptTruth
from pydantic import BaseModel, Field

PDF_DOC_TYPES = frozenset({DocType.gst_invoice, DocType.mobile_bill})
LOSSLESS_PRESETS = frozenset({"clean", "scan"})  # everything else is a photo/screenshot -> JPEG


@dataclass
class Draft:
    """What a builder produces: printed receipt fields plus render-only context."""

    receipt: ExtractedReceipt
    category: ExpenseCategory
    extras: dict[str, Any] = field(default_factory=dict)
    style: dict[str, Any] = field(default_factory=dict)


class DocSpec(BaseModel):
    truth: ReceiptTruth
    extras: dict[str, Any] = Field(default_factory=dict)
    style: dict[str, Any] = Field(default_factory=dict)
    degrade: str = Field(description="Degradation preset name (see synthgen.degrade.PRESETS)")
    render_seed: int = Field(description="Seed for the degradation stage")

    @property
    def id(self) -> str:
        return self.truth.id

    @property
    def doc_type(self) -> DocType:
        return self.truth.receipt.doc_type

    @property
    def is_pdf(self) -> bool:
        return self.doc_type in PDF_DOC_TYPES


@dataclass(frozen=True)
class OutputLayout:
    """Directory layout of one generated dataset (``data/synth/out`` by default)."""

    root: Path

    @property
    def specs(self) -> Path:
        return self.root / "specs"

    @property
    def truth(self) -> Path:
        return self.root / "truth"

    @property
    def html(self) -> Path:
        return self.root / "html"

    @property
    def clean(self) -> Path:
        return self.root / "clean"

    @property
    def docs(self) -> Path:
        return self.root / "docs"

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.jsonl"

    def document_path(self, spec: DocSpec) -> Path:
        """The final document listed in the manifest: PDF, lossless PNG or phone-style JPEG."""
        if spec.is_pdf:
            return self.docs / f"{spec.id}.pdf"
        suffix = ".png" if spec.degrade in LOSSLESS_PRESETS else ".jpg"
        return self.docs / f"{spec.id}{suffix}"

    def ensure(self) -> None:
        for directory in (self.specs, self.truth, self.html, self.clean, self.docs):
            directory.mkdir(parents=True, exist_ok=True)

    def save(self, spec: DocSpec) -> None:
        self.ensure()
        (self.specs / f"{spec.id}.json").write_text(spec.model_dump_json(indent=2), "utf-8")
        (self.truth / f"{spec.id}.json").write_text(spec.truth.model_dump_json(indent=2), "utf-8")

    def load_specs(self) -> list[DocSpec]:
        paths = sorted(self.specs.glob("*.json"))
        return [DocSpec.model_validate(json.loads(p.read_text("utf-8"))) for p in paths]

    def remove(self, spec_id: str) -> None:
        """Delete every generated file of one document."""
        for directory in (self.specs, self.truth, self.html, self.clean, self.docs):
            for path in directory.glob(f"{spec_id}.*"):
                path.unlink()

    def clear(self) -> None:
        """Remove generated files from a previous run (keeps the root directory)."""
        for directory in (self.specs, self.truth, self.html, self.clean, self.docs):
            if directory.exists():
                for path in directory.iterdir():
                    path.unlink()
        self.manifest.unlink(missing_ok=True)
