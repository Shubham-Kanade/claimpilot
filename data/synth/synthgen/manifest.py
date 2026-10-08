"""Dev/test split and ``manifest.jsonl``.

The dev split (20 documents, used for prompt work) is stratified across document types and holds
a few adversarial cases. A duplicate always lands in the same split as its original, so duplicate
detection can be evaluated within one split. Everything else is ``test`` (never tune on it).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import TypedDict

from claimpilot.domain import DocType

from synthgen.spec import DocSpec, OutputLayout

DEV_SIZE = 20
ADVERSARIAL_TAGS = frozenset({"injection", "tampered", "duplicate", "over_policy", "missing_date"})
DEV_ADVERSARIAL = ("injection", "tampered", "over_policy", "duplicate")


class ManifestEntry(TypedDict):
    id: str
    path: str
    truth_path: str
    doc_type: str
    tags: list[str]
    split: str


def is_adversarial(spec: DocSpec) -> bool:
    return bool(ADVERSARIAL_TAGS & set(spec.truth.tags))


def _groups(specs: Sequence[DocSpec]) -> dict[str, list[str]]:
    """Original id -> ids of the original and all its duplicates."""
    groups: dict[str, list[str]] = defaultdict(list)
    for spec in specs:
        groups[spec.truth.duplicate_of or spec.id].append(spec.id)
    return groups


def assign_splits(specs: Sequence[DocSpec], dev_size: int = DEV_SIZE) -> dict[str, str]:
    """Map every spec id to ``dev`` or ``test`` (deterministic: depends only on ids and tags)."""
    ordered = sorted(specs, key=lambda spec: spec.id)
    groups = _groups(ordered)
    group_of = {member: key for key, members in groups.items() for member in members}
    dev: set[str] = set()

    def take(spec: DocSpec) -> None:
        members = groups[group_of[spec.id]]
        if not dev.intersection(members) and len(dev) + len(members) <= dev_size:
            dev.update(members)

    for tag in DEV_ADVERSARIAL:
        tagged = next((spec for spec in ordered if tag in spec.truth.tags), None)
        if tagged:
            take(tagged)

    queues: dict[DocType, list[DocSpec]] = defaultdict(list)
    for spec in ordered:
        if not is_adversarial(spec):
            queues[spec.doc_type].append(spec)
    while len(dev) < dev_size and any(queues.values()):
        for doc_type in DocType:
            if queues[doc_type] and len(dev) < dev_size:
                take(queues[doc_type].pop(0))
    return {spec.id: "dev" if spec.id in dev else "test" for spec in ordered}


def build_manifest(specs: Sequence[DocSpec], layout: OutputLayout) -> list[ManifestEntry]:
    splits = assign_splits(specs)
    entries = [
        ManifestEntry(
            id=spec.id,
            path=layout.document_path(spec).relative_to(layout.root).as_posix(),
            truth_path=(layout.truth / f"{spec.id}.json").relative_to(layout.root).as_posix(),
            doc_type=spec.doc_type.value,
            tags=list(spec.truth.tags),
            split=splits[spec.id],
        )
        for spec in specs
    ]
    return sorted(entries, key=lambda entry: (entry["split"] != "dev", entry["id"]))


def write_manifest(specs: Sequence[DocSpec], layout: OutputLayout) -> list[ManifestEntry]:
    entries = build_manifest(specs, layout)
    lines = (json.dumps(entry, ensure_ascii=False) for entry in entries)
    layout.manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return entries


def read_manifest(path: Path) -> list[ManifestEntry]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]
