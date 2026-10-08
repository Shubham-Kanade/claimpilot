"""5.x Meals and client entertainment, 6.x Alcohol."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Literal

from pydantic import Field

from claimpilot.domain import (
    Claim,
    ExpenseCategory,
    Finding,
    ProcessedDocument,
    QuestionKind,
)
from claimpilot.policy.codes import PolicyCode
from claimpilot.policy.money import format_inr
from claimpilot.policy.receipt_text import alcohol_items, attendee_headcount
from claimpilot.policy.rules.base import (
    EPSILON,
    TIER_NAMES,
    Context,
    RuleClause,
    Strict,
    Tier,
    TierAmounts,
    highest_tier,
    is_inr,
)


class MealsParams(Strict):
    daily_limit: TierAmounts


class MealsClause(RuleClause):
    """5.1 All of one day's meal bills together must be within the daily limit for the city tier.

    This needs every meal of the day, so it is a claim-level check. Meals without a readable date
    cannot be placed on a day and are skipped (the claims module asks for the date). If a day's
    bills come from cities of both tiers, the higher limit applies.
    """

    rule: Literal["meals_daily_limit"]
    params: MealsParams

    def check_claim(
        self, claim: Claim, docs: Sequence[ProcessedDocument], ctx: Context
    ) -> list[Finding]:
        by_day: dict[date, list[ProcessedDocument]] = defaultdict(list)
        for doc in docs:
            day = doc.expense_date
            if doc.category is ExpenseCategory.meals and day is not None and is_inr(doc):
                by_day[day].append(doc)
        findings: list[Finding] = []
        for day, meals in sorted(by_day.items()):
            tiers: set[Tier] = {ctx.tier_for(m.receipt.merchant_city, claim.city) for m in meals}
            tier = highest_tier(tiers)
            limit = self.params.daily_limit.for_tier(tier)
            total = round(sum(m.amount for m in meals), 2)
            if total <= limit + EPSILON:
                continue
            bills = f"{len(meals)} bills" if len(meals) > 1 else "one bill"
            findings.append(
                self.finding(
                    PolicyCode.meals_over_limit,
                    f"Meals on {day:%d %b %Y} add up to {format_inr(total)} ({bills}), above the "
                    f"{format_inr(limit)} daily limit for {TIER_NAMES[tier]} cities.",
                    fields=("total",),
                    expected=limit,
                    actual=total,
                )
            )
        return findings


class EntertainmentParams(Strict):
    per_head_cap: float = Field(gt=0)


class EntertainmentClause(RuleClause):
    """5.2 Client entertainment: a per-head cap, once we know how many people were there.

    The attendee count comes from the employee's (or the calendar's) answer to the ``attendees``
    question on the claim, so the check only runs after that is answered; call the policy again
    after answers change. Naming the attendees and the purpose is requested by the claims module.
    The cap applies after removing alcohol, which clause 6.1 already deals with.
    """

    rule: Literal["entertainment"]
    params: EntertainmentParams

    def check_claim(
        self, claim: Claim, docs: Sequence[ProcessedDocument], ctx: Context
    ) -> list[Finding]:
        findings: list[Finding] = []
        for doc in docs:
            if doc.category is not ExpenseCategory.client_entertainment or not is_inr(doc):
                continue
            answer = next(
                (
                    q.answer
                    for q in claim.open_questions
                    if q.kind is QuestionKind.attendees
                    and q.answered
                    and q.answer
                    and doc.id in q.document_ids
                ),
                None,
            )
            headcount = attendee_headcount(answer, ctx.employee.name) if answer else None
            if answer and not headcount:  # answered, but nothing in it can be counted
                findings.append(
                    self.finding(
                        PolicyCode.headcount_unclear,
                        "How many people attended could not be worked out from the answer, so "
                        "the per-head cap was not checked: finance needs to look at this one.",
                        fields=("total",),
                    ).for_document(doc.id)
                )
            if not headcount:
                continue
            net = doc.amount - sum(item.amount for item in alcohol_items(doc.receipt))
            per_head = round(net / headcount, 2)
            cap = self.params.per_head_cap
            if per_head <= cap + EPSILON:
                continue
            findings.append(
                self.finding(
                    PolicyCode.entertainment_over_cap,
                    f"This client entertainment costs {format_inr(per_head)} per head "
                    f"({format_inr(net)} for {headcount} people, alcohol excluded), above the "
                    f"{format_inr(cap)} per-head cap.",
                    fields=("total",),
                    expected=cap,
                    actual=per_head,
                ).for_document(doc.id)
            )
        return findings


class AlcoholParams(Strict):
    probability_threshold: float = Field(gt=0, le=1)


class AlcoholClause(RuleClause):
    """6.1 Alcohol is never reimbursable.

    Raised when System One says alcohol is likely (``decisions.alcohol_present`` at or above the
    threshold) or when a line item names an alcoholic drink; either signal is enough. ``expected``
    is the amount to deduct (the alcohol lines) when the line items allow working it out, and
    ``actual`` is the bill total. Item names are deliberately not copied into the message.
    """

    rule: Literal["alcohol"]
    params: AlcoholParams

    def check_document(self, doc: ProcessedDocument, ctx: Context) -> list[Finding]:
        items = alcohol_items(doc.receipt)
        likely = doc.decisions.alcohol_present >= self.params.probability_threshold
        if not items and not likely:
            return []
        if items:
            excluded = round(sum(item.amount for item in items), 2)
            noun = "line" if len(items) == 1 else "lines"
            message = (
                f"This bill includes alcohol ({len(items)} {noun}, {format_inr(excluded)}). "
                "Alcohol is not reimbursable, so that amount has to be taken out before it is paid."
            )
            return [
                self.finding(
                    PolicyCode.alcohol_not_reimbursable,
                    message,
                    fields=("line_items",),
                    expected=excluded,
                    actual=doc.receipt.total,
                )
            ]
        return [
            self.finding(
                PolicyCode.alcohol_not_reimbursable,
                "This bill appears to include alcohol, which is not reimbursable. The items do "
                "not say how much, so someone has to work out the amount to take out.",
                fields=("line_items",),
                actual=doc.receipt.total,
            )
        ]
