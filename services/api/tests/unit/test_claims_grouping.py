"""Pile-to-claim grouping: trips, events, periods and the awkward cases between them."""

from __future__ import annotations

import random
import re
from datetime import date, timedelta

import pytest
from test_policy_factories import TODAY, cab, doc, employee, hotel, meal, ticket

from claimpilot.claims import group_documents
from claimpilot.claims.labels import EN_DASH
from claimpilot.domain import (
    Claim,
    ClaimMode,
    ClaimStatus,
    DocType,
    ExpenseCategory,
    ProcessedDocument,
)

BASE = "Pune"
EMP = employee("L3", BASE)


def d(day: int, month: int = 8) -> date:
    return date(2026, month, day)


def group(docs: list[ProcessedDocument], **kw) -> list[Claim]:
    return group_documents(kw.pop("emp", EMP), docs, today=TODAY, **kw)


def trips(claims: list[Claim]) -> list[Claim]:
    return [c for c in claims if c.mode is ClaimMode.trip]


def only(claims: list[Claim], mode: ClaimMode) -> Claim:
    [found] = [c for c in claims if c.mode is mode]
    return found


def nagpur_trip(
    start: int = 12, nights: int = 2, month: int = 8, tag: str = ""
) -> list[ProcessedDocument]:
    """Out ticket, a cab and two meals in Nagpur, the hotel folio, and the ticket back."""
    first, last = d(start, month), d(start + nights, month)
    return [
        ticket(f"{tag}out", first, BASE, "Nagpur"),
        cab(f"{tag}cab", first, "Nagpur"),
        meal(f"{tag}meal1", first, "Nagpur"),
        meal(f"{tag}meal2", first + timedelta(days=1), "Nagpur"),
        hotel(f"{tag}hotel", rate=3500.0, nights=nights, city="Nagpur", checkout=last),
        ticket(f"{tag}back", last, "Nagpur", BASE),
    ]


# --- one trip ---------------------------------------------------------------------------------


def test_a_trip_gathers_tickets_hotel_cab_and_meals():
    docs = nagpur_trip()
    [claim] = group(docs)
    assert claim.mode is ClaimMode.trip
    assert set(claim.document_ids) == {x.id for x in docs}
    assert claim.status is ClaimStatus.draft
    assert claim.employee_id == "P001"
    assert claim.city == "Nagpur"
    assert (claim.start_date, claim.end_date) == (d(12), d(14))
    assert claim.total == round(sum(x.amount for x in docs), 2)
    assert claim.currency == "INR"
    assert claim.findings == [] and claim.open_questions == []


def test_trip_title_has_the_city_and_the_date_range():
    [claim] = group(nagpur_trip())
    assert claim.title == f"Nagpur trip 12{EN_DASH}14 Aug 2026"


def test_documents_are_listed_by_date_then_id():
    [claim] = group(nagpur_trip())
    # Aug 12: cab, meal1, out; Aug 13: meal2; Aug 14 (checkout): back, hotel
    assert claim.document_ids == ["cab", "meal1", "out", "meal2", "back", "hotel"]


def test_hotel_dates_come_from_its_nights_not_just_checkout():
    docs = [hotel("h1", rate=3000.0, nights=3, city="Nagpur", checkout=d(14))]
    [claim] = group(docs)
    assert (claim.start_date, claim.end_date) == (d(11), d(14))
    assert claim.title == f"Nagpur trip 11{EN_DASH}14 Aug 2026"


def test_a_single_day_trip_has_a_single_date_in_its_title():
    docs = [ticket("t1", d(12), BASE, "Nagpur"), ticket("t2", d(12), "Nagpur", BASE)]
    [claim] = group(docs)
    assert claim.title == "Nagpur trip 12 Aug 2026"


def test_city_names_are_matched_loosely():
    emp = employee("L3", "Bangalore")
    docs = [
        ticket("t1", d(12), "Bengaluru", "Nagpur"),
        ticket("t2", d(13), "NAGPUR", "Bengaluru, KA"),
    ]
    [claim] = group(docs, emp=emp)
    assert claim.mode is ClaimMode.trip and claim.city == "Nagpur"


# --- what joins a trip, and what must not -----------------------------------------------------


def test_base_city_documents_stay_out_of_the_trip():
    docs = [
        *nagpur_trip(),
        meal("base-meal", d(13), BASE),  # dinner at home during the trip
        cab("base-cab", d(12), BASE),  # a local cab at home
        doc(
            "fuel",
            ExpenseCategory.fuel_vehicle,
            doc_type=DocType.fuel_slip,
            day="2026-08-13",
            city=BASE,
        ),
    ]
    claims = group(docs)
    [trip] = trips(claims)
    assert {"base-meal", "base-cab", "fuel"}.isdisjoint(trip.document_ids)
    assert any("base-meal" in c.document_ids and c.mode is ClaimMode.period for c in claims)


def test_documents_without_a_city_do_not_join_a_trip():
    upi = doc(
        "upi", ExpenseCategory.meals, doc_type=DocType.upi_payment, day="2026-08-13", city=None
    )
    claims = group([*nagpur_trip(), upi])
    assert "upi" not in trips(claims)[0].document_ids


def test_a_meal_in_another_city_during_the_window_stays_out():
    claims = group([*nagpur_trip(), meal("mumbai-meal", d(13), "Mumbai")])
    assert "mumbai-meal" not in trips(claims)[0].document_ids


@pytest.mark.parametrize(
    ("day", "joins"),
    [(9, False), (10, False), (11, True), (12, True), (14, True), (15, True), (16, False)],
)
def test_the_window_is_the_anchor_dates_plus_or_minus_one_day(day: int, joins: bool):
    docs = [
        ticket("out", d(12), BASE, "Nagpur"),
        ticket("back", d(14), "Nagpur", BASE),
        meal("edge", d(day), "Nagpur"),
    ]
    [trip] = trips(group(docs))
    assert ("edge" in trip.document_ids) is joins


def test_an_airport_transfer_at_home_joins_the_trip():
    docs = [
        *nagpur_trip(),
        cab("to-airport", d(12), BASE, items=(("Airport Entry Charge", 150.0),)),
        cab("plain-cab", d(12), BASE),
    ]
    [trip] = trips(group(docs))
    assert "to-airport" in trip.document_ids
    assert "plain-cab" not in trip.document_ids


def test_a_station_transfer_is_recognised_from_the_receipt_text():
    docs = [
        *nagpur_trip(),
        cab("to-station", d(14), None, items=(("Railway Station pickup", 120.0),)),
    ]
    [trip] = trips(group(docs))
    assert "to-station" in trip.document_ids


def test_only_meals_cabs_and_fuel_follow_a_trip():
    other = doc("misc", ExpenseCategory.misc, day="2026-08-13", city="Nagpur", total=300.0)
    wfh = doc("wfh", ExpenseCategory.wfh_supplies, day="2026-08-13", city="Nagpur", total=900.0)
    claims = group([*nagpur_trip(), other, wfh])
    assert {"misc", "wfh"}.isdisjoint(trips(claims)[0].document_ids)


def test_fuel_bought_in_the_trip_city_joins_the_trip():
    fuel = doc(
        "fuel",
        ExpenseCategory.fuel_vehicle,
        doc_type=DocType.fuel_slip,
        day="2026-08-13",
        city="Nagpur",
    )
    [trip] = trips(group([*nagpur_trip(), fuel]))
    assert "fuel" in trip.document_ids


# --- hotels, tickets and who counts as away ---------------------------------------------------


def test_a_hotel_in_the_base_city_is_not_a_trip():
    claims = group([hotel("h1", city=BASE, checkout=d(14))])
    assert [c.mode for c in claims] == [ClaimMode.period]
    assert claims[0].title == "Accommodation Aug 2026"


def test_a_ticket_between_two_base_city_points_is_not_a_trip():
    claims = group([ticket("t1", d(12), BASE, BASE)])
    assert [c.mode for c in claims] == [ClaimMode.period]


def test_a_ticket_with_no_endpoints_is_still_a_trip_anchor():
    claims = group([ticket("t1", d(12), "", "")])
    assert [c.mode for c in claims] == [ClaimMode.trip]
    assert claims[0].title.startswith("Business trip")


def test_a_hotel_only_trip_pulls_in_meals_from_its_first_night():
    docs = [
        hotel("h1", rate=3500.0, nights=3, city="Nagpur", checkout=d(14)),
        meal("first-night", d(11), "Nagpur"),
        meal("outside", d(9), "Nagpur"),
    ]
    [trip] = trips(group(docs))
    assert "first-night" in trip.document_ids and "outside" not in trip.document_ids


def test_a_multi_city_trip_is_one_claim():
    docs = [
        ticket("t1", d(3), BASE, "Nagpur"),
        ticket("t2", d(5), "Nagpur", "Jaipur"),
        ticket("t3", d(7), "Jaipur", BASE),
        meal("m1", d(4), "Nagpur"),
        meal("m2", d(6), "Jaipur"),
    ]
    [claim] = group(docs)
    assert claim.mode is ClaimMode.trip and len(claim.document_ids) == 5
    assert claim.title == f"Nagpur & Jaipur trip 3{EN_DASH}7 Aug 2026"
    assert claim.city == "Nagpur"


def test_three_cities_are_abbreviated_in_the_title():
    docs = [
        ticket("t1", d(3), BASE, "Nagpur"),
        ticket("t2", d(4), "Nagpur", "Jaipur"),
        ticket("t3", d(5), "Jaipur", "Indore"),
        ticket("t4", d(6), "Indore", BASE),
    ]
    [claim] = group(docs)
    assert claim.title == f"Nagpur +2 more trip 3{EN_DASH}6 Aug 2026"


# --- several trips ----------------------------------------------------------------------------


def test_two_trips_in_one_month_to_different_cities_are_two_claims():
    jaipur = [
        ticket("j-out", d(20), BASE, "Jaipur"),
        hotel("j-hotel", rate=3000.0, nights=2, city="Jaipur", checkout=d(22)),
        ticket("j-back", d(22), "Jaipur", BASE),
        meal("j-meal", d(21), "Jaipur"),
    ]
    claims = trips(group([*nagpur_trip(3), *jaipur]))
    assert len(claims) == 2
    assert [c.city for c in claims] == ["Nagpur", "Jaipur"]
    assert {i for c in claims for i in c.document_ids} == {x.id for x in [*nagpur_trip(3), *jaipur]}
    assert claims[0].end_date and claims[1].start_date
    assert claims[0].end_date < claims[1].start_date


def test_two_trips_to_the_same_city_stay_apart_when_the_traveller_came_home():
    claims = trips(group([*nagpur_trip(3, tag="a-"), *nagpur_trip(18, tag="b-")]))
    assert len(claims) == 2
    assert all(len(c.document_ids) == 6 for c in claims)


def test_back_to_back_trips_with_a_same_day_turnaround_are_separate():
    docs = [
        ticket("a-out", d(3), BASE, "Nagpur", time="09:00"),
        ticket("a-back", d(5), "Nagpur", BASE, time="08:00"),
        ticket("b-out", d(5), BASE, "Nagpur", time="18:30"),  # leaves again that evening
        ticket("b-back", d(7), "Nagpur", BASE, time="20:00"),
    ]
    claims = trips(group(docs))
    assert [set(c.document_ids) for c in claims] == [{"a-out", "a-back"}, {"b-out", "b-back"}]


def test_the_turnaround_follows_departure_times_not_ids():
    docs = [
        ticket("z-back", d(5), "Nagpur", BASE, time="07:45"),  # sorts last by id, departs first
        ticket("a-out", d(3), BASE, "Nagpur", time="09:00"),
        ticket("b-out", d(5), BASE, "Nagpur", time="21:10"),
        ticket("c-back", d(7), "Nagpur", BASE, time="15:00"),
    ]
    claims = trips(group(docs))
    assert [set(c.document_ids) for c in claims] == [{"a-out", "z-back"}, {"b-out", "c-back"}]


def test_a_same_day_round_trip_is_one_trip_with_or_without_times():
    plain = [ticket("t1", d(12), BASE, "Nagpur"), ticket("t2", d(12), "Nagpur", BASE)]
    timed = [
        ticket("t1", d(12), "Nagpur", BASE, time="19:00"),
        ticket("t2", d(12), BASE, "Nagpur", time="08:00"),
    ]
    assert len(trips(group(plain))) == 1
    assert len(trips(group(timed))) == 1


def test_a_second_departure_without_a_return_starts_a_new_trip():
    docs = [ticket("o1", d(3), BASE, "Nagpur"), ticket("o2", d(6), BASE, "Nagpur")]
    assert len(trips(group(docs))) == 2


def test_a_trip_with_no_hotel_stays_open_between_the_tickets():
    docs = [ticket("out", d(3), BASE, "Nagpur"), ticket("back", d(15), "Nagpur", BASE)]
    [claim] = group(docs)  # 12 days apart, but the traveller had not come home in between
    assert set(claim.document_ids) == {"out", "back"}


def test_an_open_trip_does_not_stay_open_forever():
    docs = [ticket("out", d(3), BASE, "Nagpur"), ticket("back", d(25), "Nagpur", BASE)]
    assert len(trips(group(docs))) == 2


def test_a_hotel_in_another_city_does_not_join_an_open_trip():
    docs = [ticket("out", d(3), BASE, "Nagpur"), hotel("h1", city="Jaipur", checkout=d(5))]
    assert len(trips(group(docs))) == 2


# --- month boundaries --------------------------------------------------------------------------


def test_a_trip_can_cross_a_month_boundary():
    first, last = date(2026, 7, 30), date(2026, 8, 2)
    docs = [
        ticket("out", first, BASE, "Nagpur"),
        meal("m1", date(2026, 7, 31), "Nagpur"),
        meal("m2", date(2026, 8, 1), "Nagpur"),
        hotel("h1", rate=3500.0, nights=3, city="Nagpur", checkout=last),
        ticket("back", last, "Nagpur", BASE),
    ]
    [claim] = group(docs)
    assert len(claim.document_ids) == 5
    assert (claim.start_date, claim.end_date) == (first, last)
    assert claim.title == f"Nagpur trip 30 Jul{EN_DASH}2 Aug 2026"


def test_a_trip_can_cross_a_year_boundary():
    first, last = date(2026, 12, 30), date(2027, 1, 2)
    docs = [
        ticket("out", first, BASE, "Nagpur"),
        hotel("h1", rate=3500.0, nights=3, city="Nagpur", checkout=last),
        ticket("back", last, "Nagpur", BASE),
    ]
    [claim] = group(docs)
    assert claim.title == f"Nagpur trip 30 Dec 2026{EN_DASH}2 Jan 2027"
    assert claim.start_date == first


def test_period_claims_split_at_the_month_boundary():
    docs = [
        cab("c1", date(2026, 7, 31), BASE),
        cab("c2", date(2026, 8, 1), BASE),
        cab("c3", date(2026, 8, 15), BASE),
    ]
    claims = group(docs)
    assert sorted(c.title for c in claims) == [
        "Local conveyance Aug 2026",
        "Local conveyance Jul 2026",
    ]
    assert {c.title: len(c.document_ids) for c in claims}["Local conveyance Aug 2026"] == 2


# --- events and periods -----------------------------------------------------------------------


def test_each_client_dinner_is_its_own_event_claim():
    docs = [
        doc("d1", ExpenseCategory.client_entertainment, day="2026-08-14", city=BASE, total=4000.0),
        doc("d2", ExpenseCategory.client_entertainment, day="2026-08-14", city=BASE, total=3000.0),
        doc("d3", ExpenseCategory.client_entertainment, day="2026-08-20", city=BASE, total=2000.0),
    ]
    claims = group(docs)
    assert [c.mode for c in claims] == [ClaimMode.event] * 3
    assert [c.title for c in claims].count("Client dinner 14 Aug 2026") == 2
    assert all(len(c.document_ids) == 1 for c in claims)


def test_a_client_dinner_in_the_trip_city_is_still_its_own_claim():
    dinner = doc(
        "din", ExpenseCategory.client_entertainment, day="2026-08-13", city="Nagpur", total=4000.0
    )
    claims = group([*nagpur_trip(), dinner])
    assert "din" not in trips(claims)[0].document_ids
    assert only(claims, ClaimMode.event).document_ids == ["din"]


def test_learning_and_conference_documents_are_events_too():
    docs = [
        doc("l1", ExpenseCategory.learning, doc_type=DocType.gst_invoice, day="2026-07-18",
            city="Bengaluru", total=22420.0),
        doc("c1", ExpenseCategory.conference, day="2026-09-02", city="Goa", total=15000.0),
    ]  # fmt: skip
    claims = group(docs)
    assert [(c.title, c.mode) for c in claims] == [
        ("Learning 18 Jul 2026", ClaimMode.event),
        ("Conference 2 Sep 2026", ClaimMode.event),
    ]
    assert claims[0].city == BASE  # a course supplier's city says nothing about where we were
    assert claims[1].city == "Goa"


def test_local_conveyance_fuel_and_mobile_are_monthly_period_claims():
    docs = [
        cab("c1", d(3), BASE),
        cab("c2", d(20), BASE),
        doc(
            "f1",
            ExpenseCategory.fuel_vehicle,
            doc_type=DocType.fuel_slip,
            day="2026-08-05",
            city=BASE,
        ),
        doc(
            "m1",
            ExpenseCategory.mobile_internet,
            doc_type=DocType.mobile_bill,
            day="2026-08-06",
            city=BASE,
        ),
    ]
    claims = group(docs)
    assert {c.title for c in claims} == {
        "Local conveyance Aug 2026",
        "Fuel Aug 2026",
        "Mobile & internet Aug 2026",
    }
    assert all(c.mode is ClaimMode.period for c in claims)
    conveyance = next(c for c in claims if c.title.startswith("Local"))
    assert (conveyance.start_date, conveyance.end_date) == (d(3), d(20))
    assert conveyance.city == BASE


def test_the_modal_merchant_city_labels_a_period_claim():
    docs = [cab("c1", d(3), "Nagpur"), cab("c2", d(4), "Nagpur"), cab("c3", d(5), "Pune")]
    [claim] = group(docs)  # no trip anchors: these are just cabs
    assert claim.city == "Nagpur"


def test_leftover_categories_get_period_claims_with_a_readable_title():
    docs = [
        doc("w1", ExpenseCategory.wfh_supplies, day="2026-08-11", city=BASE, total=1270.0),
        doc("x1", ExpenseCategory.misc, day="2026-08-12", city=BASE, total=300.0),
        doc("r1", ExpenseCategory.medical, day="2026-08-13", city=BASE, total=800.0),
    ]
    assert {c.title for c in group(docs)} == {
        "WFH supplies Aug 2026",
        "Miscellaneous Aug 2026",
        "Medical Aug 2026",
    }


# --- missing dates -----------------------------------------------------------------------------


def test_an_undated_meal_in_the_only_trip_city_joins_that_trip():
    [trip] = trips(group([*nagpur_trip(), meal("undated", None, "Nagpur")]))
    assert "undated" in trip.document_ids


def test_an_undated_meal_in_a_city_two_trips_visited_is_not_guessed():
    docs = [*nagpur_trip(3, tag="a-"), *nagpur_trip(18, tag="b-"), meal("undated", None, "Nagpur")]
    claims = group(docs)
    assert all("undated" not in c.document_ids for c in trips(claims))


def test_an_undated_meal_with_no_city_is_a_period_document():
    claims = group([*nagpur_trip(), meal("undated", None, None)])
    assert "undated" not in trips(claims)[0].document_ids


def test_an_undated_fuel_slip_joins_the_newest_month_of_its_category():
    docs = [
        doc(
            "f1",
            ExpenseCategory.fuel_vehicle,
            doc_type=DocType.fuel_slip,
            day="2026-07-04",
            city=BASE,
        ),
        doc(
            "f2",
            ExpenseCategory.fuel_vehicle,
            doc_type=DocType.fuel_slip,
            day="2026-08-18",
            city=BASE,
        ),
        doc("f3", ExpenseCategory.fuel_vehicle, doc_type=DocType.fuel_slip, day=None, city=BASE),
    ]
    claims = {c.title: c for c in group(docs)}
    assert set(claims["Fuel Aug 2026"].document_ids) == {"f2", "f3"}
    assert claims["Fuel Jul 2026"].document_ids == ["f1"]


def test_an_undated_document_alone_gets_a_date_needed_claim():
    claims = group([doc("u1", ExpenseCategory.fuel_vehicle, day=None, city=BASE, total=3000.0)])
    [claim] = claims
    assert claim.title == "Fuel (date needed)"
    assert (claim.start_date, claim.end_date) == (None, None)
    assert claim.total == 3000.0


def test_an_undated_document_of_a_new_category_follows_the_newest_dated_month():
    docs = [
        meal("m1", d(5), BASE),
        doc("u1", ExpenseCategory.mobile_internet, day=None, city=BASE, total=999.0),
    ]
    claims = {c.title: c for c in group(docs)}
    assert set(claims) == {"Meals Aug 2026", "Mobile & internet (date needed)"}


def test_an_undated_hotel_joins_the_trip_to_its_city():
    undated = hotel("h-undated", city="Nagpur", checkout=d(14)).model_copy()
    undated = undated.model_copy(
        update={"receipt": undated.receipt.model_copy(update={"date": None})}
    )
    [trip] = trips(
        group(
            [ticket("out", d(12), BASE, "Nagpur"), ticket("back", d(14), "Nagpur", BASE), undated]
        )
    )
    assert "h-undated" in trip.document_ids


def test_an_undated_ticket_with_no_matching_trip_is_a_trip_of_its_own():
    [claim] = group([ticket("t1", None, BASE, "Nagpur")])
    assert claim.mode is ClaimMode.trip
    assert claim.title == "Nagpur trip (dates needed)"
    assert claim.start_date is None


def test_an_undated_ticket_to_an_unknown_place_is_its_own_trip():
    docs = [ticket("t1", d(12), BASE, "Nagpur"), ticket("t2", None, BASE, "Jaipur")]
    claims = trips(group(docs))
    assert sorted(c.title for c in claims) == [
        "Jaipur trip (dates needed)",
        "Nagpur trip 12 Aug 2026",
    ]


def test_an_undated_dinner_is_an_event_that_needs_a_date():
    [claim] = group([doc("d1", ExpenseCategory.client_entertainment, day=None, city=BASE)])
    assert claim.title == "Client dinner (date needed)"


# --- identity and ordering ---------------------------------------------------------------------


def test_claim_ids_are_deterministic_and_independent_of_input_order():
    docs = [*nagpur_trip(), meal("base", d(20), BASE), cab("c9", d(21), BASE)]
    shuffled = list(docs)
    random.Random(7).shuffle(shuffled)
    assert group(docs) == group(shuffled)


def test_claim_id_has_the_documented_shape():
    [claim] = group(nagpur_trip())
    assert re.fullmatch(r"clm-P001-[0-9a-f]{10}", claim.id)


def test_claim_id_depends_on_the_documents_and_the_prefix():
    a, b = group(nagpur_trip())[0], group(nagpur_trip(10, tag="x-"))[0]
    assert a.id != b.id
    [custom] = group(nagpur_trip(), id_prefix="draft")
    assert custom.id.startswith("draft-P001-") and custom.id.split("-")[-1] == a.id.split("-")[-1]


def test_claim_id_includes_the_employee():
    other = employee("L3", BASE, emp_id="P777")
    [claim] = group(nagpur_trip(), emp=other)
    assert claim.id.startswith("clm-P777-") and claim.employee_id == "P777"


def test_claims_are_ordered_by_start_date():
    docs = [cab("late", d(25), BASE), *nagpur_trip(3), meal("early", d(1), BASE)]
    starts = [c.start_date for c in group(docs) if c.start_date]
    assert len(starts) == 3 and starts == sorted(starts)


def test_nothing_in_nothing_out():
    assert group([]) == []


def test_a_document_passed_twice_counts_once():
    docs = nagpur_trip()
    [claim] = group([*docs, *docs])
    assert len(claim.document_ids) == len(docs)
    assert claim.total == round(sum(x.amount for x in docs), 2)


def test_every_document_lands_in_exactly_one_claim():
    docs = [
        *nagpur_trip(),
        meal("base", d(20), BASE),
        cab("c9", d(21), BASE),
        doc("din", ExpenseCategory.client_entertainment, day="2026-08-14", city=BASE),
        doc("u1", ExpenseCategory.fuel_vehicle, day=None, city=BASE),
        ticket("lone", None, BASE, "Goa"),
    ]
    placed = [i for c in group(docs) for i in c.document_ids]
    assert sorted(placed) == sorted(x.id for x in docs)


def test_a_missing_total_counts_as_zero():
    docs = [meal("m1", d(5), BASE, 300.0), meal("m2", d(6), BASE, 0.0)]
    docs[1] = docs[1].model_copy(
        update={"receipt": docs[1].receipt.model_copy(update={"total": None})}
    )
    [claim] = group(docs)
    assert claim.total == 300.0


def test_a_single_foreign_currency_is_kept():
    usd = doc(
        "u1", ExpenseCategory.meals, day="2026-08-05", city="Austin", total=40.0, currency="USD"
    )
    [claim] = group([usd])
    assert claim.currency == "USD"


def test_mixed_currencies_fall_back_to_rupees():
    docs = [
        doc("u1", ExpenseCategory.meals, day="2026-08-05", city=BASE, total=40.0, currency="USD"),
        doc("i1", ExpenseCategory.meals, day="2026-08-05", city=BASE, total=400.0),
    ]
    [claim] = group(docs)
    assert claim.currency == "INR"


def test_grouping_is_pure_and_uses_no_clock_when_today_is_given():
    docs = nagpur_trip()
    before = [x.model_copy(deep=True) for x in docs]
    group(docs)
    assert docs == before


def test_today_defaults_to_the_clock():
    claims = group_documents(EMP, [doc("u1", ExpenseCategory.fuel_vehicle, day=None, city=BASE)])
    assert len(claims) == 1


# --- reading a hotel folio's nights ------------------------------------------------------------


def folio(*labels: str, checkout: date = date(2026, 8, 14)) -> ProcessedDocument:
    return doc(
        "h1",
        ExpenseCategory.accommodation,
        doc_type=DocType.hotel_folio,
        day=checkout.isoformat(),
        city="Nagpur",
        total=7000.0,
        items=[(label, 3500.0) for label in labels],
    )


def test_room_lines_without_dates_give_the_nights_by_counting():
    [claim] = group([folio("Room Charges", "Room Charges", "Room Charges")])
    assert claim.start_date == d(11)  # three nights before the 14th


def test_an_impossible_night_label_is_ignored():
    [claim] = group([folio("Room Charges 31-Feb", "Room Charges 13-Aug")])
    assert claim.start_date == d(13)


def test_a_folio_with_no_room_lines_counts_as_one_night():
    [claim] = group([folio()])
    assert (claim.start_date, claim.end_date) == (d(13), d(14))


def test_an_undated_trip_does_not_collect_dated_followers():
    docs = [ticket("t1", None, BASE, "Nagpur"), meal("m1", d(12), "Nagpur")]
    claims = group(docs)
    [trip] = trips(claims)
    assert trip.document_ids == ["t1"]
    assert any("m1" in c.document_ids and c.mode is ClaimMode.period for c in claims)
