"""4.x Accommodation: a nightly cap by grade and city tier, applied to the pre-GST room rate."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from claimpilot.domain import DocType, ExpenseCategory, Finding, ProcessedDocument, Severity
from claimpilot.policy.codes import PolicyCode
from claimpilot.policy.money import format_inr
from claimpilot.policy.receipt_text import hotel_stay
from claimpilot.policy.rules.base import (
    EPSILON,
    TIER_NAMES,
    Context,
    RuleClause,
    Strict,
    TierAmounts,
    grade_coverage_error,
    is_inr,
)


class HotelCapParams(Strict):
    nightly_cap: dict[str, TierAmounts]


class HotelCapClause(RuleClause):
    """4.1 Each night's room rate (before GST) must be within the cap for grade and city tier.

    Why per night and before GST: a folio prints one room line per night, and GST (5% or 18%
    depending on the tariff) would otherwise make the same room cost more or less against the cap.
    The city tier comes from the hotel's own city; if the bill prints none, the higher Tier-1
    cap is used so a missing city can never create a blocking finding on its own.
    """

    rule: Literal["hotel_cap"]
    params: HotelCapParams

    def validate_grades(self, grades: Sequence[str]) -> None:
        problem = grade_coverage_error(list(self.params.nightly_cap), grades, f"clause {self.id}")
        if problem:
            raise ValueError(problem)

    def check_document(self, doc: ProcessedDocument, ctx: Context) -> list[Finding]:
        receipt = doc.receipt
        is_hotel = (
            doc.category is ExpenseCategory.accommodation or receipt.doc_type is DocType.hotel_folio
        )
        if not is_hotel or not is_inr(doc):
            return []
        caps = self.params.nightly_cap.get(ctx.grade)
        if caps is None:
            return [self.unknown_grade(ctx)]
        stay = hotel_stay(receipt)
        if stay is None:
            return []
        tier = ctx.tiers.tier_of(receipt.merchant_city)
        cap = caps.for_tier(tier or "tier1")
        over = [rate for rate in stay.rates if rate > cap + EPSILON]
        if not over:
            return []
        top = max(over)
        if tier is None:
            where = "(the bill prints no city, so the Tier-1 cap applies)"
        else:
            where = f"in a {TIER_NAMES[tier]} city"
        if not stay.nights_known:
            return [
                self.finding(
                    PolicyCode.hotel_over_cap,
                    f"This hotel bill is {format_inr(top)} before GST and does not say how many "
                    f"nights it covers. If it is one night it is above the {format_inr(cap)} cap "
                    f"for grade {ctx.grade} {where}.",
                    severity=Severity.warn,
                    fields=("line_items",),
                    expected=cap,
                    actual=top,
                )
            ]
        extra = f" ({len(over)} of {len(stay.rates)} nights are over)" if len(over) > 1 else ""
        return [
            self.finding(
                PolicyCode.hotel_over_cap,
                f"Hotel night {format_inr(top)} exceeds the {format_inr(cap)} cap for grade "
                f"{ctx.grade} {where}{extra}.",
                fields=("line_items",),
                expected=cap,
                actual=top,
            )
        ]
