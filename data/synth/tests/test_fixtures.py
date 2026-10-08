"""The committed fixture set (data/synth/fixtures) used by API tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from claimpilot.domain import ReceiptTruth

from synthgen.fixtures import FIXTURE_BUDGET_BYTES
from synthgen.gst import reconciles

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(scope="module")
def entries() -> list[dict]:
    manifest = FIXTURES / "manifest.jsonl"
    if not manifest.exists():
        pytest.skip("fixtures not exported yet (uv run generate.py fixtures)")
    return [json.loads(line) for line in manifest.read_text("utf-8").splitlines()]


def test_fixture_set_is_small_and_varied(entries: list[dict]) -> None:
    assert 8 <= len(entries) <= 12
    assert len({entry["doc_type"] for entry in entries}) >= 7
    tags = {tag for entry in entries for tag in entry["tags"]}
    assert {"injection", "tampered", "hindi", "handwritten"} <= tags
    size = sum(path.stat().st_size for path in FIXTURES.rglob("*") if path.is_file())
    assert size < FIXTURE_BUDGET_BYTES


def test_fixture_files_exist_and_truths_validate(entries: list[dict]) -> None:
    for entry in entries:
        assert (FIXTURES / entry["path"]).is_file()
        truth_json = (FIXTURES / entry["truth_path"]).read_text("utf-8")
        truth = ReceiptTruth.model_validate_json(truth_json)
        assert truth.id == entry["id"]
        assert truth.receipt.contains_instructions is ("injection" in entry["tags"])
        if truth.receipt.line_items:
            assert reconciles(truth.receipt) is ("tampered" not in entry["tags"])
