"""7.x Travel: air class, rail class by grade, local travel on a trip."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Literal, Self

from pydantic import Field, model_validator

from claimpilot.domain import (
    Claim,
    ClaimMode,
    DocType,
    ExpenseCategory,
    Finding,
    ProcessedDocument,
)
from claimpilot.policy.codes import PolicyCode
from claimpilot.policy.money import format_inr
from claimpilot.policy.receipt_text import TRAIN_CLASS_NAMES, AirClass, air_class, train_class
from claimpilot.policy.rules.base import (
    EPSILON,
    TIER_NAMES,
    Context,
    RuleClause,
    Strict,
    Tier,
    TierAmounts,
    grade_coverage_error,
    highest_tier,
    is_inr,
)

_AIR_NAMES: dict[AirClass, str] = {
    "economy": "economy",
    "premium_economy": "premium economy",
    "business": "business",
    "first": "first",
}


class AirClassParams(Strict):
    allowed: tuple[AirClass, ...] = Field(min_length=1)


class AirClassClause(RuleClause):
    """7.1 Flights are booked in an allowed cabin class (economy only in v3).

    Only checked when a class is printed on the ticket; most e-tickets do not print one in the
    fare lines, and guessing would put a blocking finding on a legitimate ticket.
    """

    rule: Literal["air_class"]
    params: AirClassParams

    def check_document(self, doc: ProcessedDocument, ctx: Context) -> list[Finding]:
        if doc.receipt.doc_type is not DocType.flight_ticket:
            return []
        printed = air_class(doc.receipt)
        if printed is None or printed in self.params.allowed:
            return []
        allowed = " or ".join(_AIR_NAMES[c] for c in self.params.allowed)
        return [
            self.finding(
                PolicyCode.air_class_not_economy,
                f"The flight ticket is in {_AIR_NAMES[printed]} class, but air travel must be "
                f"booked in {allowed} class.",
                fields=("line_items",),
                expected=allowed,
                actual=_AIR_NAMES[printed],
            )
        ]


class TrainClassParams(Strict):
    class_order: tuple[str, ...] = Field(min_length=1)  # lowest to highest
    max_class_by_grade: dict[str, str]


class TrainClassClause(RuleClause):
    """7.2 A train ticket may not be above the highest class allowed for the employee's grade.

    Only checked when a class is printed on the ticket.
    """

    rule: Literal["train_class"]
    params: TrainClassParams

    @model_validator(mode="after")
    def _classes_are_known(self) -> Self:
        unknown = {*self.params.class_order, *self.params.max_class_by_grade.values()} - set(
            TRAIN_CLASS_NAMES
        )
        if unknown:
            raise ValueError(f"clause {self.id}: unknown railway classes {sorted(unknown)}")
        outside = set(self.params.max_class_by_grade.values()) - set(self.params.class_order)
        if outside:
            raise ValueError(f"clause {self.id}: classes {sorted(outside)} are not in class_order")
        return self

    def validate_grades(self, grades: Sequence[str]) -> None:
        problem = grade_coverage_error(
            list(self.params.max_class_by_grade), grades, f"clause {self.id}"
        )
        if problem:
            raise ValueError(problem)

    def check_document(self, doc: ProcessedDocument, ctx: Context) -> list[Finding]:
        if doc.receipt.doc_type is not DocType.train_ticket:
            return []
        allowed = self.params.max_class_by_grade.get(ctx.grade)
        if allowed is None:
            return [self.unknown_grade(ctx)]
        printed = train_class(doc.receipt)
        order = self.params.class_order
        if printed is None or printed not in order:
            return []
        if order.index(printed) <= order.index(allowed):
            return []
        return [
            self.finding(
                PolicyCode.train_class_above_grade,
                f"The train ticket is {printed} ({TRAIN_CLASS_NAMES[printed]}), but grade "
                f"{ctx.grade} is allowed up to {allowed} ({TRAIN_CLASS_NAMES[allowed]}).",
                fields=("line_items",),
                expected=allowed,
                actual=printed,
            )
        ]


class TripConveyanceParams(Strict):
    daily_limit: TierAmounts


class TripConveyanceClause(RuleClause):
    """7.3 Local travel (cabs, autos) at a trip destination has a daily limit by city tier.

    A per-trip limit: it only applies to claims in trip mode, summing the day's local travel
    receipts across the whole trip, airport and station transfers included.
    """

    rule: Literal["trip_conveyance"]
    params: TripConveyanceParams

    def check_claim(
        self, claim: Claim, docs: Sequence[ProcessedDocument], ctx: Context
    ) -> list[Finding]:
        if claim.mode is not ClaimMode.trip:
            return []
        by_day: dict[date, list[ProcessedDocument]] = defaultdict(list)
        for doc in docs:
            day = doc.expense_date
            if doc.category is ExpenseCategory.local_conveyance and day is not None and is_inr(doc):
                by_day[day].append(doc)
        findings: list[Finding] = []
        for day, rides in sorted(by_day.items()):
            tiers: set[Tier] = {ctx.tier_for(r.receipt.merchant_city, claim.city) for r in rides}
            tier = highest_tier(tiers)
            limit = self.params.daily_limit.for_tier(tier)
            total = round(sum(r.amount for r in rides), 2)
            if total <= limit + EPSILON:
                continue
            findings.append(
                self.finding(
                    PolicyCode.trip_conveyance_over_limit,
                    f"Local travel on {day:%d %b %Y} adds up to {format_inr(total)} "
                    f"({len(rides)} receipts), above the {format_inr(limit)} daily limit for "
                    f"{TIER_NAMES[tier]} cities.",
                    fields=("total",),
                    expected=limit,
                    actual=total,
                )
            )
        return findings
