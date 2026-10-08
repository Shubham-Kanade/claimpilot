"""Claim-level policy rules: daily totals, per-trip limits, per-head caps, mobile totals, timing."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from test_policy_factories import TODAY, cab, claim_of, doc, employee, meal

from claimpilot.domain import (
    Claim,
    ClaimMode,
    DocType,
    ExpenseCategory,
    Finding,
    OpenQuestion,
    ProcessedDocument,
    QuestionKind,
    Severity,
)
from claimpilot.policy import Policy

POLICY = Policy.load()
DAY = date(2026, 8, 12)
TIER1_CITY, TIER2_CITY = "Mumbai", "Nagpur"


def check(claim: Claim, docs: list[ProcessedDocument], *, grade: str = "L3", today: date = TODAY):
    return POLICY.evaluate_claim(employee(grade, "Pune"), claim, docs, today=today)


def codes(findings: list[Finding]) -> list[str]:
    return [f.code for f in findings]


# --- 5.1 daily meals ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("city", "limit"), [(TIER1_CITY, 2000.0), (TIER2_CITY, 1500.0)], ids=["tier1", "tier2"]
)
def test_daily_meals_boundary(city: str, limit: float):
    at_limit = [meal("m1", DAY, city, limit)]
    assert check(claim_of(at_limit), at_limit) == []
    over = [meal("m1", DAY, city, limit + 0.01)]
    [finding] = check(claim_of(over), over)
    assert finding.code == "meals_over_limit"
    assert finding.severity is Severity.warn
    assert finding.clause_id == "5.1"
    assert (finding.expected, finding.actual) == (limit, limit + 0.01)


def test_daily_meals_add_up_across_bills():
    docs = [meal("m1", DAY, TIER2_CITY, 900.0), meal("m2", DAY, TIER2_CITY, 700.0)]
    [finding] = check(claim_of(docs), docs)
    assert finding.message == (
        "Meals on 12 Aug 2026 add up to ₹1,600 (2 bills), above the ₹1,500 daily limit "
        "for Tier-2 cities."
    )
    assert finding.document_id is None  # about the day, not one bill


def test_each_day_has_its_own_limit():
    docs = [
        meal("m1", DAY, TIER2_CITY, 1400.0),
        meal("m2", DAY + timedelta(days=1), TIER2_CITY, 1400.0),
    ]
    assert check(claim_of(docs), docs) == []


def test_only_the_days_over_the_limit_are_reported():
    docs = [
        meal("m1", DAY, TIER2_CITY, 1400.0),
        meal("m2", DAY + timedelta(days=1), TIER2_CITY, 1800.0),
    ]
    [finding] = check(claim_of(docs), docs)
    assert "13 Aug 2026" in finding.message


def test_a_day_spanning_both_tiers_gets_the_higher_limit():
    docs = [meal("m1", DAY, TIER1_CITY, 900.0), meal("m2", DAY, TIER2_CITY, 900.0)]
    assert check(claim_of(docs), docs) == []  # 1,800 is under the Tier-1 limit of 2,000


def test_meal_without_a_city_uses_the_claim_city_then_the_base_city():
    upi = doc("u1", ExpenseCategory.meals, doc_type=DocType.upi_payment, city=None, total=1700.0)
    assert check(claim_of([upi], city=TIER1_CITY), [upi]) == []  # Tier-1 claim city: limit 2,000
    assert codes(check(claim_of([upi], city=TIER2_CITY), [upi])) == ["meals_over_limit"]
    # no claim city: the base city decides (Pune is Tier-1)
    assert check(claim_of([upi]), [upi]) == []


def test_undated_meals_cannot_be_placed_on_a_day():
    docs = [meal("m1", None, TIER2_CITY, 5000.0)]
    assert check(claim_of(docs), docs) == []


def test_client_entertainment_is_not_a_meal():
    dinner = doc("d1", ExpenseCategory.client_entertainment, city=TIER2_CITY, total=9000.0)
    assert "meals_over_limit" not in codes(check(claim_of([dinner]), [dinner]))


def test_foreign_currency_meals_are_not_added_to_rupee_limits():
    docs = [
        meal("m1", DAY, TIER2_CITY, 1400.0),
        doc("m2", total=900.0, currency="USD", city=TIER2_CITY),
    ]
    assert check(claim_of(docs), docs) == []


# --- 5.2 client entertainment per head ----------------------------------------------------------


def with_attendees(
    claim: Claim, document_id: str, answer: str | None, *, kind=QuestionKind.attendees
) -> Claim:
    question = OpenQuestion(
        id="q-attendees", kind=kind, text="Who attended?", document_ids=[document_id], answer=answer
    )
    return claim.model_copy(update={"open_questions": [question]})


def dinner(total: float = 9000.0, **kw) -> ProcessedDocument:
    return doc("d1", ExpenseCategory.client_entertainment, total=total, city=TIER1_CITY, **kw)


def test_per_head_cap_boundary():
    docs = [dinner(10000.0)]
    claim = with_attendees(claim_of(docs), "d1", "4 people")  # exactly 2,500 each
    assert check(claim, docs) == []
    over = [dinner(10000.04)]
    [finding] = check(with_attendees(claim_of(over), "d1", "4 people"), over)
    assert finding.code == "entertainment_over_cap"
    assert finding.severity is Severity.warn
    assert finding.clause_id == "5.2"
    assert (finding.expected, finding.actual) == (2500.0, 2500.01)
    assert finding.document_id == "d1"
    assert finding.message == (
        "This client entertainment costs ₹2,500.01 per head (₹10,000.04 for 4 people, alcohol "
        "excluded), above the ₹2,500 per-head cap."
    )


def test_names_are_counted_with_the_employee():
    docs = [dinner(9000.0)]
    claim = with_attendees(
        claim_of(docs), "d1", "Rahul Mehra and Anita Rao"
    )  # 3 people: 3,000 each
    [finding] = check(claim, docs)
    assert finding.actual == 3000.0


def test_no_attendee_answer_means_no_per_head_check():
    docs = [dinner(90000.0)]
    assert check(claim_of(docs), docs) == []
    assert check(with_attendees(claim_of(docs), "d1", None), docs) == []
    assert check(with_attendees(claim_of(docs), "d1", "   "), docs) == []


@pytest.mark.parametrize("answer", ["n/a", "unknown", "1000", "?"])
def test_an_uncountable_answer_asks_finance_to_look_instead_of_passing(answer):
    docs = [dinner(90000.0)]
    [finding] = check(with_attendees(claim_of(docs), "d1", answer), docs)
    assert finding.code == "headcount_unclear" and finding.severity is Severity.warn
    assert finding.clause_id == "5.2" and finding.document_id == "d1"
    assert answer not in finding.message  # the answer is not echoed back


def test_only_the_attendees_question_for_that_document_counts():
    docs = [dinner(90000.0)]
    purpose = with_attendees(claim_of(docs), "d1", "2 people", kind=QuestionKind.business_purpose)
    assert check(purpose, docs) == []
    elsewhere = with_attendees(claim_of(docs), "someone-else", "2 people")
    assert check(elsewhere, docs) == []


def test_alcohol_is_taken_out_before_dividing():
    docs = [dinner(12000.0, items=[("Veg Thali", 3000.0), ("Draught Beer 330ml", 2000.0)])]
    claim = with_attendees(claim_of(docs), "d1", "4 people")
    assert check(claim, docs) == []  # (12,000 - 2,000) / 4 = 2,500


def test_a_calendar_answer_gives_the_count():
    docs = [dinner(9000.0)]
    answer = "from calendar: Client dinner - Orion Retail (attendees: Rahul Mehra, Anita Rao)"
    [finding] = check(with_attendees(claim_of(docs), "d1", answer), docs)
    assert finding.actual == 3000.0


def test_per_head_check_skips_other_categories():
    docs = [doc("d1", ExpenseCategory.meals, total=90000.0)]
    assert "entertainment_over_cap" not in codes(
        check(with_attendees(claim_of(docs), "d1", "2"), docs)
    )


# --- 7.3 local travel on a trip ----------------------------------------------------------------


def trip(docs: list[ProcessedDocument], city: str) -> Claim:
    return claim_of(docs, mode=ClaimMode.trip, city=city)


@pytest.mark.parametrize(
    ("city", "limit"), [(TIER1_CITY, 3000.0), (TIER2_CITY, 2500.0)], ids=["tier1", "tier2"]
)
def test_trip_local_travel_boundary(city: str, limit: float):
    docs = [cab("c1", DAY, city, limit / 2), cab("c2", DAY, city, limit / 2)]
    assert check(trip(docs, city), docs) == []
    docs = [cab("c1", DAY, city, limit / 2), cab("c2", DAY, city, limit / 2 + 0.01)]
    [finding] = check(trip(docs, city), docs)
    assert finding.code == "trip_conveyance_over_limit"
    assert finding.severity is Severity.warn
    assert finding.clause_id == "7.3"
    assert (finding.expected, finding.actual) == (limit, limit + 0.01)


def test_trip_local_travel_message():
    docs = [cab("c1", DAY, TIER2_CITY, 1500.0), cab("c2", DAY, TIER2_CITY, 1200.0)]
    [finding] = check(trip(docs, TIER2_CITY), docs)
    assert finding.message == (
        "Local travel on 12 Aug 2026 adds up to ₹2,700 (2 receipts), above the ₹2,500 daily "
        "limit for Tier-2 cities."
    )


def test_trip_local_travel_is_per_day():
    docs = [
        cab("c1", DAY, TIER2_CITY, 2000.0),
        cab("c2", DAY + timedelta(days=1), TIER2_CITY, 2000.0),
    ]
    assert check(trip(docs, TIER2_CITY), docs) == []


def test_the_local_travel_limit_only_applies_to_trips():
    docs = [cab("c1", DAY, TIER2_CITY, 9000.0)]
    assert check(claim_of(docs, mode=ClaimMode.period), docs) == []
    assert check(claim_of(docs, mode=ClaimMode.event), docs) == []


def test_only_local_conveyance_counts_towards_the_trip_limit():
    docs = [cab("c1", DAY, TIER2_CITY, 2000.0), meal("m1", DAY, TIER2_CITY, 1000.0)]
    assert check(trip(docs, TIER2_CITY), docs) == []


# --- 8.1 mobile, several bills in one month -----------------------------------------------------


def mobile(doc_id: str, day: str, total: float) -> ProcessedDocument:
    return doc(
        doc_id, ExpenseCategory.mobile_internet, doc_type=DocType.mobile_bill, day=day, total=total
    )


def test_two_bills_in_a_month_are_added_up():
    docs = [mobile("m1", "2026-08-05", 1500.0), mobile("m2", "2026-08-20", 1200.0)]
    [finding] = check(claim_of(docs), docs, grade="L1")  # cap 2,000
    assert finding.code == "mobile_over_cap"
    assert finding.message == (
        "Mobile and internet bills for Aug 2026 add up to ₹2,700 (2 bills), above the ₹2,000 "
        "monthly cap for grade L1."
    )
    assert (finding.expected, finding.actual) == (2000.0, 2700.0)


def test_two_bills_at_the_cap_are_fine():
    docs = [mobile("m1", "2026-08-05", 1000.0), mobile("m2", "2026-08-20", 1000.0)]
    assert check(claim_of(docs), docs, grade="L1") == []


def test_bills_in_different_months_are_not_combined():
    docs = [mobile("m1", "2026-07-05", 1500.0), mobile("m2", "2026-08-20", 1500.0)]
    assert check(claim_of(docs), docs, grade="L1") == []


def test_a_bill_already_over_the_cap_is_not_reported_twice():
    docs = [mobile("m1", "2026-08-05", 2600.0), mobile("m2", "2026-08-20", 500.0)]
    assert check(claim_of(docs), docs, grade="L1") == []  # the document-level rule owns it
    assert codes(POLICY.evaluate_document(employee("L1"), docs[0], today=TODAY)) == [
        "mobile_over_cap"
    ]


def test_a_single_bill_is_left_to_the_document_rule():
    docs = [mobile("m1", "2026-08-05", 2600.0)]
    assert check(claim_of(docs), docs, grade="L1") == []


def test_mobile_total_for_an_unknown_grade_is_not_repeated_per_claim():
    docs = [mobile("m1", "2026-08-05", 9000.0), mobile("m2", "2026-08-20", 9000.0)]
    assert check(claim_of(docs), docs, grade="Q9") == []


def test_undated_mobile_bills_cannot_be_placed_in_a_month():
    docs = [mobile("m1", "", 9000.0), mobile("m2", "", 9000.0)]
    docs = [
        d.model_copy(update={"receipt": d.receipt.model_copy(update={"date": None})}) for d in docs
    ]
    assert check(claim_of(docs), docs, grade="L1") == []


# --- 2.1 submission window ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days_after_end", "late_by"),
    [(0, None), (89, None), (90, None), (91, 1), (92, 2), (200, 110)],
)
def test_submission_window_boundary(days_after_end: int, late_by: int | None):
    end = date(2026, 7, 1)
    docs = [meal("m1", end, TIER1_CITY, 300.0)]
    findings = check(claim_of(docs, end=end), docs, today=end + timedelta(days=days_after_end))
    if late_by is None:
        assert findings == []
        return
    [finding] = findings
    assert finding.code == "late_submission"
    assert finding.severity is Severity.warn
    assert finding.clause_id == "2.1"
    assert finding.expected == 90
    assert finding.actual == days_after_end
    assert f"{late_by} day" in finding.message


def test_late_message_is_singular_for_one_day():
    end = date(2026, 7, 1)
    docs = [meal("m1", end, TIER1_CITY, 300.0)]
    [finding] = check(claim_of(docs, end=end), docs, today=end + timedelta(days=91))
    assert finding.message == (
        "The last expense on this claim was on 01 Jul 2026, 91 days ago. The window is 90 days, "
        "so the claim is 1 day late."
    )


def test_the_window_runs_from_the_claims_last_day_not_its_first():
    docs = [
        meal("m1", date(2026, 6, 1), TIER1_CITY, 300.0),
        meal("m2", date(2026, 8, 1), TIER1_CITY, 300.0),
    ]
    claim = claim_of(docs, start=date(2026, 6, 1), end=date(2026, 8, 1))
    assert check(claim, docs, today=date(2026, 10, 15)) == []  # 75 days after 1 Aug


def test_dates_come_from_the_documents_when_the_claim_has_none():
    docs = [meal("m1", date(2026, 3, 1), TIER1_CITY, 300.0)]
    assert codes(check(claim_of(docs), docs, today=TODAY)) == ["late_submission"]


def test_a_claim_with_no_dates_at_all_cannot_be_late():
    docs = [meal("m1", None, TIER1_CITY, 300.0)]
    assert check(claim_of(docs), docs, today=date(2030, 1, 1)) == []


def test_today_defaults_to_the_clock():
    old = date.today() - timedelta(days=400)
    docs = [meal("m1", old, TIER1_CITY, 300.0)]
    findings = POLICY.evaluate_claim(employee(), claim_of(docs, end=old), docs)
    assert codes(findings) == ["late_submission"]


# --- evaluate_claim in general -----------------------------------------------------------------


def test_evaluate_claim_does_not_repeat_document_level_findings():
    docs = [doc("c1", ExpenseCategory.learning, total=40000.0, items=[("Course", 40000.0)])]
    assert POLICY.evaluate_document(employee(), docs[0], today=TODAY) != []
    assert check(claim_of(docs), docs) == []


def test_documents_outside_the_claim_are_ignored():
    inside = meal("m1", DAY, TIER2_CITY, 900.0)
    stray = meal("m2", DAY, TIER2_CITY, 900.0)
    assert check(claim_of([inside]), [inside, stray]) == []  # 900 alone is fine; 1,800 would not be


def test_claim_findings_cite_their_clause():
    docs = [meal("m1", DAY, TIER2_CITY, 5000.0)]
    [finding] = check(claim_of(docs), docs)
    assert finding.source.value == "policy"
    assert finding.clause_text == POLICY.clause("5.1").text


def test_with_no_city_anywhere_a_meal_gets_the_tier_two_limit():
    nowhere = employee("L3", "")
    upi = doc("u1", ExpenseCategory.meals, doc_type=DocType.upi_payment, city=None, total=1700.0)
    findings = POLICY.evaluate_claim(nowhere, claim_of([upi]), [upi], today=TODAY)
    assert codes(findings) == ["meals_over_limit"]
    assert findings[0].expected == 1500.0
