"""8.x Mobile and internet monthly cap, 9.x Learning pre-approval."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Literal

from pydantic import Field

from claimpilot.domain import Claim, ExpenseCategory, Finding, ProcessedDocument
from claimpilot.policy.codes import PolicyCode
from claimpilot.policy.money import format_inr
from claimpilot.policy.rules.base import (
    EPSILON,
    Context,
    RuleClause,
    Strict,
    grade_coverage_error,
    is_inr,
)


class MobileCapParams(Strict):
    monthly_cap: dict[str, float]


class MobileCapClause(RuleClause):
    """8.1 Mobile and internet spend in a calendar month is capped by grade.

    A bill is a month's charges, so one bill above the cap is flagged on its own document. Several
    bills in the same month (say mobile plus broadband) are also added up, and flagged on the
    claim only when no single bill was already over the cap, so nothing is reported twice.
    """

    rule: Literal["mobile_cap"]
    params: MobileCapParams

    def validate_grades(self, grades: Sequence[str]) -> None:
        problem = grade_coverage_error(list(self.params.monthly_cap), grades, f"clause {self.id}")
        if problem:
            raise ValueError(problem)
        if any(cap <= 0 for cap in self.params.monthly_cap.values()):
            raise ValueError(f"clause {self.id}: monthly caps must be positive")

    def check_document(self, doc: ProcessedDocument, ctx: Context) -> list[Finding]:
        if doc.category is not ExpenseCategory.mobile_internet or not is_inr(doc):
            return []
        cap = self.params.monthly_cap.get(ctx.grade)
        if cap is None:
            return [self.unknown_grade(ctx)]
        if doc.amount <= cap + EPSILON:
            return []
        return [
            self.finding(
                PolicyCode.mobile_over_cap,
                f"This mobile or internet bill is {format_inr(doc.amount)}, above the "
                f"{format_inr(cap)} monthly cap for grade {ctx.grade}.",
                fields=("total",),
                expected=cap,
                actual=doc.amount,
            )
        ]

    def check_claim(
        self, claim: Claim, docs: Sequence[ProcessedDocument], ctx: Context
    ) -> list[Finding]:
        cap = self.params.monthly_cap.get(ctx.grade)
        if cap is None:
            return []  # check_document already said so, once per bill
        by_month: dict[tuple[int, int], list[ProcessedDocument]] = defaultdict(list)
        for doc in docs:
            day = doc.expense_date
            if doc.category is ExpenseCategory.mobile_internet and day is not None and is_inr(doc):
                by_month[(day.year, day.month)].append(doc)
        findings: list[Finding] = []
        for (year, month), bills in sorted(by_month.items()):
            total = round(sum(b.amount for b in bills), 2)
            already_flagged = any(b.amount > cap + EPSILON for b in bills)
            if len(bills) < 2 or already_flagged or total <= cap + EPSILON:
                continue
            findings.append(
                self.finding(
                    PolicyCode.mobile_over_cap,
                    f"Mobile and internet bills for {date(year, month, 1):%b %Y} add up to "
                    f"{format_inr(total)} ({len(bills)} bills), above the {format_inr(cap)} "
                    f"monthly cap for grade {ctx.grade}.",
                    fields=("total",),
                    expected=cap,
                    actual=total,
                )
            )
        return findings


class LearningPreapprovalParams(Strict):
    threshold: float = Field(gt=0)


class LearningPreapprovalClause(RuleClause):
    """9.1 Learning above the threshold needs the manager's approval before purchase.

    The claim cannot prove an approval exists, so this is a warning for the approver to check
    rather than a block.
    """

    rule: Literal["learning_preapproval"]
    params: LearningPreapprovalParams

    def check_document(self, doc: ProcessedDocument, ctx: Context) -> list[Finding]:
        if doc.category is not ExpenseCategory.learning or not is_inr(doc):
            return []
        threshold = self.params.threshold
        if doc.amount <= threshold + EPSILON:
            return []
        return [
            self.finding(
                PolicyCode.preapproval_required,
                f"This learning expense is {format_inr(doc.amount)}, above "
                f"{format_inr(threshold)}, so it needs your manager's approval from before the "
                "purchase.",
                fields=("total",),
                expected=threshold,
                actual=doc.amount,
            )
        ]
