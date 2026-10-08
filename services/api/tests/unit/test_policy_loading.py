"""policy.yaml: loading, strict validation, clause lookup and text/number consistency."""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from claimpilot.config import API_ROOT
from claimpilot.domain import Severity
from claimpilot.policy import DEFAULT_POLICY_PATH, Policy, UnknownClause, format_inr
from claimpilot.policy.rules import (
    REQUIRED_RULES,
    AirClassClause,
    CityTiersClause,
    HotelCapClause,
    InfoClause,
    MealsClause,
    MobileCapClause,
    RuleClause,
    TrainClassClause,
)


@pytest.fixture(scope="module")
def policy() -> Policy:
    return Policy.load()


@pytest.fixture
def raw() -> dict[str, Any]:
    """The policy file as plain data, free to be broken one way at a time."""
    return copy.deepcopy(yaml.safe_load(DEFAULT_POLICY_PATH.read_text(encoding="utf-8")))


def clause_of(data: dict[str, Any], clause_id: str) -> dict[str, Any]:
    return next(c for c in data["clauses"] if c["id"] == clause_id)


# --- loading ------------------------------------------------------------------------------------


def test_default_path_is_the_config_folder():
    assert DEFAULT_POLICY_PATH == API_ROOT / "config" / "policy.yaml"
    assert DEFAULT_POLICY_PATH.exists()


def test_loads_the_orion_demo_policy(policy: Policy):
    assert policy.title == "Orion Demo Corp Travel & Expense Policy v3 (synthetic)"
    assert policy.effective_date == date(2026, 4, 1)
    assert policy.currency == "INR"
    assert policy.grades == ("L1", "L2", "L3", "L4", "L5")


def test_every_enforced_rule_is_present_exactly_once(policy: Policy):
    rules = [c.rule for c in policy.clauses if c.rule != "info"]
    assert sorted(rules) == sorted(REQUIRED_RULES)


def test_clause_ids_follow_the_documented_numbering(policy: Policy):
    ids = [c.id for c in policy.clauses]
    assert ids == ["1.1", "1.2", "2.1", "3.1", "4.1", "5.1", "5.2", "6.1", "7.1", "7.2", "7.3",
                   "8.1", "9.1", "10.1"]  # fmt: skip
    assert policy.clause("2.1").rule == "submission_window"
    assert policy.clause("6.1").rule == "alcohol"


def test_load_from_an_explicit_path(tmp_path: Path, raw: dict[str, Any]):
    raw["version"] = "4"
    path = tmp_path / "other.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert Policy.load(path).version == "4"


def test_a_policy_is_immutable(policy: Policy):
    with pytest.raises(ValidationError):
        policy.version = "9"  # type: ignore[misc]


def test_load_is_repeatable_and_equal(policy: Policy):
    assert Policy.load() == policy


# --- clause lookup -----------------------------------------------------------------------------


def test_clause_lookup_returns_quotable_text(policy: Policy):
    clause = policy.clause("4.1")
    assert isinstance(clause, HotelCapClause)
    assert clause.title == "Accommodation"
    assert clause.text.startswith("Hotel rooms are reimbursed up to a nightly limit")
    assert clause.severity is Severity.high


def test_unknown_clause_id_raises(policy: Policy):
    with pytest.raises(UnknownClause):
        policy.clause("99.9")


def test_severities_match_the_design(policy: Policy):
    severities = {c.id: c.severity for c in policy.clauses if isinstance(c, RuleClause)}
    assert {i for i, s in severities.items() if s is Severity.high} == {"4.1", "6.1", "7.1"}
    assert set(severities.values()) <= {Severity.high, Severity.warn}


def test_info_clause_is_not_enforced(policy: Policy):
    scope = policy.clause("1.1")
    assert isinstance(scope, InfoClause)
    assert not isinstance(scope, RuleClause)


def test_receipt_and_personal_thresholds_are_exposed(policy: Policy):
    assert policy.receipt_threshold == 200
    assert policy.personal_threshold == 0.5


# --- strictness ---------------------------------------------------------------------------------


def test_unknown_top_level_key_is_rejected(raw: dict[str, Any]):
    raw["effective"] = "2026-01-01"
    with pytest.raises(ValidationError, match="effective"):
        Policy.model_validate(raw)


def test_unknown_clause_key_is_rejected(raw: dict[str, Any]):
    clause_of(raw, "4.1")["notes"] = "typo"
    with pytest.raises(ValidationError, match="notes"):
        Policy.model_validate(raw)


def test_unknown_parameter_key_is_rejected(raw: dict[str, Any]):
    clause_of(raw, "2.1")["params"]["window_day"] = 30
    with pytest.raises(ValidationError, match="window_day"):
        Policy.model_validate(raw)


def test_unknown_rule_is_rejected(raw: dict[str, Any]):
    clause_of(raw, "2.1")["rule"] = "teleportation"
    with pytest.raises(ValidationError, match="teleportation"):
        Policy.model_validate(raw)


def test_duplicate_clause_id_is_rejected(raw: dict[str, Any]):
    clause_of(raw, "5.2")["id"] = "5.1"
    with pytest.raises(ValidationError, match="duplicate clause ids"):
        Policy.model_validate(raw)


def test_missing_rule_is_rejected(raw: dict[str, Any]):
    raw["clauses"] = [c for c in raw["clauses"] if c["rule"] != "alcohol"]
    with pytest.raises(ValidationError, match="alcohol"):
        Policy.model_validate(raw)


def test_a_rule_in_two_clauses_is_rejected(raw: dict[str, Any]):
    extra = copy.deepcopy(clause_of(raw, "6.1"))
    extra["id"] = "6.2"
    raw["clauses"].append(extra)
    with pytest.raises(ValidationError, match="more than one clause"):
        Policy.model_validate(raw)


@pytest.mark.parametrize("bad_id", ["4", "4.1.2", "four", "4.x"])
def test_clause_id_must_look_like_a_number(raw: dict[str, Any], bad_id: str):
    clause_of(raw, "4.1")["id"] = bad_id
    with pytest.raises(ValidationError):
        Policy.model_validate(raw)


def test_hotel_table_must_cover_every_grade(raw: dict[str, Any]):
    del clause_of(raw, "4.1")["params"]["nightly_cap"]["L5"]
    with pytest.raises(ValidationError, match="missing"):
        Policy.model_validate(raw)


def test_a_grade_table_cannot_name_an_unknown_grade(raw: dict[str, Any]):
    clause_of(raw, "8.1")["params"]["monthly_cap"]["L9"] = 5000
    with pytest.raises(ValidationError, match="unknown"):
        Policy.model_validate(raw)


@pytest.mark.parametrize(
    ("clause_id", "path", "value"),
    [
        ("4.1", ("nightly_cap", "L1", "tier1"), 0),
        ("4.1", ("nightly_cap", "L1", "tier2"), -5),
        ("5.1", ("daily_limit", "tier1"), 0),
        ("2.1", ("window_days",), 0),
        ("3.1", ("receipt_threshold",), -1),
        ("9.1", ("threshold",), 0),
        ("6.1", ("probability_threshold",), 1.5),
        ("10.1", ("probability_threshold",), 0),
    ],
)
def test_numeric_parameters_must_be_sane(
    raw: dict[str, Any], clause_id: str, path: tuple[str, ...], value: float
):
    node = clause_of(raw, clause_id)["params"]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValidationError):
        Policy.model_validate(raw)


def test_mobile_caps_must_be_positive(raw: dict[str, Any]):
    clause_of(raw, "8.1")["params"]["monthly_cap"]["L1"] = 0
    with pytest.raises(ValidationError, match="positive"):
        Policy.model_validate(raw)


def test_train_class_must_be_a_known_railway_class(raw: dict[str, Any]):
    clause_of(raw, "7.2")["params"]["max_class_by_grade"]["L1"] = "9Z"
    with pytest.raises(ValidationError, match="unknown railway classes"):
        Policy.model_validate(raw)


def test_train_class_must_be_in_the_class_order(raw: dict[str, Any]):
    clause_of(raw, "7.2")["params"]["class_order"] = ["SL", "3A", "2A"]
    with pytest.raises(ValidationError, match="not in class_order"):
        Policy.model_validate(raw)


def test_grades_must_be_unique_uppercase(raw: dict[str, Any]):
    raw["grades"] = ["L1", "l2", "L3", "L4", "L5"]
    with pytest.raises(ValidationError, match="upper-case"):
        Policy.model_validate(raw)


def test_air_class_must_be_a_known_cabin(raw: dict[str, Any]):
    clause_of(raw, "7.1")["params"]["allowed"] = ["economy", "sleeper"]
    with pytest.raises(ValidationError):
        Policy.model_validate(raw)


def test_malformed_yaml_fails_loudly(tmp_path: Path):
    path = tmp_path / "broken.yaml"
    path.write_text("name: [unclosed", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        Policy.load(path)


def test_missing_file_fails_loudly(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        Policy.load(tmp_path / "nope.yaml")


# --- the quoted wording must match what the code enforces --------------------------------------


def test_hotel_clause_text_quotes_every_cap(policy: Policy):
    clause = policy.clause("4.1")
    assert isinstance(clause, HotelCapClause)
    for caps in clause.params.nightly_cap.values():
        assert format_inr(caps.tier1) in clause.text
        assert format_inr(caps.tier2) in clause.text


def test_mobile_clause_text_quotes_every_cap(policy: Policy):
    clause = policy.clause("8.1")
    assert isinstance(clause, MobileCapClause)
    for cap in clause.params.monthly_cap.values():
        assert format_inr(cap) in clause.text


def test_meals_and_local_travel_text_quote_both_limits(policy: Policy):
    for clause_id in ("5.1", "7.3"):
        clause = policy.clause(clause_id)
        assert isinstance(clause, RuleClause)
        limits = clause.params.daily_limit  # type: ignore[union-attr]
        assert format_inr(limits.tier1) in clause.text
        assert format_inr(limits.tier2) in clause.text


@pytest.mark.parametrize(
    ("clause_id", "value"),
    [("3.1", 200), ("5.2", 2500), ("9.1", 10000)],
)
def test_single_amount_clauses_quote_their_number(policy: Policy, clause_id: str, value: float):
    assert format_inr(value) in policy.clause(clause_id).text


def test_submission_window_text_quotes_the_days(policy: Policy):
    clause = policy.clause("2.1")
    assert f"{clause.params.window_days} days" in clause.text  # type: ignore[union-attr]


def test_train_clause_text_names_the_class_for_each_grade(policy: Policy):
    clause = policy.clause("7.2")
    assert isinstance(clause, TrainClassClause)
    for code in set(clause.params.max_class_by_grade.values()):
        assert code in clause.text


def test_air_clause_says_economy(policy: Policy):
    clause = policy.clause("7.1")
    assert isinstance(clause, AirClassClause)
    assert "economy" in clause.text.lower()


def test_tier_one_cities_are_the_documented_eight(policy: Policy):
    clause = policy.clause("1.2")
    assert isinstance(clause, CityTiersClause)
    names = {n.lower() for n in clause.params.tier1}
    assert names == {"delhi", "new delhi", "mumbai", "bengaluru", "hyderabad", "chennai",
                     "kolkata", "pune", "ahmedabad"}  # fmt: skip
    for name in names - {"delhi", "new delhi"}:
        assert name.title() in clause.text


def test_meals_clause_is_a_rule_with_a_tier_table(policy: Policy):
    clause = policy.clause("5.1")
    assert isinstance(clause, MealsClause)
    assert clause.params.daily_limit.tier1 > clause.params.daily_limit.tier2


def test_train_table_must_cover_every_grade(raw: dict[str, Any]):
    del clause_of(raw, "7.2")["params"]["max_class_by_grade"]["L3"]
    with pytest.raises(ValidationError, match="missing"):
        Policy.model_validate(raw)
