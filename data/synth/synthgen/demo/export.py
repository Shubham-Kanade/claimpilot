"""Render the demo pile and write the committed folder.

Output (``data/synth/demo`` by default)::

    docs/<id>.png|jpg|pdf   the documents, named for upload order (01-..., 02-...)
    truth/<id>.json         ReceiptTruth, same format as golden/ and fixtures/
    manifest.jsonl          {id, path, truth_path, doc_type, tags, split="demo"}, in upload order
    persona.json            DEMO-ASHA, the same record as services/mcp-corp/seed/employees.json
    README.md               what each document is, why it is there and what should happen to it

Rendering happens in a temporary folder with the generator's own renderer and degradations; only
the finished files are copied out, small enough to commit (about 2 MB for the 15 documents): clean
pictures stay lossless PNG, everything that looks like a photo or a scan becomes a downscaled
JPEG, like a phone would save it.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from synthgen.assemble import CLEAN
from synthgen.degrade import degrade_image, limit_size
from synthgen.demo.builders import DemoDoc
from synthgen.demo.persona import DEMO_EMPLOYEE, DEMO_SPLIT, DEMO_TODAY
from synthgen.demo.story import CLAIM_ROUTES, QUESTION_TRIP, QUESTION_UPI, build_demo_docs
from synthgen.manifest import ManifestEntry
from synthgen.render import render_specs
from synthgen.spec import DocSpec, OutputLayout
from synthgen.textfmt import indian_grouping

DEMO_BUDGET_BYTES = 3 * 1024 * 1024  # the committed folder stays about as small as fixtures/
EXPORT_MAX_SIDE = 1568  # px: the long edge the pipeline downsizes every upload to anyway
JPEG_QUALITY = (76, 86)  # chosen per document from its render seed, like phones differ
PDF_DATE = re.compile(rb"(/(?:CreationDate|ModDate) \(D:)\d{14}")

LOOKS: dict[str, str] = {
    "clean": "clean digital image",
    "scan": "scan of a printout",
    "photo": "phone photo",
    "photo_folded": "phone photo, folded paper",
    "photo_low_light": "phone photo, low light",
    "thermal_faded": "phone photo, faded thermal paper",
    "screen_capture": "re-shared screenshot",
}


def look_of(spec: DocSpec) -> str:
    return "PDF" if spec.is_pdf else LOOKS[spec.degrade]


# --- documents -----------------------------------------------------------------------------------


def pin_pdf_dates(pdf: bytes, day: str | None) -> bytes:
    """Replace the browser's creation and modification time with the document's own date.

    The only thing that differs between two renders of the same PDF is that timestamp. Swapping
    its 14 digits for the document's date at 09:00:00 keeps the file the same length (so every
    offset stays valid) and makes the PDF byte-identical on every run.
    """
    stamp = (day or DEMO_TODAY.isoformat()).replace("-", "") + "090000"
    return PDF_DATE.sub(lambda match: match.group(1) + stamp.encode("ascii"), pdf)


def _write_png(image: np.ndarray, target: Path) -> None:
    if not cv2.imwrite(str(target), image, [cv2.IMWRITE_PNG_COMPRESSION, 9]):
        raise OSError(f"could not write {target}")


def _write_jpeg(image: np.ndarray, target: Path, seed: int) -> None:
    low, high = JPEG_QUALITY
    quality = int(np.random.default_rng(seed).integers(low, high + 1))
    params = [cv2.IMWRITE_JPEG_QUALITY, quality, cv2.IMWRITE_JPEG_OPTIMIZE, 1]
    if not cv2.imwrite(str(target), limit_size(image, EXPORT_MAX_SIDE), params):
        raise OSError(f"could not write {target}")


def export_document(spec: DocSpec, layout: OutputLayout, docs_dir: Path) -> Path:
    """Turn the clean render of ``spec`` into its final file in ``docs_dir``; return its path."""
    if spec.is_pdf:
        target = docs_dir / f"{spec.id}.pdf"
        pdf = (layout.clean / f"{spec.id}.pdf").read_bytes()
        target.write_bytes(pin_pdf_dates(pdf, spec.truth.receipt.date))
        return target
    clean = cv2.imread(str(layout.clean / f"{spec.id}.png"), cv2.IMREAD_COLOR)
    look = degrade_image(clean, spec.degrade, spec.render_seed)
    if spec.degrade == CLEAN:
        target = docs_dir / f"{spec.id}.png"
        _write_png(look, target)
    else:
        target = docs_dir / f"{spec.id}.jpg"
        _write_jpeg(look, target, spec.render_seed)
    return target


# --- README --------------------------------------------------------------------------------------


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def _rs(amount: float) -> str:
    return f"Rs {indian_grouping(amount)}"


TRAPS: dict[str, str] = {
    "duplicate": "a duplicate",
    "tampered": "an edited total",
    "injection": "a prompt injection",
    "over_policy": "an alcohol bill",
}


def render_readme(docs: list[DemoDoc], files: dict[str, Path]) -> str:
    """The README of the pile, generated from the same objects that produce the documents."""
    rows = [
        f"| {number} | `{files[doc.id].name}`<br>{look_of(doc.spec)} | {_cell(doc.what)} "
        f"| {_cell(doc.why)} | {_cell(doc.expect)} |"
        for number, doc in enumerate(docs, 1)
    ]
    members: dict[str, list[DemoDoc]] = defaultdict(list)
    for doc in docs:
        members[doc.claim].append(doc)
    claims = [
        f"| {title} | {', '.join(doc.id[:2] for doc in group)} "
        f"| {_rs(sum(doc.spec.truth.receipt.total or 0 for doc in group))} "
        f"| {CLAIM_ROUTES[title]} |"
        for title, group in members.items()
    ]
    traps = [
        f"- {doc.id[:2]}: {TRAPS[tag]}"
        for doc in docs
        for tag in doc.spec.truth.tags
        if tag in TRAPS
    ]
    return README.format(
        count=len(docs),
        honest=len(docs) - len(traps),
        trap_count=len(traps),
        traps="\n".join(traps),
        claim_count=len(members),
        question_trip=QUESTION_TRIP,
        question_upi=QUESTION_UPI,
        today=DEMO_TODAY.isoformat(),
        pile="\n".join(
            [
                "| # | File | What it is | Why it is in the pile | Expected outcome |",
                "|---|---|---|---|---|",
                *rows,
            ]
        ),
        claims="\n".join(
            ["| Claim | Documents | Total printed | Route |", "|---|---|---|---|", *claims]
        ),
    )


README = """\
# Demo pile: Asha Menon's week

{count} synthetic documents that tell ONE story, for the hosted demo and the 3-minute video.
Everything is fictional: Orion Demo Corp, every merchant and person, every GSTIN (valid checksum,
random PAN part). Nothing here is real data.

Regenerate (about a minute; needs a Chromium-family browser, Edge is found automatically):

```bash
cd data/synth
uv run generate.py demo            # writes data/synth/demo
uv run generate.py demo --seed 7   # same story and truths, different photo angles and noise
```

The truths are written out in `synthgen/demo/story.py`, so they never change with `--seed`; the
seed only changes how photos and scans look. Same seed, same bytes (PDFs included).

## The story

Asha Menon (`DEMO-ASHA`, grade L3, base city Pune, Orion Demo Corp) hands ClaimPilot her pile
after a busy week. Her calendar (`services/mcp-corp/seed/calendar.json`) has a client meeting and a
client dinner with Kestrel Logistics on 6 Oct, and a client visit to Mumbai on 9 to 10 Oct. Pin the
demo clock to {today} (`DEMO_TODAY={today}`): every receipt is then inside the 90 day window.

{honest} documents are honest. The other {trap_count} are traps that ClaimPilot should catch:

{traps}

Upload all of them as `DEMO-ASHA`, in this order (the file names sort that way): the second copy
of a bill is the one that gets flagged.

## The pile, in upload order

{pile}

Dates are written with the month's name (06-Oct-2026) on purpose: 06/10 would read as 6 Oct or
10 Jun.

## What should happen

Fed to the real grouping, policy and trust code with perfect extraction, the pile makes
{claim_count} claims. `services/api/tests/unit/test_demo_pile.py` asserts all of this offline.

{claims}

Two questions are left for Asha; everything else is answered from the calendar or needs no answer:

- Mumbai trip: "{question_trip}" The calendar lists the trip as travel, not as a client event,
  so it cannot answer.
- Local conveyance: "{question_upi}" Asked because System One is unsure of the category of
  document 11.

The trust checks find nothing on the honest documents. They catch the duplicate, the edited total
and the injected note; the alcohol bill is a policy finding (6.1), not a trust one. Only the first
client dinner and the mobile bill are auto-approved.

## Files

- `docs/`: the documents (`.png` clean pictures, `.jpg` photos and scans, `.pdf` the two PDFs).
- `truth/<id>.json`: what is printed on each document, as `ReceiptTruth` (see `claimpilot.domain`).
  Tampered, duplicate and injected documents keep the truth of what is printed, so the tampered
  total does not reconcile, on purpose.
- `manifest.jsonl`: one line per document in upload order, in the format of `golden/` and
  `fixtures/` (`split` is `demo`).
- `persona.json`: Asha's directory record, identical to `DEMO-ASHA` in
  `services/mcp-corp/seed/employees.json`.
"""


# --- the whole pile ------------------------------------------------------------------------------


def _write_text(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))  # LF on every platform, so the bytes are reproducible


def write_pile(stage: Path, docs: list[DemoDoc], files: dict[str, Path]) -> list[ManifestEntry]:
    """Write everything but the documents (truths, manifest, persona, README) into ``stage``."""
    entries: list[ManifestEntry] = []
    for doc in docs:
        truth = doc.spec.truth
        _write_text(stage / "truth" / f"{doc.id}.json", truth.model_dump_json(indent=2) + "\n")
        entries.append(
            ManifestEntry(
                id=doc.id,
                path=f"docs/{files[doc.id].name}",
                truth_path=f"truth/{doc.id}.json",
                doc_type=truth.receipt.doc_type.value,
                tags=list(truth.tags),
                split=DEMO_SPLIT,
            )
        )
    lines = (json.dumps(entry, ensure_ascii=False) for entry in entries)
    _write_text(stage / "manifest.jsonl", "\n".join(lines) + "\n")
    _write_text(stage / "persona.json", json.dumps(DEMO_EMPLOYEE, indent=2) + "\n")
    _write_text(stage / "README.md", render_readme(docs, files))
    return entries


def generate_demo(out: Path, seed: int = 42, browser: str = "auto") -> list[ManifestEntry]:
    """Render the pile and put it in ``out`` (replacing its ``docs/`` and ``truth/``).

    Everything is built in a temporary folder first, so a render that fails (no browser, say)
    leaves the committed pile as it was.
    """
    docs = build_demo_docs(seed)
    with tempfile.TemporaryDirectory(prefix="claimpilot-demo-") as work:
        layout, stage = OutputLayout(Path(work) / "render"), Path(work) / "pile"
        for sub in ("docs", "truth"):
            (stage / sub).mkdir(parents=True)
        render_specs([doc.spec for doc in docs], layout, browser)
        files = {doc.id: export_document(doc.spec, layout, stage / "docs") for doc in docs}
        entries = write_pile(stage, docs, files)
        total = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file())
        if total > DEMO_BUDGET_BYTES:
            raise RuntimeError(f"the demo pile is {total / 1e6:.1f} MB, over the 3 MB budget")
        for sub in ("docs", "truth"):
            shutil.rmtree(out / sub, ignore_errors=True)
        shutil.copytree(stage, out, dirs_exist_ok=True)
    return entries
