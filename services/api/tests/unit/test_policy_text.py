"""Policy helpers: city names and tiers, rupee formatting, and reading facts out of receipt text."""

from __future__ import annotations

import pytest

from claimpilot.domain import DocType, ExtractedReceipt, LineItem, TaxBreakup
from claimpilot.policy import (
    Policy,
    alcohol_items,
    attendee_headcount,
    format_inr,
    hotel_stay,
    normalise_city,
    same_city,
)
from claimpilot.policy.receipt_text import air_class, train_class
from claimpilot.policy.rules import CityTiersClause


def lines(*items: tuple[str, float]) -> list[LineItem]:
    return [LineItem(description=text, amount=amount) for text, amount in items]


def receipt(doc_type: DocType = DocType.other, **kw: object) -> ExtractedReceipt:
    return ExtractedReceipt(doc_type=doc_type, **kw)  # type: ignore[arg-type]


# --- cities -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Pune", "pune"),
        ("  PUNE  ", "pune"),
        ("Bangalore", "bengaluru"),
        ("Bengaluru, KA", "bengaluru"),
        ("BENGALURU - 560001", "bengaluru"),
        ("Bombay", "mumbai"),
        ("Mumbai (Andheri East)", "mumbai"),
        ("New Delhi", "delhi"),
        ("Delhi", "delhi"),
        ("Delhi NCR", "delhi ncr"),
        ("Madras", "chennai"),
        ("Calcutta", "kolkata"),
        ("Cochin", "kochi"),
        ("Bhubaneshwar", "bhubaneswar"),
        ("Navi Mumbai", "navi mumbai"),
        ("लखनऊ", "लखनऊ"),
    ],
)
def test_city_names_are_normalised(raw: str, expected: str):
    assert normalise_city(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", ",,,", "-"])
def test_nothing_to_normalise(raw: str | None):
    assert normalise_city(raw) is None


def test_same_city_compares_by_canonical_name():
    assert same_city("Bangalore", "Bengaluru")
    assert same_city("New Delhi", "delhi")
    assert not same_city("Pune", "Mumbai")
    assert not same_city(None, None)
    assert not same_city("Pune", None)


@pytest.fixture(scope="module")
def tiers() -> CityTiersClause:
    clause = Policy.load().clause("1.2")
    assert isinstance(clause, CityTiersClause)
    return clause


@pytest.mark.parametrize(
    "city",
    ["Delhi", "New Delhi", "Mumbai", "Bengaluru", "Bangalore", "Hyderabad", "Chennai", "Kolkata",
     "Pune", "Ahmedabad", "Navi Mumbai", "Delhi NCR", "Pune, Maharashtra"],
)  # fmt: skip
def test_tier_one_cities(tiers: CityTiersClause, city: str):
    assert tiers.tier_of(city) == "tier1"


@pytest.mark.parametrize(
    "city", ["Nagpur", "Jaipur", "Lucknow", "Indore", "Chandigarh", "Kochi", "Panaji", "Surat"]
)
def test_every_other_city_is_tier_two(tiers: CityTiersClause, city: str):
    assert tiers.tier_of(city) == "tier2"


def test_tier_of_unknown_city_is_none(tiers: CityTiersClause):
    assert tiers.tier_of(None) is None
    assert tiers.tier_of("") is None


def test_tier_match_is_by_whole_word(tiers: CityTiersClause):
    assert tiers.tier_of("Punekar Nagar") == "tier2"  # not Pune


# --- money --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "text"),
    [
        (0, "₹0"),
        (5, "₹5"),
        (999, "₹999"),
        (1000, "₹1,000"),
        (10450, "₹10,450"),
        (99999, "₹99,999"),
        (100000, "₹1,00,000"),
        (122420, "₹1,22,420"),
        (12345678, "₹1,23,45,678"),
        (651.5, "₹651.50"),
        (4102.0, "₹4,102"),
        (0.05, "₹0.05"),
        (-2500, "-₹2,500"),
        (999.999, "₹1,000"),
    ],
)
def test_format_inr(amount: float, text: str):
    assert format_inr(amount) == text


# --- alcohol ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "description",
    ["Draught Beer 330ml", "House Red Wine (Glass)", "Whisky 60ml", "whiskey sour", "Vodka shot",
     "Old Rum", "Gin & Tonic", "Cocktail of the day", "Kingfisher Lager", "Champagne flute",
     "Tequila", "Scotch 30ml", "Liquor", "Prosecco", "बीयर", "रेड वाइन", "व्हिस्की", "शराब"],
)  # fmt: skip
def test_alcohol_vocabulary_matches(description: str):
    assert len(alcohol_items(receipt(line_items=lines((description, 100.0))))) == 1


@pytest.mark.parametrize(
    "description",
    ["Masala Chai", "Ginger Tea", "Fresh Lime Soda", "Non-Alcoholic Beer", "Alcohol free wine",
     "Ginger Beer", "Ginger ale", "Root beer float", "Virgin Mocktail", "Brumby", "Swine curry",
     "Engine oil", "Paneer Tikka"],
)  # fmt: skip
def test_non_alcohol_items_are_not_matched(description: str):
    assert alcohol_items(receipt(line_items=lines((description, 100.0)))) == []


def test_alcohol_items_keep_only_the_drinks():
    items = lines(("Paneer Tikka", 300.0), ("Draught Beer 330ml", 420.0), ("Naan", 60.0))
    found = alcohol_items(receipt(line_items=items))
    assert [i.description for i in found] == ["Draught Beer 330ml"]


# --- hotel nights -------------------------------------------------------------------------------


def test_each_room_line_is_a_night():
    stay = hotel_stay(receipt(line_items=lines(("Room Charges 12-Jul", 6100.0),
                                               ("Room Charges 13-Jul", 6100.0))))  # fmt: skip
    assert stay is not None and stay.nights_known
    assert stay.rates == (6100.0, 6100.0)


def test_nights_can_differ():
    stay = hotel_stay(receipt(line_items=lines(("Room Charges 15-Aug", 7700.0),
                                               ("Room Charges 16-Aug", 7200.0))))  # fmt: skip
    assert stay is not None and stay.rates == (7700.0, 7200.0)


def test_quantity_and_unit_price_give_the_nightly_rate():
    item = LineItem(description="Deluxe Room", quantity=3, unit_price=6000.0, amount=18000.0)
    stay = hotel_stay(receipt(line_items=[item]))
    assert stay is not None and stay.rates == (6000.0, 6000.0, 6000.0)


def test_quantity_without_unit_price_divides_the_amount():
    item = LineItem(description="Room tariff", quantity=2, amount=9000.0)
    stay = hotel_stay(receipt(line_items=[item]))
    assert stay is not None and stay.rates == (4500.0, 4500.0)


def test_nights_in_the_description_divide_the_amount():
    stay = hotel_stay(receipt(line_items=lines(("Room charges (3 nights)", 18000.0))))
    assert stay is not None and stay.nights_known and stay.rates == (6000.0,) * 3


def test_extras_are_not_room_nights():
    items = lines(
        ("Room Charges 12-Jul", 6000.0),
        ("Room Service - dinner", 1800.0),
        ("Laundry", 400.0),
        ("Mini Bar", 900.0),
        ("Breakfast buffet", 700.0),
        ("Service Charge", 300.0),
        ("GST", 300.0),
    )
    stay = hotel_stay(receipt(line_items=items))
    assert stay is not None and stay.rates == (6000.0,)


def test_unlabelled_lines_count_when_nothing_says_room():
    stay = hotel_stay(receipt(line_items=lines(("Executive Twin", 8000.0))))
    assert stay is not None and stay.rates == (8000.0,)


def test_only_extras_means_nothing_to_compare():
    assert (
        hotel_stay(receipt(line_items=lines(("Laundry", 400.0), ("Room Service", 900.0)))) is None
    )


def test_no_lines_falls_back_to_the_pre_tax_amount_with_unknown_nights():
    stay = hotel_stay(receipt(subtotal=17000.0, total=20060.0))
    assert stay is not None and not stay.nights_known and stay.rates == (17000.0,)


def test_no_lines_and_no_subtotal_backs_tax_out_of_the_total():
    taxes = TaxBreakup(cgst=450.0, sgst=450.0)
    stay = hotel_stay(receipt(total=10900.0, taxes=taxes, service_charge=100.0))
    assert stay is not None and stay.rates == (9900.0,)


def test_no_amounts_at_all_is_none():
    assert hotel_stay(receipt()) is None
    assert hotel_stay(receipt(total=0.0)) is None


def test_zero_amount_lines_are_ignored():
    stay = hotel_stay(receipt(line_items=lines(("Room Charges 12-Jul", 0.0))))
    assert stay is None


# --- ticket class -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Base Fare - Economy", "economy"),
        ("Fare (Business Class)", "business"),
        ("Business", "business"),
        ("Premium Economy fare", "premium_economy"),
        ("premium-economy", "premium_economy"),
        ("First Class", "first"),
        ("Class: First", "first"),
        ("Base Fare", None),
        ("Convenience Fee", None),
    ],
)
def test_air_class_from_fare_lines(text: str, expected: str | None):
    assert air_class(receipt(DocType.flight_ticket, line_items=lines((text, 4000.0)))) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ticket Fare (3A)", "3A"),
        ("Fare - 2A", "2A"),
        ("Ticket Fare - SL", "SL"),
        ("Class: CC", "CC"),
        ("First AC fare", "1A"),
        ("Third AC", "3A"),
        ("3rd AC Economy", "3A"),
        ("3AC Economy", "3E"),
        ("Second AC", "2A"),
        ("Sleeper", "SL"),
        ("Executive Chair Car", "EC"),
        ("AC Chair Car", "CC"),
        ("Second Sitting", "2S"),
        ("Ticket Fare", None),
        ("cc avenue snacks", None),  # lower-case two-letter codes are not trusted
        ("Convenience Fee", None),
    ],
)
def test_train_class_from_fare_lines(text: str, expected: str | None):
    assert train_class(receipt(DocType.train_ticket, line_items=lines((text, 900.0)))) == expected


# --- attendees ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("4", 4),
        ("4 people", 4),
        ("  6 guests ", 6),
        ("8 pax", 8),
        ("party of 5", 5),
        ("a table of 3", 3),
        ("12 attendees in total", 12),
        ("four people", 4),
        ("Six of us from Orion Retail and Asha", 6),
        ("party of five", 5),
        ("Twenty", 20),
        ("Rahul Mehra, Anita Rao", 3),  # two clients plus the employee
        ("Rahul Mehra and Anita Rao", 3),
        ("Rahul Mehra & Anita Rao; Dev Patel", 4),
        ("Rahul Mehra (Orion Retail), Anita Rao (Orion Retail, Pune)", 3),
        ("Rahul Mehra from Orion Retail", 2),
        ("Me, Rahul Mehra", 2),  # "me" is the employee: do not add them twice
        ("Asha Rao, Rahul Mehra", 2),  # the employee's own name is in the list
        ("Rahul Mehra\nAnita Rao", 3),
        ("from calendar: Client dinner - Orion Retail (attendees: Rahul Mehra, Anita Rao)", 3),
        ("राहुल मेहता, अनीता राव", 3),
    ],
)
def test_attendee_headcount(answer: str, expected: int):
    assert attendee_headcount(answer, "Asha Rao") == expected


@pytest.mark.parametrize("answer", ["", "   ", "0", "---", "123 456 789 0000", "n/a, -"])
def test_uncountable_answers_give_none(answer: str):
    assert attendee_headcount(answer, "Asha Rao") is None


def test_short_fragments_do_not_count_as_the_employee():
    # "Al" is part of "Alan" but not enough to say the employee is listed
    assert attendee_headcount("Al, Bo", "Alan Smith") == 3


def test_headcount_without_an_employee_name_only_counts_names():
    assert attendee_headcount("Rahul Mehra, Anita Rao") == 3
