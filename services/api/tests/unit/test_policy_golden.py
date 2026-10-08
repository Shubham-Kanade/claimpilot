"""Calibration of the policy against the synthetic golden set.

The objective, reproducible on every run: with ``today`` pinned to the calibration date,

(a) NO ``high`` policy finding on any golden document without an adversarial tag, and
(b) EVERY ``over_policy`` document gets at least one ``high`` policy finding,

and (c) per-code precision and recall are reported (``python -m claimpilot.policy.report``).
The first two are asserted here directly from ``evaluate_document``, not through the report
module, so the report cannot grade itself.
"""

from __future__ import annotations

from datetime import date
from itertools import pairwise
from pathlib import Path

import pytest
import yaml
from test_policy_factories import clause_as

from claimpilot.claims import build_claims
from claimpilot.claims.eval import ADVERSARIAL_TAGS, CALIBRATION_TODAY
from claimpilot.domain import Severity
from claimpilot.evals.golden import GOLDEN_DIR, GoldenCase, has_alcohol, load_golden, to_processed
from claimpilot.policy import Policy
from claimpilot.policy.report import evaluate_policy, expected_findings, format_report, main
from claimpilot.policy.rules import HotelCapClause, MobileCapClause

pytestmark = pytest.mark.skipif(not GOLDEN_DIR.exists(), reason="golden dataset not generated")

POLICY = Policy.load()


@pytest.fixture(scope="module")
def cases() -> list[GoldenCase]:
    return load_golden()


def is_clean(case: GoldenCase) -> bool:
    return not (ADVERSARIAL_TAGS & set(case.tags))


def high_codes(case: GoldenCase) -> list[str]:
    findings = POLICY.evaluate_document(
        case.employee, to_processed(case.truth), today=CALIBRATION_TODAY
    )
    return [f.code for f in findings if f.severity is Severity.high]


# --- the objective -----------------------------------------------------------------------------


def test_the_golden_set_has_the_expected_shape(cases: list[GoldenCase]):
    assert len(cases) == 100
    assert sum(is_clean(c) for c in cases) == 80
    assert sum("over_policy" in c.tags for c in cases) == 4


def test_a_no_high_finding_on_any_non_adversarial_document(cases: list[GoldenCase]):
    offenders = {c.truth.id: high_codes(c) for c in cases if is_clean(c) and high_codes(c)}
    assert offenders == {}


def test_b_every_over_policy_document_gets_a_high_finding(cases: list[GoldenCase]):
    over_policy = [c for c in cases if "over_policy" in c.tags]
    assert over_policy
    missed = [c.truth.id for c in over_policy if not high_codes(c)]
    assert missed == []


def test_over_policy_documents_are_caught_for_the_right_reason(cases: list[GoldenCase]):
    for case in cases:
        if "over_policy" not in case.tags:
            continue
        expected = (
            "hotel_over_cap"
            if case.truth.receipt.doc_type.value == "hotel_folio"
            else ("alcohol_not_reimbursable")
        )
        assert expected in high_codes(case), case.truth.id


def test_alcohol_flags_are_exactly_the_documents_with_alcohol_lines(cases: list[GoldenCase]):
    flagged = {c.truth.id for c in cases if "alcohol_not_reimbursable" in high_codes(c)}
    assert flagged == {c.truth.id for c in cases if has_alcohol(c.truth)}


def test_no_claim_level_finding_is_high(cases: list[GoldenCase]):
    by_persona: dict[str, list[GoldenCase]] = {}
    for case in cases:
        by_persona.setdefault(case.employee.id, []).append(case)
    for members in by_persona.values():
        docs = [to_processed(c.truth) for c in members]
        for claim in build_claims(members[0].employee, docs, POLICY, today=CALIBRATION_TODAY):
            claim_level = [f for f in claim.findings if f.document_id is None]
            assert all(f.severity is not Severity.high for f in claim_level)


# --- precision and recall per code -------------------------------------------------------------


@pytest.fixture(scope="module")
def report(cases: list[GoldenCase]):
    return evaluate_policy(cases, POLICY)


def test_report_objectives_pass(report):
    assert report.passed
    assert report.high_on_clean == ()
    assert report.over_policy_flagged == report.over_policy_total == 4
    assert (report.documents, report.clean_documents) == (100, 80)


def test_labelled_codes_have_perfect_precision_and_recall(report):
    rows = {r.code: r for r in report.rows}
    for code in (
        "alcohol_not_reimbursable",
        "hotel_over_cap",
        "receipt_required",
        "preapproval_required",
    ):
        row = rows[code]
        assert (row.precision, row.recall) == (1.0, 1.0), code
        assert row.flagged == row.tp > 0


def test_no_code_has_a_false_positive_or_a_miss(report):
    assert [(r.code, r.fp, r.fn) for r in report.rows if r.fp or r.fn] == []


def test_codes_without_labels_report_n_a(report):
    unlabelled = [r for r in report.rows if r.labels == "none in dataset"]
    assert {r.code for r in unlabelled} >= {
        "meals_over_limit",
        "mobile_over_cap",
        "late_submission",
    }
    assert all(r.precision is None and r.recall is None for r in unlabelled)


def test_expected_findings_labels(cases: list[GoldenCase]):
    expected = {c.truth.id: expected_findings(c, POLICY) for c in cases}
    by_code: dict[str, int] = {}
    for found in expected.values():
        for code in found:
            by_code[code] = by_code.get(code, 0) + 1
    assert by_code == {
        "hotel_over_cap": 2,
        "alcohol_not_reimbursable": 2,
        "receipt_required": 2,  # the two undated documents above the receipt threshold
        "preapproval_required": 3,  # courses above 10,000
    }


# --- the calendar: today decides lateness ------------------------------------------------------


def test_with_the_real_date_the_july_claims_are_late(cases: list[GoldenCase]):
    quarter_end = {r.code: r for r in evaluate_policy(cases, POLICY, today=CALIBRATION_TODAY).rows}
    october = {r.code: r for r in evaluate_policy(cases, POLICY, today=date(2026, 10, 8)).rows}
    assert quarter_end["late_submission"].flagged == 0
    assert october["late_submission"].flagged > 0
    assert (
        october["late_submission"].fp == october["late_submission"].flagged
    )  # nothing labels them


# --- the numbers are plausible and fit the generator -------------------------------------------

# Copied from data/synth/synthgen (personas.HOTEL_BUDGET, adversarial.OVER_POLICY_HOTEL_TARIFF and
# the mobile plan catalogue): the dataset's own idea of a legitimate and an over-policy bill.
GENERATOR_HOTEL_BAND = {
    "L1": (2200, 4200),
    "L2": (3000, 5500),
    "L3": (4200, 7500),
    "L4": (5500, 8800),
    "L5": (7000, 9800),
}
GENERATOR_OVER_POLICY_FROM = 10_500
GENERATOR_MAX_MOBILE_BILL = (999 + 299 + 199 + 140) * 1.18  # plan + two add-ons + usage, with GST


def test_hotel_caps_admit_the_generators_legitimate_tariffs_but_not_its_over_policy_ones():
    caps = clause_as(POLICY, HotelCapClause, "4.1").params.nightly_cap
    for grade, (_, top) in GENERATOR_HOTEL_BAND.items():
        for tier_cap in (caps[grade].tier1, caps[grade].tier2):
            assert top <= tier_cap < GENERATOR_OVER_POLICY_FROM, grade


def test_hotel_caps_are_plausible_for_a_real_policy():
    caps = clause_as(POLICY, HotelCapClause, "4.1").params.nightly_cap
    grades = list(caps)
    for grade in grades:
        assert caps[grade].tier1 >= caps[grade].tier2  # metros are not cheaper
    for junior, senior in pairwise(grades):
        assert caps[junior].tier1 < caps[senior].tier1  # seniority never lowers the cap
        assert caps[junior].tier2 < caps[senior].tier2


def test_mobile_caps_admit_every_bill_the_generator_can_print():
    caps = clause_as(POLICY, MobileCapClause, "8.1").params.monthly_cap
    assert min(caps.values()) >= GENERATOR_MAX_MOBILE_BILL
    assert list(caps.values()) == sorted(caps.values())  # non-decreasing with grade


# --- the CLI -----------------------------------------------------------------------------------


def test_report_prints_the_table_and_exits_zero(capsys: pytest.CaptureFixture[str]):
    assert main(["--dataset", str(GOLDEN_DIR)]) == 0
    out = capsys.readouterr().out
    assert "Orion Demo Corp Travel & Expense Policy v3 (synthetic)" in out
    assert "precision" in out and "recall" in out
    for code in ("hotel_over_cap", "alcohol_not_reimbursable", "late_submission"):
        assert code in out
    assert "(a) high findings on non-adversarial documents: 0 of 80  PASS" in out
    assert "(b) over_policy documents with a high finding: 4 of 4  PASS" in out


def test_report_resolves_the_dataset_relative_to_the_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(tmp_path)  # nothing called data/synth/golden here
    assert main(["--dataset", "data/synth/golden"]) == 0
    assert "100 documents (80 without an adversarial tag)" in capsys.readouterr().out


def test_report_fails_when_the_policy_flags_clean_documents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    raw = yaml.safe_load(Path(POLICY_PATH).read_text(encoding="utf-8"))
    hotel_clause = next(c for c in raw["clauses"] if c["id"] == "4.1")
    for caps in hotel_clause["params"]["nightly_cap"].values():
        caps["tier1"] = caps["tier2"] = 1000  # absurdly tight: every real hotel night is over
    tight = tmp_path / "tight.yaml"
    tight.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert main(["--dataset", str(GOLDEN_DIR), "--policy", str(tight)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_report_fails_when_over_policy_documents_slip_through(tmp_path: Path):
    raw = yaml.safe_load(Path(POLICY_PATH).read_text(encoding="utf-8"))
    hotel_clause = next(c for c in raw["clauses"] if c["id"] == "4.1")
    for caps in hotel_clause["params"]["nightly_cap"].values():
        caps["tier1"] = caps["tier2"] = 50000  # nothing is ever over
    loose = tmp_path / "loose.yaml"
    loose.write_text(yaml.safe_dump(raw), encoding="utf-8")
    result = evaluate_policy(load_golden(), Policy.load(loose))
    assert not result.passed
    assert result.over_policy_flagged < result.over_policy_total
    assert "FAIL" in format_report(result, GOLDEN_DIR, CALIBRATION_TODAY)


POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "policy.yaml"
