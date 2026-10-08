"""The evaluation tool: runs on the committed fixtures and on a tiny dataset built here."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_trust_support import (
    FIXTURES,
    TRANSFORMS,
    bill,
    jpeg_bytes,
    needs_fixtures,
    receipt_image,
    reupload,
)

from claimpilot.domain import ExpenseCategory, ReceiptTruth
from claimpilot.trust import eval as trust_eval
from claimpilot.trust.eval import (
    ADVERSARIAL_TAGS,
    duplicate_families,
    injection_text_recall,
    load_items,
    main,
    report,
    sweep_thresholds,
)


def write_dataset(root: Path) -> Path:
    """Four documents: an original, a resized duplicate, a template twin and an injection."""
    docs = root / "docs"
    truth_dir = root / "truth"
    docs.mkdir(parents=True)
    truth_dir.mkdir()
    original = jpeg_bytes(receipt_image(1))
    twin = reupload(original, TRANSFORMS["jpeg_q40"])  # same picture, but another bill
    entries = [
        ("t-0001", original, bill(), [], None),
        ("t-0002", reupload(original, TRANSFORMS["resize_70"]), bill(), ["duplicate"], "t-0001"),
        ("t-0003", twin, bill(invoice_number="R999"), [], None),
        (
            "t-0004",
            jpeg_bytes(receipt_image(9)),
            bill(invoice_number="R777", contains_instructions=True),
            ["injection"],
            None,
        ),
    ]
    rows = []
    for doc_id, raw, receipt, tags, duplicate_of in entries:
        (docs / f"{doc_id}.jpg").write_bytes(raw)
        truth = ReceiptTruth(
            id=doc_id,
            receipt=receipt,
            category=ExpenseCategory.meals,
            tags=tags,
            duplicate_of=duplicate_of,
            persona_id="P001",
        )
        (truth_dir / f"{doc_id}.json").write_text(truth.model_dump_json(), encoding="utf-8")
        rows.append(
            {
                "id": doc_id,
                "path": f"docs/{doc_id}.jpg",
                "truth_path": f"truth/{doc_id}.json",
                "doc_type": "restaurant_bill",
                "tags": tags,
                "split": "dev",
            }
        )
    manifest = "\n".join(json.dumps(r) for r in rows)
    (root / "manifest.jsonl").write_text(manifest, encoding="utf-8")
    return root


def test_items_load_in_id_order_and_families_follow_duplicate_of(tmp_path):
    items = load_items(write_dataset(tmp_path))
    assert [i.id for i in items] == ["t-0001", "t-0002", "t-0003", "t-0004"]
    family = duplicate_families(items)
    assert family["t-0001"] == family["t-0002"] != family["t-0003"]
    assert family["t-0004"] == "t-0004"
    assert items[1].tags == {"duplicate"} and items[1].receipt.merchant_name == "Rasoi Ghar Eatery"


def test_the_sweep_finds_the_true_pair_and_no_false_one(tmp_path):
    items = load_items(write_dataset(tmp_path))
    sweep = sweep_thresholds(items, max_threshold=10)
    assert sweep.true_pairs == 1 and sweep.total_pairs == 6
    assert len(sweep.rows) == 11
    assert (sweep.fingerprint_flagged, sweep.fingerprint_true) == (1, 1)
    for row in sweep.rows:  # the bill's resized copy is found at every threshold, nothing else
        assert (row.policy_flagged, row.policy_true) == (1, 1)
    assert sweep.rows[10].image_flagged > sweep.rows[10].image_true == 1  # picture alone: twin too


def test_injection_text_recall_counts_lines_and_false_hits(tmp_path):
    items = load_items(write_dataset(tmp_path))
    caught, tried, false_hits, others = injection_text_recall(items)
    assert (caught, tried, false_hits, others) == (4, 4, 0, 3)


def test_the_report_has_every_section_and_no_false_positives(tmp_path):
    items = load_items(write_dataset(tmp_path))
    text = report(items, max_threshold=4, dataset=tmp_path)
    for heading in ("1. Duplicate detection", "2. End to end", "3. False positives"):
        assert heading in text
    assert "duplicates flagged:   1/1" in text
    assert "injection flagged:    1/1" in text
    assert "high findings: 0" in text


@needs_fixtures
def test_the_cli_runs_on_the_committed_fixtures(capsys):
    assert main(["--dataset", str(FIXTURES), "--max-threshold", "3"]) == 0
    out = capsys.readouterr().out
    assert "documents: 10" in out and "chosen: PHASH_MAX_DISTANCE=8" in out
    assert "high findings: 0" in out  # no genuine fixture gets a high finding
    assert "injection flagged:    1/1" in out and "tampered flagged:     2/2" in out
    assert "ai_generated blocked: n/a" in out


@needs_fixtures
def test_the_cli_can_limit_the_documents(capsys):
    assert main(["--dataset", str(FIXTURES), "--max-threshold", "1", "--limit", "3"]) == 0
    assert "documents: 3" in capsys.readouterr().out


def test_adversarial_tags_are_the_four_trust_cases():
    assert {"duplicate", "tampered", "injection", "ai_generated"} == ADVERSARIAL_TAGS


def test_the_module_is_runnable_as_a_script():
    assert callable(trust_eval.main)
    with pytest.raises(SystemExit):
        main(["--help"])
