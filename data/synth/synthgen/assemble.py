"""Turn a builder's :class:`Draft` into a finished :class:`DocSpec`: id, languages, tags and the
degradation plan."""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Iterator
from typing import Any

from claimpilot.domain import DocType, ReceiptTruth

from synthgen.rng import derive_rng, derive_seed
from synthgen.spec import PDF_DOC_TYPES, DocSpec, Draft

DEVANAGARI = re.compile(f"[{chr(0x0900)}-{chr(0x097F)}]")  # the Devanagari Unicode block

CLEAN = "clean"
SCREEN_CAPTURE = "screen_capture"
PHOTO_PRESETS = ("photo", "photo_folded", "photo_low_light")
PAPER_PRESETS = ("scan", *PHOTO_PRESETS)
THERMAL_PRESETS = ("thermal_faded",)
ALL_PRESETS = (CLEAN, SCREEN_CAPTURE, *PAPER_PRESETS, *THERMAL_PRESETS)
THERMAL_DOC_TYPES = frozenset({DocType.restaurant_bill, DocType.fuel_slip})

PAPER_CLEAN_SHARE = 0.2  # with PDFs and most screenshots clean, ~30% of the dataset is clean


def plan_degradation(doc_type: DocType, rng: random.Random) -> str:
    """Choose how a document will look: digital PDFs stay clean, screenshots are mostly clean,
    paper documents are mostly scanned or photographed."""
    if doc_type in PDF_DOC_TYPES:
        return CLEAN
    if doc_type == DocType.upi_payment:
        return CLEAN if rng.random() < 0.6 else SCREEN_CAPTURE
    if rng.random() < PAPER_CLEAN_SHARE:
        return CLEAN
    pool = PAPER_PRESETS + THERMAL_PRESETS if doc_type in THERMAL_DOC_TYPES else PAPER_PRESETS
    weights = [1 if preset == "scan" else 2 for preset in pool]
    return rng.choices(pool, weights=weights)[0]


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def detect_languages(*printed: Any) -> list[str]:
    """ISO 639-1 codes of the scripts printed: English always, Hindi if Devanagari appears."""
    hindi = any(DEVANAGARI.search(text) for text in _strings(list(printed)))
    return ["en", "hi"] if hindi else ["en"]


def base_tags(spec_degrade: str, languages: Iterable[str], handwritten: bool) -> list[str]:
    tags = [CLEAN if spec_degrade == CLEAN else "degraded"]
    if "hi" in languages:
        tags.append("hindi")
    if handwritten:
        tags.append("handwritten")
    return tags


def assemble(
    draft: Draft,
    *,
    doc_id: str,
    seed: int,
    persona_id: str | None,
    trip_id: str | None = None,
    extra_tags: Iterable[str] = (),
    degrade: str | None = None,
) -> DocSpec:
    receipt = draft.receipt.model_copy(
        update={"languages": detect_languages(draft.receipt.model_dump(mode="json"), draft.extras)}
    )
    preset = degrade or plan_degradation(receipt.doc_type, derive_rng(seed, "degrade", doc_id))
    tags = [*base_tags(preset, receipt.languages, receipt.handwritten), *extra_tags]
    truth = ReceiptTruth(
        id=doc_id,
        receipt=receipt,
        category=draft.category,
        tags=tags,
        persona_id=persona_id,
        trip_id=trip_id,
    )
    return DocSpec(
        truth=truth,
        extras={"injection_text": None, **draft.extras},  # every template has an injection slot
        style=draft.style,
        degrade=preset,
        render_seed=derive_seed(seed, "render", doc_id),
    )


def doc_id(seed: int, index: int) -> str:
    return f"s{seed}-{index:04d}"
