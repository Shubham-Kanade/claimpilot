"""Grouping against the golden set's ground-truth ``trip_id`` (and the metric's own arithmetic).

Objective: pairwise precision and recall of "two documents are in the same trip" are both >= 0.9
on the non-adversarial documents, and no document without a ``trip_id`` ends up in a trip claim
unless it is a stand-alone ticket or hotel (which is a trip by definition).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

import pytest
from test_policy_factories import cab, employee, hotel, meal, ticket

from claimpilot.claims import group_documents
from claimpilot.claims.eval import (
    ADVERSARIAL_TAGS,
    CALIBRATION_TODAY,
    GroupingReport,
    PairScores,
    Stray,
    evaluate_grouping,
    format_report,
    is_adversarial,
    main,
)
from claimpilot.domain import ClaimMode, ExpenseCategory, ProcessedDocument, ReceiptTruth
from claimpilot.evals.golden import GOLDEN_DIR, GoldenCase, load_golden, to_processed

BASE = "Pune"


@pytest.fixture(scope="module")
def cases() -> list[GoldenCase]:
    if not GOLDEN_DIR.exists():
        pytest.skip("golden dataset not generated")
    return load_golden()


@pytest.fixture(scope="module")
def report(cases: list[GoldenCase]) -> GroupingReport:
    return evaluate_grouping(cases)


# --- the objective ------------------------------------------------------------------------------


def test_pairwise_precision_and_recall_meet_the_objective(report: GroupingReport):
    assert report.clean.documents == 80
    assert report.clean.true_pairs > 0
    assert report.clean.precision >= 0.9
    assert report.clean.recall >= 0.9
    assert report.clean.f1 >= 0.9


def test_the_adversarial_documents_do_not_hurt_either(report: GroupingReport):
    assert report.everything.documents == 100
    assert report.everything.precision >= 0.9 and report.everything.recall >= 0.9


def test_no_clean_document_without_a_trip_id_lands_in_a_trip(
    cases: list[GoldenCase], report: GroupingReport
):
    clean_ids = {c.truth.id for c in cases if not is_adversarial(c)}
    assert {s.document_id for s in report.strays}.isdisjoint(clean_ids)


def test_every_stray_is_a_justified_standalone_anchor(report: GroupingReport):
    assert report.strays  # the adversarial hotels and the injected flight form one-document trips
    assert report.unjustified_strays == ()
    assert all("stand-alone" in s.reason for s in report.strays)


def test_a_trip_is_never_split_and_never_mixed(cases: list[GoldenCase]):
    by_persona: dict[str, list[GoldenCase]] = defaultdict(list)
    for case in cases:
        by_persona[case.employee.id].append(case)
    for members in by_persona.values():
        docs = [to_processed(c.truth) for c in members]
        trip_of = {c.truth.id: c.truth.trip_id for c in members}
        claims = group_documents(members[0].employee, docs, today=CALIBRATION_TODAY)
        homes: dict[str, set[str]] = defaultdict(set)
        for claim in claims:
            ids = {trip for i in claim.document_ids if (trip := trip_of[i])}
            assert len(ids) <= 1, (claim.title, ids)  # one claim never holds two trips
            for trip_id in ids:
                homes[trip_id].add(claim.id)
        assert all(len(claim_ids) == 1 for claim_ids in homes.values()), homes  # no trip is split


def test_claim_ids_are_unique_across_the_dataset(cases: list[GoldenCase]):
    ids: list[str] = []
    by_persona: dict[str, list[GoldenCase]] = defaultdict(list)
    for case in cases:
        by_persona[case.employee.id].append(case)
    for members in by_persona.values():
        docs = [to_processed(c.truth) for c in members]
        ids += [c.id for c in group_documents(members[0].employee, docs, today=CALIBRATION_TODAY)]
    assert len(ids) == len(set(ids))


def test_the_report_counts_claims_and_trips(report: GroupingReport):
    assert report.claims > report.trip_claims > 0


def test_the_documented_adversarial_tags_are_the_dataset_s(cases: list[GoldenCase]):
    used = {t for c in cases for t in c.tags} & ADVERSARIAL_TAGS
    assert used == ADVERSARIAL_TAGS


# --- the CLI -----------------------------------------------------------------------------------


def test_cli_prints_the_metrics_and_exits_zero(capsys: pytest.CaptureFixture[str]):
    assert main(["--dataset", str(GOLDEN_DIR)]) == 0
    out = capsys.readouterr().out
    assert "precision" in out and "recall" in out and "F1" in out
    assert "non-adversarial (80)" in out and "all (100)" in out
    assert "justified: stand-alone ticket or hotel away from the base city" in out
    assert "UNJUSTIFIED" not in out


def test_cli_resolves_the_dataset_relative_to_the_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(tmp_path)
    assert main(["--dataset", "data/synth/golden", "--today", "2026-10-08"]) == 0
    assert "today pinned to 2026-10-08" in capsys.readouterr().out


def test_cli_fails_when_the_objective_is_missed(monkeypatch: pytest.MonkeyPatch):
    poor = GroupingReport(PairScores(10, 10, 10, 5), PairScores(10, 10, 10, 5), (), 3, 1)
    monkeypatch.setattr("claimpilot.claims.eval.evaluate_grouping", lambda *a, **k: poor)
    assert main(["--dataset", str(GOLDEN_DIR)]) == 1


def test_cli_fails_on_an_unjustified_stray(monkeypatch: pytest.MonkeyPatch):
    perfect = PairScores(10, 10, 10, 10)
    bad = GroupingReport(
        perfect, perfect, (Stray("x", "t", False, "pulled in by date and city"),), 3, 1
    )
    monkeypatch.setattr("claimpilot.claims.eval.evaluate_grouping", lambda *a, **k: bad)
    assert main(["--dataset", str(GOLDEN_DIR)]) == 1


def test_format_report_lists_strays_or_says_none():
    perfect = PairScores(4, 3, 3, 3)
    clean = format_report(GroupingReport(perfect, perfect, (), 2, 1), Path("d"), date(2026, 9, 30))
    assert clean.endswith("  none")
    flagged = format_report(
        GroupingReport(perfect, perfect, (Stray("s42-1", "Pune trip", False, "why"),), 2, 1),
        Path("d"),
        date(2026, 9, 30),
    )
    assert "s42-1  Pune trip  [UNJUSTIFIED: why]" in flagged


# --- the metric's own arithmetic ---------------------------------------------------------------


def test_pair_scores():
    scores = PairScores(documents=6, true_pairs=4, predicted_pairs=5, correct_pairs=3)
    assert scores.precision == pytest.approx(0.6)
    assert scores.recall == pytest.approx(0.75)
    assert scores.f1 == pytest.approx(2 * 0.6 * 0.75 / (0.6 + 0.75))


def test_pair_scores_with_nothing_to_find_are_perfect_by_convention():
    nothing = PairScores(documents=3, true_pairs=0, predicted_pairs=0, correct_pairs=0)
    assert (nothing.precision, nothing.recall, nothing.f1) == (1.0, 1.0, 1.0)


def test_pair_scores_with_no_overlap_have_zero_f1():
    miss = PairScores(documents=4, true_pairs=2, predicted_pairs=2, correct_pairs=0)
    assert (miss.precision, miss.recall, miss.f1) == (0.0, 0.0, 0.0)


def case(doc: ProcessedDocument, trip_id: str | None, *tags: str) -> GoldenCase:
    truth = ReceiptTruth(
        id=doc.id,
        receipt=doc.receipt,
        category=doc.category,
        tags=list(tags),
        persona_id="P001",
        trip_id=trip_id,
    )
    return GoldenCase(truth=truth, employee=employee("L3", BASE), split="dev", tags=tags)


def trip_cases() -> list[GoldenCase]:
    return [
        case(ticket("out", date(2026, 8, 12), BASE, "Nagpur"), "T1"),
        case(hotel("hotel", city="Nagpur", nights=2, checkout=date(2026, 8, 14)), "T1"),
        case(ticket("back", date(2026, 8, 14), "Nagpur", BASE), "T1"),
    ]


def test_a_perfectly_grouped_trip_scores_one():
    report = evaluate_grouping(trip_cases())
    assert (report.clean.precision, report.clean.recall) == (1.0, 1.0)
    assert report.clean.true_pairs == report.clean.predicted_pairs == 3
    assert report.strays == ()


def test_a_document_wrongly_pulled_into_a_trip_costs_precision():
    # the labels say this Nagpur meal is not part of the trip, but it is dated and bought inside it
    extra = case(meal("meal", date(2026, 8, 13), "Nagpur"), None)
    report = evaluate_grouping([*trip_cases(), extra])
    assert report.clean.recall == 1.0
    assert report.clean.precision == pytest.approx(3 / 6)
    assert [s.document_id for s in report.strays] == ["meal"]
    assert report.unjustified_strays[0].reason == "pulled in by date and city"


def test_a_document_missed_by_the_trip_costs_recall():
    # the labels say this cab in the base city belongs to the trip, but nothing ties it there
    missed = case(cab("cab", date(2026, 8, 13), BASE), "T1")
    report = evaluate_grouping([*trip_cases(), missed])
    assert report.clean.precision == 1.0
    assert report.clean.recall == pytest.approx(3 / 6)


def test_adversarial_documents_are_scored_separately():
    extra = case(meal("meal", date(2026, 8, 13), "Nagpur"), None, "duplicate")
    report = evaluate_grouping([*trip_cases(), extra])
    assert report.clean.documents == 3 and report.clean.precision == 1.0
    assert report.everything.documents == 4 and report.everything.precision < 1.0


def test_a_standalone_hotel_is_a_justified_stray():
    lone = case(hotel("lone", city="Goa", checkout=date(2026, 8, 20)), None, "over_policy")
    report = evaluate_grouping([lone])
    assert [(s.document_id, s.justified) for s in report.strays] == [("lone", True)]


def test_personas_are_grouped_separately():
    mine = trip_cases()
    other_truth = case(ticket("out2", date(2026, 8, 12), "Jaipur", "Delhi"), "T9")
    other = GoldenCase(
        truth=other_truth.truth.model_copy(update={"persona_id": "P002"}),
        employee=employee("L1", "Jaipur", emp_id="P002"),
        split="dev",
        tags=(),
    )
    report = evaluate_grouping([*mine, other])
    assert report.claims == 2 and report.trip_claims == 2
    assert report.clean.precision == 1.0


def test_claims_modes_in_the_mini_dataset():
    docs = [to_processed(c.truth) for c in trip_cases()]
    [claim] = group_documents(employee("L3", BASE), docs, today=CALIBRATION_TODAY)
    assert claim.mode is ClaimMode.trip
    assert {d.category for d in docs} == {
        ExpenseCategory.travel_domestic,
        ExpenseCategory.accommodation,
    }
