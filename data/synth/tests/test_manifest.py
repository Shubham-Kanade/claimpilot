from __future__ import annotations

from pathlib import Path

from synthgen.manifest import DEV_SIZE, assign_splits, read_manifest, write_manifest
from synthgen.spec import DocSpec, OutputLayout

MANIFEST_KEYS = {"id", "path", "truth_path", "doc_type", "tags", "split"}


def test_manifest_lines_and_split_sizes(all_specs: list[DocSpec], tmp_path: Path) -> None:
    layout = OutputLayout(tmp_path)
    write_manifest(all_specs, layout)
    entries = read_manifest(layout.manifest)

    assert len(entries) == len(all_specs)
    assert all(set(entry) == MANIFEST_KEYS for entry in entries)
    splits = [entry["split"] for entry in entries]
    assert splits.count("dev") == DEV_SIZE
    assert splits.count("test") == len(all_specs) - DEV_SIZE
    assert splits[:DEV_SIZE] == ["dev"] * DEV_SIZE  # dev first, then test
    for entry in entries:
        assert entry["truth_path"] == f"truth/{entry['id']}.json"
        assert entry["path"].startswith(f"docs/{entry['id']}.")
        suffix = entry["path"].rsplit(".", 1)[1]
        is_pdf_type = entry["doc_type"] in {"gst_invoice", "mobile_bill"}
        assert (suffix == "pdf") if is_pdf_type else (suffix in {"png", "jpg"})


def test_dev_split_is_stratified_with_adversarial_cases(all_specs: list[DocSpec]) -> None:
    splits = assign_splits(all_specs)
    dev = [spec for spec in all_specs if splits[spec.id] == "dev"]
    assert len({spec.doc_type for spec in dev}) >= 8
    dev_tags = {tag for spec in dev for tag in spec.truth.tags}
    assert {"injection", "tampered", "over_policy", "duplicate"} <= dev_tags


def test_duplicates_share_the_split_of_their_original(all_specs: list[DocSpec]) -> None:
    splits = assign_splits(all_specs)
    for spec in all_specs:
        if spec.truth.duplicate_of:
            assert splits[spec.id] == splits[spec.truth.duplicate_of]


def test_split_is_deterministic(all_specs: list[DocSpec]) -> None:
    assert assign_splits(all_specs) == assign_splits(list(reversed(all_specs)))
