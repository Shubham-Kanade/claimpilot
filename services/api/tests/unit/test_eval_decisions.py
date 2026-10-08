from __future__ import annotations

import pytest

from claimpilot.decisions import DecisionError, DecisionResult
from claimpilot.decisions.types import Answer
from claimpilot.evals.decisions import (
    PERSONAL_PROBES,
    BudgetExceededError,
    EngineReport,
    evaluate_engine,
    expected_calibration_error,
    render_markdown,
    write_report,
)
from claimpilot.evals.golden import GOLDEN_DIR, has_alcohol, load_golden

pytestmark = pytest.mark.skipif(not GOLDEN_DIR.exists(), reason="golden dataset not generated")


class OracleEngine:
    """Answers from ground truth, optionally wrong on every Nth document."""

    name = "oracle"

    def __init__(self, cases, *, wrong_every: int = 0, confidence: float = 0.9, cost: float = 0.0):
        self._by_state = {}
        self._cases = cases
        self._wrong_every, self._confidence, self._cost = wrong_every, confidence, cost
        self._n = 0

    async def decide(self, state, questions):
        case = next(
            c for c in self._cases if c.truth.receipt.merchant_name == state.get("merchant")
        )
        self._n += 1
        wrong = self._wrong_every and self._n % self._wrong_every == 0
        label = "misc" if wrong else case.truth.category.value
        alc = 1.0 if has_alcohol(case.truth) else 0.0
        return DecisionResult(
            answers={
                "category": Answer(
                    key="category",
                    kind="choice",
                    value=label,
                    confidence=self._confidence,
                    engine="oracle",
                ),
                "alcohol_present": Answer(
                    key="alcohol_present", kind="noul", value=alc, confidence=1.0, engine="oracle"
                ),
                "personal_expense": Answer(
                    key="personal_expense", kind="noul", value=0.0, confidence=1.0, engine="oracle"
                ),
            },
            engine="oracle",
            latency_ms=5,
            cost_usd=self._cost,
        )


def unique_cases(n=12):
    seen, out = set(), []
    for c in load_golden():
        name = c.truth.receipt.merchant_name
        if name and name not in seen:
            seen.add(name)
            out.append(c)
        if len(out) == n:
            break
    return out


async def test_perfect_engine_scores_perfectly():
    cases = unique_cases()
    report = await evaluate_engine(OracleEngine(cases), cases, probes=())
    assert report.documents == len(cases) and report.failures == 0
    assert report.category_accuracy == 1.0 and report.alcohol_accuracy == 1.0
    assert report.personal_false_positive_rate == 0.0
    assert report.calibration_error == pytest.approx(0.1)  # 0.9 confident, 100% right


async def test_wrong_answers_are_counted_and_listed():
    cases = unique_cases()
    report = await evaluate_engine(
        OracleEngine(cases, wrong_every=4, confidence=0.99), cases, probes=()
    )
    assert 0 < report.category_accuracy < 1
    assert report.category_errors and all(p == "misc" for _, _, p, _ in report.category_errors)
    assert report.calibration_error > 0.1  # overconfident: wrong while claiming 0.99


async def test_engine_failures_are_counted_not_raised():
    class Down:
        name = "down"

        async def decide(self, state, questions):
            raise DecisionError("offline")

    cases = unique_cases(3)
    report = await evaluate_engine(Down(), cases, probes=())
    assert report.failures == 3 and report.documents == 0 and report.category_accuracy == 0.0


async def test_budget_guard_stops_the_run():
    cases = unique_cases(6)
    with pytest.raises(BudgetExceededError):
        await evaluate_engine(
            OracleEngine(cases, cost=1.0), cases, concurrency=1, budget_usd=0.5, probes=()
        )


def test_expected_calibration_error():
    assert expected_calibration_error([]) == 0.0
    assert expected_calibration_error([(1.0, True)] * 5) == 0.0
    assert expected_calibration_error([(0.9, True), (0.9, False)]) == pytest.approx(0.4)


def test_empty_report_properties_do_not_divide_by_zero():
    r = EngineReport(engine="x")
    assert r.category_accuracy == r.alcohol_accuracy == r.cost_per_1k == r.mean_confidence == 0.0
    assert r.alcohol_recall is None


async def test_markdown_report_lists_engines_and_errors():
    cases = unique_cases()
    r1 = await evaluate_engine(OracleEngine(cases), cases, probes=())
    r2 = await evaluate_engine(OracleEngine(cases, wrong_every=3), cases, probes=())
    md = render_markdown([r1, r2], {"label": "t", "date": "d", "documents": len(cases)})
    assert md.count("| oracle |") == 2 and "category errors" in md and "ECE" in md


async def test_write_report_creates_dated_file(tmp_path):
    cases = unique_cases(4)
    report = await evaluate_engine(OracleEngine(cases), cases, probes=())
    path = write_report(
        tmp_path / "reports", [report], {"label": "t", "date": "2026-10-08", "documents": 4}
    )
    assert path.name == "2026-10-08-t.md" and "| oracle |" in path.read_text("utf-8")


class ProbeEngine:
    """Flags every document as personal, so recall on the probes is 100% and FP is 100%."""

    name = "probe"

    async def decide(self, state, questions):
        answers = {
            q.key: Answer(key=q.key, kind="noul", value=1.0, confidence=1.0, engine="probe")
            for q in questions
        }
        return DecisionResult(answers=answers, engine="probe", latency_ms=1)


def test_personal_probes_are_data_driven():
    assert len(PERSONAL_PROBES) == 8
    assert all(p["items"] and p["merchant"] for p in PERSONAL_PROBES)


async def test_probe_recall_is_measured():
    report = await evaluate_engine(ProbeEngine(), [])
    assert report.probes == 8 and report.personal_recall == 1.0


async def test_selective_accuracy_and_coverage():
    cases = unique_cases()
    report = await evaluate_engine(
        OracleEngine(cases, wrong_every=4, confidence=0.9), cases, probes=()
    )
    assert report.coverage == 1.0 and report.confident_accuracy == report.category_accuracy
    unsure = await evaluate_engine(
        OracleEngine(cases, wrong_every=4, confidence=0.3), cases, probes=()
    )
    assert unsure.coverage == 0.0 and unsure.confident_accuracy == 0.0


async def test_probe_failures_are_skipped_not_raised():
    class Down:
        name = "down"

        async def decide(self, state, questions):
            raise DecisionError("offline")

    report = await evaluate_engine(Down(), [])
    assert report.probes == 0 and report.personal_recall is None
