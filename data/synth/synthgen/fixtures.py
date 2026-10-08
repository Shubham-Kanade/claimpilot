"""Export a small, committed fixture set (for API tests) from a generated dataset.

Images are downscaled and palette-compressed to PNG so the whole set stays well under 3 MB.
PDF documents are copied as-is (they are small and API tests need a real PDF upload).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageFilter

from synthgen.manifest import ManifestEntry, read_manifest
from synthgen.spec import OutputLayout

FIXTURE_MAX_SIDE = 1100
FIXTURE_BUDGET_BYTES = 3 * 1024 * 1024

Selector = Callable[[ManifestEntry], bool]


def _doc(doc_type: str, *tags: str) -> Selector:
    return lambda entry: entry["doc_type"] == doc_type and all(t in entry["tags"] for t in tags)


def _tag(*tags: str) -> Selector:
    return lambda entry: all(tag in entry["tags"] for tag in tags)


# One fixture per row; each row lists selectors in order of preference. Noisy phone photos are
# ~0.5 MB even as palette PNGs, so only three rows ask for degraded documents.
FIXTURE_PLAN: tuple[tuple[Selector, ...], ...] = (
    (_doc("restaurant_bill", "hindi", "degraded"), _doc("restaurant_bill", "hindi")),
    (_doc("handwritten_bill", "degraded"), _doc("handwritten_bill")),
    (_doc("cab_receipt", "degraded"), _doc("cab_receipt")),
    (_doc("upi_payment", "clean"), _doc("upi_payment")),
    (_doc("hotel_folio", "clean"), _doc("hotel_folio")),
    (_doc("flight_ticket", "clean"), _doc("flight_ticket")),
    (_doc("fuel_slip", "clean"), _doc("fuel_slip")),
    (_doc("gst_invoice"),),
    (_tag("injection", "clean"), _tag("injection")),
    (_tag("tampered", "clean"), _tag("tampered")),
)


def select_fixtures(entries: list[ManifestEntry]) -> list[ManifestEntry]:
    chosen: list[ManifestEntry] = []
    for alternatives in FIXTURE_PLAN:
        for selector in alternatives:
            match = next((e for e in entries if selector(e) and e not in chosen), None)
            if match:
                chosen.append(match)
                break
    return chosen


def compress_png(source: Path, target: Path, noisy: bool) -> None:
    """Downscale and palette-encode; noisy photos are median-filtered first (noise does not
    compress). Text stays legible at 1100 px."""
    with Image.open(source) as image:
        rgb = image.convert("RGB")
    rgb.thumbnail((FIXTURE_MAX_SIDE, FIXTURE_MAX_SIDE), Image.Resampling.LANCZOS)
    if noisy:
        rgb = rgb.filter(ImageFilter.MedianFilter(3))
    palette = rgb.quantize(
        colors=32 if noisy else 64, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    palette.save(target, optimize=True)


def export_fixtures(layout: OutputLayout, dest: Path) -> list[ManifestEntry]:
    """Copy the selected documents + truths into ``dest`` and write ``dest/manifest.jsonl``."""
    entries = select_fixtures(read_manifest(layout.manifest))
    if dest.exists():
        shutil.rmtree(dest / "docs", ignore_errors=True)
        shutil.rmtree(dest / "truth", ignore_errors=True)
    (dest / "docs").mkdir(parents=True, exist_ok=True)
    (dest / "truth").mkdir(parents=True, exist_ok=True)

    exported: list[ManifestEntry] = []
    for entry in entries:
        source = layout.root / entry["path"]
        if source.suffix == ".pdf":
            doc_path = Path("docs") / source.name
            shutil.copyfile(source, dest / doc_path)
        else:
            doc_path = Path("docs") / f"{entry['id']}.png"
            compress_png(source, dest / doc_path, noisy="degraded" in entry["tags"])
        truth_path = Path("truth") / f"{entry['id']}.json"
        shutil.copyfile(layout.root / entry["truth_path"], dest / truth_path)
        exported.append({**entry, "path": doc_path.as_posix(), "truth_path": truth_path.as_posix()})

    lines = (json.dumps(entry, ensure_ascii=False) for entry in exported)
    (dest / "manifest.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    total = sum(path.stat().st_size for path in dest.rglob("*") if path.is_file())
    if total > FIXTURE_BUDGET_BYTES:
        raise RuntimeError(f"fixtures are {total / 1e6:.1f} MB, over the 3 MB budget")
    return exported
