"""Document-level policy rules: each clause's positive, negative and boundary cases.

Expected values come from the policy file's own parameters where a table is involved, so a
calibration change in policy.yaml moves the boundaries and the tests together.
"""

from __future__ import annotations

from datetime import date

import pytest
from test_policy_factories import TODAY, clause_as, doc, employee, hotel, ticket

from claimpilot.domain import (
    DocType,
    ExpenseCategory,
    Finding,
    FindingSource,
    Severity,
    TaxBreakup,
)
from claimpilot.policy import Policy, PolicyCode, format_inr
from claimpilot.policy.rules import HotelCapClause, MobileCapClause, TrainClassClause

POLICY = Policy.load()
HOTEL = clause_as(POLICY, HotelCapClause, "4.1")
MOBILE = clause_as(POLICY, MobileCapClause, "8.1")
TRAIN = clause_as(POLICY, TrainClassClause, "7.2")

TIER1_CITY, TIER2_CITY = "Mumbai", "Nagpur"


def check(document, grade: str = "L3", base: str = "Pune") -> list[Finding]:
    return POLICY.evaluate_document(employee(grade, base), document, today=TODAY)


def codes(findings: list[Finding]) -> list[str]:
    return [f.code for f in findings]


# --- every finding cites its clause -----------------------------------------------------------


def test_findings_cite_the_clause_and_the_document():
    [finding] = check(hotel("h9", rate=12000.0), grade="L4")
    assert finding.source is FindingSource.policy
    assert finding.clause_id == "4.1"
    assert finding.clause_text == POLICY.clause("4.1").text
    assert finding.document_id == "h9"
    assert finding.code == PolicyCode.hotel_over_cap.value


def test_a_clean_document_has_no_findings():
    assert check(doc(total=450.0)) == []


# --- 3.1 receipts -----------------------------------------------------------------------------


def test_receipt_with_everything_printed_is_fine():
    assert check(doc(total=5000.0)) == []


@pytest.mark.parametrize(
    ("changes", "fields"),
    [
        ({"day": None}, ("date",)),
        ({"merchant": None}, ("merchant_name",)),
        ({"merchant": "   "}, ("merchant_name",)),
        ({"total": None}, ("total",)),
        ({"day": None, "merchant": None}, ("date", "merchant_name")),
        ({"day": None, "merchant": None, "total": None}, ("total", "date", "merchant_name")),
    ],
)
def test_receipt_above_threshold_missing_evidence_is_flagged(changes: dict, fields: tuple):
    findings = check(doc(**{"total": 651.0, **changes}))
    assert codes(findings) == ["receipt_required"]
    assert findings[0].severity is Severity.warn
    assert findings[0].clause_id == "3.1"
    assert findings[0].fields == fields
    assert "₹200" in findings[0].message


def test_receipt_message_names_what_is_missing():
    [finding] = check(doc(total=651.0, day=None, merchant=None))
    assert finding.message.endswith("but this one has no date and merchant.")
    assert finding.actual == "missing date and merchant"


@pytest.mark.parametrize(("total", "flagged"), [(199.99, False), (200.0, False), (200.01, True)])
def test_receipt_threshold_boundary(total: float, flagged: bool):
    assert bool(check(doc(total=total, day=None))) is flagged


def test_small_expense_without_any_evidence_is_not_flagged():
    assert check(doc(total=90.0, day=None, merchant=None)) == []


def test_unreadable_date_counts_as_missing():
    assert codes(check(doc(total=500.0, day="12/08/2026"))) == ["receipt_required"]


def test_non_rupee_receipt_missing_a_date_is_flagged_whatever_the_amount():
    findings = check(doc(total=20.0, day=None, currency="USD"))
    assert "receipt_required" in codes(findings)


# --- 4.1 accommodation ------------------------------------------------------------------------


def test_hotel_message_matches_the_documented_example():
    [finding] = check(hotel(rate=9501.0, city=TIER1_CITY), grade="L4")
    assert (
        finding.message
        == "Hotel night ₹9,501 exceeds the ₹9,500 cap for grade L4 in a Tier-1 city."
    )
    assert finding.severity is Severity.high
    assert (finding.expected, finding.actual) == (9500.0, 9501.0)
    assert finding.fields == ("line_items",)


@pytest.mark.parametrize("grade", list(HOTEL.params.nightly_cap))
@pytest.mark.parametrize(("city", "tier"), [(TIER1_CITY, "tier1"), (TIER2_CITY, "tier2")])
def test_hotel_cap_boundary_for_every_grade_and_tier(grade: str, city: str, tier: str):
    cap = getattr(HOTEL.params.nightly_cap[grade], tier)
    assert check(hotel(rate=cap, city=city), grade=grade) == []
    assert codes(check(hotel(rate=cap + 1, city=city), grade=grade)) == ["hotel_over_cap"]


def test_tier_two_cap_is_lower_for_the_same_room():
    room = hotel(rate=9000.0, city=TIER1_CITY)
    assert check(room, grade="L4") == []
    cheap_city = hotel(rate=9000.0, city=TIER2_CITY)
    [finding] = check(cheap_city, grade="L4")
    assert "Tier-2" in finding.message and finding.expected == 8800.0


def test_cap_is_on_the_room_rate_not_the_gst_inclusive_total():
    # ₹8,550 a night is under the L4 Tier-1 cap even though the bill total is above ₹10,000.
    room = hotel(rate=8550.0, gst=0.18, city="Hyderabad")
    assert room.amount > 10000
    assert check(room, grade="L4") == []


def test_only_the_nights_over_the_cap_count():
    room = doc(
        "h1",
        ExpenseCategory.accommodation,
        doc_type=DocType.hotel_folio,
        city=TIER1_CITY,
        total=30000.0,
        items=[("Room Charges 10-Aug", 7000.0), ("Room Charges 11-Aug", 9600.0),
               ("Room Charges 12-Aug", 9700.0)],
    )  # fmt: skip
    [finding] = check(room, grade="L4")
    assert finding.actual == 9700.0
    assert "(2 of 3 nights are over)" in finding.message


def test_every_night_is_checked_not_the_average():
    room = doc(
        "h1",
        ExpenseCategory.accommodation,
        doc_type=DocType.hotel_folio,
        city=TIER1_CITY,
        items=[("Room Charges 10-Aug", 2000.0), ("Room Charges 11-Aug", 9900.0)],
    )
    assert codes(check(room, grade="L4")) == ["hotel_over_cap"]  # average 5,950 would pass


def test_room_service_and_laundry_are_not_counted_against_the_cap():
    room = doc(
        "h1",
        ExpenseCategory.accommodation,
        doc_type=DocType.hotel_folio,
        city=TIER1_CITY,
        items=[("Room Charges 10-Aug", 6000.0), ("Room Service", 4500.0), ("Laundry", 900.0)],
    )
    assert check(room, grade="L4") == []


def test_a_multi_night_line_is_divided_by_its_nights():
    room = doc(
        "h1",
        ExpenseCategory.accommodation,
        doc_type=DocType.hotel_folio,
        city=TIER1_CITY,
        items=[("Room charges (2 nights)", 19000.0)],
    )
    assert check(room, grade="L4") == []
    assert codes(check(room, grade="L3")) == ["hotel_over_cap"]  # L3 cap is 8,000


def test_hotel_without_line_items_says_nights_are_unclear_and_only_warns():
    room = doc(
        "h1",
        ExpenseCategory.accommodation,
        doc_type=DocType.hotel_folio,
        city=TIER2_CITY,
        items=(),
        subtotal=20000.0,
        total=23600.0,
        taxes=TaxBreakup(cgst=1800.0, sgst=1800.0),
    )
    [finding] = check(room, grade="L4")
    assert finding.severity is Severity.warn
    assert "does not say how many nights" in finding.message
    assert (finding.expected, finding.actual) == (8800.0, 20000.0)


def test_a_missing_hotel_city_uses_the_generous_tier_one_cap_and_says_so():
    room = hotel(rate=9000.0, city=None)
    assert check(room, grade="L4") == []  # 9,000 is over the Tier-2 cap but under Tier-1
    [finding] = check(hotel(rate=9600.0, city=None), grade="L4")
    assert "no city" in finding.message and finding.expected == 9500.0


def test_hotel_is_recognised_by_category_even_if_the_type_is_other():
    room = doc(
        "h1",
        ExpenseCategory.accommodation,
        doc_type=DocType.other,
        city=TIER1_CITY,
        items=[("Room Charges 10-Aug", 12000.0)],
    )
    assert codes(check(room, grade="L4")) == ["hotel_over_cap"]


def test_grade_is_normalised():
    assert codes(check(hotel(rate=9600.0, city=TIER1_CITY), grade=" l4 ")) == ["hotel_over_cap"]


def test_unknown_grade_says_so_instead_of_skipping_silently():
    [finding] = check(hotel(rate=50000.0, city=TIER1_CITY), grade="M2")
    assert finding.code == "grade_not_in_policy"
    assert finding.severity is Severity.info
    assert finding.clause_id == "4.1"


def test_non_hotel_documents_never_get_a_hotel_finding():
    assert check(doc(items=[("Room Charges", 99999.0)], total=99999.0)) == []


def test_foreign_currency_hotel_is_not_compared_with_rupee_caps():
    room = hotel(rate=500.0, city=TIER1_CITY)
    room = room.model_copy(update={"receipt": room.receipt.model_copy(update={"currency": "USD"})})
    findings = check(room, grade="L1")
    assert codes(findings) == ["currency_not_inr"]
    assert findings[0].severity is Severity.info


# --- 6.1 alcohol ------------------------------------------------------------------------------


def dinner(**kw):
    return doc(
        "b1",
        ExpenseCategory.client_entertainment,
        total=kw.pop("total", 4102.0),
        items=kw.pop("items", [("Veg Thali", 910.0), ("Draught Beer 330ml", 840.0),
                               ("House Red Wine (Glass)", 1120.0)]),
        **kw,
    )  # fmt: skip


def test_alcohol_line_items_are_flagged_with_the_amount_to_deduct():
    [finding] = check(dinner())
    assert finding.code == "alcohol_not_reimbursable"
    assert finding.severity is Severity.high
    assert finding.clause_id == "6.1"
    assert finding.expected == 1960.0  # beer + wine
    assert finding.actual == 4102.0  # the bill total
    assert "2 lines" in finding.message and "₹1,960" in finding.message


def test_alcohol_message_does_not_echo_item_text():
    [finding] = check(dinner())
    assert "Beer" not in finding.message and "Wine" not in finding.message


def test_one_alcohol_line_is_singular():
    [finding] = check(dinner(items=[("Whisky 60ml", 600.0)], total=600.0))
    assert "1 line," in finding.message


@pytest.mark.parametrize(
    ("probability", "flagged"), [(0.0, False), (0.49, False), (0.5, True), (1.0, True)]
)
def test_alcohol_decision_threshold(probability: float, flagged: bool):
    findings = check(doc(items=[("Mystery drink", 100.0)], total=100.0, alcohol=probability))
    assert bool(findings) is flagged


def test_decision_without_line_items_flags_but_cannot_say_how_much():
    [finding] = check(doc(total=900.0, alcohol=0.9))
    assert finding.expected is None
    assert finding.actual == 900.0
    assert "appears to include alcohol" in finding.message


def test_both_signals_give_one_finding_not_two():
    assert codes(check(dinner(alcohol=1.0))) == ["alcohol_not_reimbursable"]


def test_a_line_item_alone_is_enough_even_when_the_decision_says_no():
    assert codes(check(dinner(alcohol=0.0))) == ["alcohol_not_reimbursable"]


def test_soft_drinks_are_not_alcohol():
    findings = check(doc(items=[("Ginger Beer", 90.0), ("Non-Alcoholic Wine", 200.0)], total=290.0))
    assert findings == []


def test_alcohol_on_a_hotel_folio_minibar_is_caught_too():
    room = doc(
        "h1",
        ExpenseCategory.accommodation,
        doc_type=DocType.hotel_folio,
        city=TIER1_CITY,
        items=[("Room Charges 10-Aug", 5000.0), ("Mini bar - Beer", 400.0)],
    )
    findings = check(room, grade="L4")
    assert codes(findings) == ["alcohol_not_reimbursable"]  # the room itself is within its cap
    assert findings[0].expected == 400.0


# --- 7.1 / 7.2 ticket class -------------------------------------------------------------------


def flight(*lines: tuple[str, float]):
    return ticket("f1", date(2026, 8, 12), "Pune", "Delhi", items=lines)


@pytest.mark.parametrize(
    "printed", ["Business Class", "business", "First Class", "Premium Economy"]
)
def test_non_economy_flight_is_flagged(printed: str):
    [finding] = check(flight((f"Base Fare ({printed})", 9000.0)))
    assert finding.code == "air_class_not_economy"
    assert finding.severity is Severity.high
    assert finding.clause_id == "7.1"
    assert finding.expected == "economy"


def test_economy_flight_is_fine():
    assert check(flight(("Base Fare - Economy", 3000.0))) == []


def test_flight_without_a_printed_class_is_skipped():
    assert check(flight(("Base Fare", 3000.0), ("Convenience Fee", 300.0))) == []


def test_business_word_on_a_non_flight_is_ignored():
    assert check(doc(items=[("Business lunch", 900.0)], total=900.0)) == []


def train(text: str):
    return ticket("t1", date(2026, 8, 12), "Pune", "Mumbai", kind=DocType.train_ticket,
                  items=[(text, 900.0)])  # fmt: skip


@pytest.mark.parametrize("grade", list(TRAIN.params.max_class_by_grade))
def test_train_class_boundary_for_every_grade(grade: str):
    order = TRAIN.params.class_order
    allowed = TRAIN.params.max_class_by_grade[grade]
    assert check(train(f"Ticket Fare ({allowed})"), grade=grade) == []
    higher = order[order.index(allowed) + 1 :]
    if higher:
        [finding] = check(train(f"Ticket Fare ({higher[0]})"), grade=grade)
        assert finding.code == "train_class_above_grade"
        assert finding.severity is Severity.warn
        assert (finding.expected, finding.actual) == (allowed, higher[0])


def test_train_class_message_names_both_classes():
    [finding] = check(train("Ticket Fare (1A)"), grade="L3")
    assert finding.message == (
        "The train ticket is 1A (First AC), but grade L3 is allowed up to 2A (Second AC)."
    )


def test_lower_class_than_allowed_is_fine():
    assert check(train("Ticket Fare (SL)"), grade="L5") == []


def test_train_without_a_printed_class_is_skipped():
    assert check(train("Ticket Fare"), grade="L1") == []


def test_train_class_for_an_unknown_grade_is_reported():
    assert codes(check(train("Ticket Fare (1A)"), grade="X9")) == ["grade_not_in_policy"]


def test_train_rule_ignores_flights():
    assert check(flight(("Fare (3A)", 3000.0)), grade="L1") == []


# --- 8.1 mobile --------------------------------------------------------------------------------


def mobile(total: float, doc_id: str = "m1", day: str = "2026-08-06"):
    return doc(doc_id, ExpenseCategory.mobile_internet, doc_type=DocType.mobile_bill, total=total,
               day=day, merchant="Tarang Telecom")  # fmt: skip


@pytest.mark.parametrize("grade", list(MOBILE.params.monthly_cap))
def test_mobile_cap_boundary_for_every_grade(grade: str):
    cap = MOBILE.params.monthly_cap[grade]
    assert check(mobile(cap), grade=grade) == []
    [finding] = check(mobile(cap + 0.01), grade=grade)
    assert finding.code == "mobile_over_cap"
    assert finding.severity is Severity.warn
    assert (finding.expected, finding.actual) == (cap, cap + 0.01)


def test_mobile_message_has_the_numbers():
    [finding] = check(mobile(2600.0), grade="L1")
    assert finding.message == (
        "This mobile or internet bill is ₹2,600, above the ₹2,000 monthly cap for grade L1."
    )


def test_mobile_cap_unknown_grade_is_reported():
    assert codes(check(mobile(99999.0), grade="Z")) == ["grade_not_in_policy"]


# --- 9.1 learning ------------------------------------------------------------------------------


def course(total: float, category: ExpenseCategory = ExpenseCategory.learning):
    return doc("c1", category, doc_type=DocType.gst_invoice, total=total, merchant="LearnSphere")


@pytest.mark.parametrize(
    ("total", "flagged"), [(9999.0, False), (10000.0, False), (10000.01, True)]
)
def test_learning_preapproval_boundary(total: float, flagged: bool):
    assert bool(check(course(total))) is flagged


def test_learning_finding_details():
    [finding] = check(course(22420.0))
    assert finding.code == "preapproval_required"
    assert finding.severity is Severity.warn
    assert finding.clause_id == "9.1"
    assert (finding.expected, finding.actual) == (10000.0, 22420.0)
    assert "₹22,420" in finding.message and "₹10,000" in finding.message


def test_only_learning_needs_preapproval():
    assert check(course(40000.0, ExpenseCategory.conference)) == []
    assert check(course(40000.0, ExpenseCategory.wfh_supplies)) == []


# --- 10.1 personal -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("probability", "flagged"), [(0.0, False), (0.49, False), (0.5, True), (0.99, True)]
)
def test_personal_expense_threshold(probability: float, flagged: bool):
    findings = check(doc(total=126.0, personal=probability))
    assert bool(findings) is flagged
    if flagged:
        assert findings[0].code == "personal_expense_flagged"
        assert findings[0].severity is Severity.warn
        assert findings[0].clause_id == "10.1"


# --- evaluate_document in general --------------------------------------------------------------


def test_findings_come_in_policy_order():
    messy = dinner(day=None, personal=0.9)
    assert codes(check(messy)) == [
        "receipt_required",  # 3.1
        "alcohol_not_reimbursable",  # 6.1
        "personal_expense_flagged",  # 10.1
    ]


def test_non_rupee_document_gets_one_housekeeping_note():
    findings = check(doc(total=100.0, currency="EUR"))
    assert codes(findings) == ["currency_not_inr"]
    assert findings[0].document_id == "d1" and findings[0].source is FindingSource.policy


def test_evaluation_is_deterministic_and_pure():
    document = dinner(personal=0.9)
    assert check(document) == check(document)
    assert document.findings == []  # the document is not touched


def test_money_in_messages_uses_indian_grouping():
    assert format_inr(150000) == "₹1,50,000"
    [finding] = check(course(150000.0))
    assert "₹1,50,000" in finding.message


def test_a_hotel_bill_with_nothing_to_compare_is_not_flagged():
    empty = doc("h1", ExpenseCategory.accommodation, doc_type=DocType.hotel_folio, total=None)
    assert "hotel_over_cap" not in codes(check(empty, grade="L4"))
    only_extras = doc(
        "h2",
        ExpenseCategory.accommodation,
        doc_type=DocType.hotel_folio,
        items=[("Laundry", 900.0), ("Room Service", 4000.0)],
    )
    assert check(only_extras, grade="L4") == []
