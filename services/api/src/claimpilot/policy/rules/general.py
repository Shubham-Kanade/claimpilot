"""Clauses that apply to every claim: scope, city tiers, timing, receipts, personal expenses."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Literal

from pydantic import Field

from claimpilot.domain import Claim, Finding, ProcessedDocument
from claimpilot.policy.cities import normalise_city
from claimpilot.policy.codes import PolicyCode
from claimpilot.policy.money import format_inr
from claimpilot.policy.rules.base import (
    ClauseBase,
    Context,
    RuleClause,
    Strict,
    Tier,
    is_inr,
)


class InfoClause(ClauseBase):
    """Context only (scope, definitions): quoted on request, never enforced."""

    rule: Literal["info"]


class CityTiersParams(Strict):
    tier1: tuple[str, ...] = Field(min_length=1)


class CityTiersClause(ClauseBase):
    """1.2 Which cities are Tier-1; every other city is Tier-2."""

    rule: Literal["city_tiers"]
    params: CityTiersParams

    def tier_of(self, city: str | None) -> Tier | None:
        """``None`` when there is no city to look up."""
        name = normalise_city(city)
        if name is None:
            return None
        padded = f" {name} "
        for listed in self.params.tier1:
            key = normalise_city(listed)
            if key and f" {key} " in padded:  # whole words: "Navi Mumbai" is Tier-1, "Punekar" not
                return "tier1"
        return "tier2"


class SubmissionWindowParams(Strict):
    window_days: int = Field(gt=0)


class SubmissionWindowClause(RuleClause):
    """2.1 A claim is due within N days of its last expense date."""

    rule: Literal["submission_window"]
    params: SubmissionWindowParams

    def check_claim(
        self, claim: Claim, docs: Sequence[ProcessedDocument], ctx: Context
    ) -> list[Finding]:
        dated = [d for d in (doc.expense_date for doc in docs) if d is not None]
        end = claim.end_date or (max(dated) if dated else None)
        if end is None:
            return []
        window = self.params.window_days
        elapsed = (ctx.today - end).days
        if ctx.today <= end + timedelta(days=window):
            return []
        late_by = elapsed - window
        return [
            self.finding(
                PolicyCode.late_submission,
                f"The last expense on this claim was on {end:%d %b %Y}, {elapsed} days ago. The "
                f"window is {window} days, so the claim is {late_by} "
                f"{'day' if late_by == 1 else 'days'} late.",
                expected=window,
                actual=elapsed,
            )
        ]


class ReceiptsParams(Strict):
    receipt_threshold: float = Field(gt=0)


class ReceiptsClause(RuleClause):
    """3.1 Above the threshold a receipt showing merchant, date and amount is required.

    At or below it nothing is required (conveyance and tips can be self-declared; the claims
    module asks for that declaration), so only larger documents missing evidence are flagged.
    """

    rule: Literal["receipts"]
    params: ReceiptsParams

    def check_document(self, doc: ProcessedDocument, ctx: Context) -> list[Finding]:
        receipt = doc.receipt
        missing: list[str] = []
        if receipt.total is None:
            missing.append("total")
        if doc.expense_date is None:  # not printed, or not a date we can read
            missing.append("date")
        if not (receipt.merchant_name or "").strip():
            missing.append("merchant_name")
        if not missing:
            return []
        # With no total there is no amount to compare, and the missing total is a gap on its own.
        threshold = self.params.receipt_threshold
        if receipt.total is not None and is_inr(doc) and receipt.total <= threshold:
            return []
        names = {"total": "amount", "date": "date", "merchant_name": "merchant"}
        gaps = _join([names[m] for m in missing])
        return [
            self.finding(
                PolicyCode.receipt_required,
                f"A receipt showing the merchant, date and amount is needed for expenses above "
                f"{format_inr(self.params.receipt_threshold)}, but this one has no {gaps}.",
                fields=tuple(missing),
                expected="merchant, date and amount",
                actual=f"missing {gaps}",
            )
        ]


class PersonalExpenseParams(Strict):
    probability_threshold: float = Field(gt=0, le=1)


class PersonalExpenseClause(RuleClause):
    """10.1 Personal expenses are not reimbursable (System One flags the likely ones)."""

    rule: Literal["personal_expense"]
    params: PersonalExpenseParams

    def check_document(self, doc: ProcessedDocument, ctx: Context) -> list[Finding]:
        if doc.decisions.personal_expense < self.params.probability_threshold:
            return []
        return [
            self.finding(
                PolicyCode.personal_expense_flagged,
                f"This {format_inr(doc.amount)} expense looks personal rather than business, "
                "and personal expenses are not reimbursable.",
                actual=doc.amount,
            )
        ]


def _join(parts: Sequence[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    if len(parts) <= 1:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"
