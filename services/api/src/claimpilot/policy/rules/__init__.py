"""The rule clauses of the policy and the tagged union ``policy.yaml`` clauses are parsed into."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from claimpilot.policy.rules.accommodation import HotelCapClause
from claimpilot.policy.rules.allowances import LearningPreapprovalClause, MobileCapClause
from claimpilot.policy.rules.base import ClauseBase, Context, RuleClause, Tier, TierAmounts
from claimpilot.policy.rules.general import (
    CityTiersClause,
    InfoClause,
    PersonalExpenseClause,
    ReceiptsClause,
    SubmissionWindowClause,
)
from claimpilot.policy.rules.meals import AlcoholClause, EntertainmentClause, MealsClause
from claimpilot.policy.rules.travel import AirClassClause, TrainClassClause, TripConveyanceClause

# One class per ``rule:`` value in policy.yaml; pydantic picks the class from that field.
Clause = Annotated[
    InfoClause
    | CityTiersClause
    | SubmissionWindowClause
    | ReceiptsClause
    | HotelCapClause
    | MealsClause
    | EntertainmentClause
    | AlcoholClause
    | AirClassClause
    | TrainClassClause
    | TripConveyanceClause
    | MobileCapClause
    | LearningPreapprovalClause
    | PersonalExpenseClause,
    Field(discriminator="rule"),
]

# Every enforced rule must appear exactly once in a policy file.
REQUIRED_RULES: frozenset[str] = frozenset(
    {
        "city_tiers",
        "submission_window",
        "receipts",
        "hotel_cap",
        "meals_daily_limit",
        "entertainment",
        "alcohol",
        "air_class",
        "train_class",
        "trip_conveyance",
        "mobile_cap",
        "learning_preapproval",
        "personal_expense",
    }
)

__all__ = [
    "REQUIRED_RULES",
    "AirClassClause",
    "AlcoholClause",
    "CityTiersClause",
    "Clause",
    "ClauseBase",
    "Context",
    "EntertainmentClause",
    "HotelCapClause",
    "InfoClause",
    "LearningPreapprovalClause",
    "MealsClause",
    "MobileCapClause",
    "PersonalExpenseClause",
    "ReceiptsClause",
    "RuleClause",
    "SubmissionWindowClause",
    "Tier",
    "TierAmounts",
    "TrainClassClause",
    "TripConveyanceClause",
]
