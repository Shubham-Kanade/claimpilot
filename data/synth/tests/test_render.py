"""End-to-end render + degrade + manifest on a tiny dataset. Skipped without a browser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from claimpilot.domain import ReceiptTruth
from PIL import Image

import generate
from synthgen.render import browser_available

pytestmark = pytest.mark.render


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not browser_available():
        pytest.skip("no Chromium-family browser available for Playwright")
    out = tmp_path_factory.mktemp("synth-out")
    assert generate.main(["all", "--count", "22", "--seed", "3", "--out", str(out)]) == 0
    return out


def _manifest(out: Path) -> list[dict]:
    return [json.loads(line) for line in (out / "manifest.jsonl").read_text("utf-8").splitlines()]


def test_every_manifest_document_exists_and_validates(dataset: Path) -> None:
    entries = _manifest(dataset)
    assert len(entries) == 22
    for entry in entries:
        document = dataset / entry["path"]
        assert document.stat().st_size > 5_000
        truth_json = (dataset / entry["truth_path"]).read_text("utf-8")
        truth = ReceiptTruth.model_validate_json(truth_json)
        assert truth.id == entry["id"]
        assert truth.receipt.doc_type.value == entry["doc_type"]
        if document.suffix == ".pdf":
            assert document.read_bytes().startswith(b"%PDF")
            assert (dataset / "docs" / f"{entry['id']}.preview.png").exists()
        else:
            with Image.open(document) as image:
                assert min(image.size) >= 300


def test_dataset_info_is_written(dataset: Path) -> None:
    info = json.loads((dataset / "dataset.json").read_text("utf-8"))
    assert info["dataset_version"] == generate.DATASET_VERSION
    assert info["count"] == 22
    assert info["splits"] == {"dev": 20, "test": 2}


def test_rerender_is_identical(dataset: Path, tmp_path: Path) -> None:
    assert generate.main(["all", "--count", "22", "--seed", "3", "--out", str(tmp_path)]) == 0
    for entry in _manifest(dataset):
        if entry["path"].endswith(".pdf"):
            continue  # PDFs embed a creation timestamp
        first = (dataset / entry["path"]).read_bytes()
        assert first == (tmp_path / entry["path"]).read_bytes(), entry["id"]
