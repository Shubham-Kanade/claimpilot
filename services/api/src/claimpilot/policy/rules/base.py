"""Building blocks shared by every rule clause.

A *clause* is one numbered paragraph of the policy: its quotable wording plus the parameters the
code enforces. Rule clauses also know how to check a document or a claim against themselves, so a
finding always cites the clause that produced it. Adding a rule means adding a clause class.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from claimpilot.domain import (
    Claim,
    Employee,
    Finding,
    FindingSource,
    ProcessedDocument,
    Severity,
)
from claimpilot.policy.codes import PolicyCode

Tier = Literal["tier1", "tier2"]
TIER_NAMES: dict[Tier, str] = {"tier1": "Tier-1", "tier2": "Tier-2"}
# Currency is rounded to paise; compare with a hair of slack so 9,000.00 is not "over" 9,000.
EPSILON = 0.005


class Strict(BaseModel):
    """Immutable, and unknown keys are an error: a typo in policy.yaml must not pass silently."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class TierAmounts(Strict):
    """A limit that differs between Tier-1 and Tier-2 cities."""

    tier1: float = Field(gt=0)
    tier2: float = Field(gt=0)

    def for_tier(self, tier: Tier) -> float:
        return self.tier1 if tier == "tier1" else self.tier2


class TierLookup(Protocol):
    def tier_of(self, city: str | None) -> Tier | None: ...


@dataclass(frozen=True, slots=True)
class Context:
    """What a rule needs to know besides the document or claim itself."""

    employee: Employee
    today: date
    tiers: TierLookup
    grades: tuple[str, ...]

    @property
    def grade(self) -> str:
        return self.employee.grade.strip().upper()

    def tier_for(self, *cities: str | None) -> Tier:
        """Tier of the first city that is known, else of the employee's base city, else Tier-2."""
        for city in (*cities, self.employee.base_city):
            tier = self.tiers.tier_of(city)
            if tier is not None:
                return tier
        return "tier2"


def highest_tier(tiers: set[Tier]) -> Tier:
    """Tier-1 if any of the tiers is Tier-1: when a day spans both, the higher limit applies."""
    return "tier1" if "tier1" in tiers else "tier2"


def is_inr(doc: ProcessedDocument) -> bool:
    """Amount limits are in rupees, so they are only applied to rupee documents."""
    return doc.receipt.currency.strip().upper() == "INR"


class ClauseBase(Strict):
    """Id, title and quotable text: what every clause has, including purely informational ones."""

    id: str = Field(pattern=r"^\d{1,2}\.\d{1,2}$", description="Clause number, e.g. '4.1'")
    title: str = Field(min_length=1)
    text: str = Field(min_length=1, description="Plain-English wording, quoted to users")

    def validate_grades(self, grades: Sequence[str]) -> None:
        """Raise ``ValueError`` if a grade table does not cover exactly the policy's grades."""


class RuleClause(ClauseBase):
    """A clause that is enforced: it can raise findings about a document or a whole claim."""

    severity: Severity

    def check_document(self, doc: ProcessedDocument, ctx: Context) -> list[Finding]:
        """Findings decidable from one document alone."""
        return []

    def check_claim(
        self, claim: Claim, docs: Sequence[ProcessedDocument], ctx: Context
    ) -> list[Finding]:
        """Findings that need several documents (daily totals, per-trip limits, timing)."""
        return []

    def finding(
        self,
        code: PolicyCode,
        message: str,
        *,
        severity: Severity | None = None,
        fields: tuple[str, ...] = (),
        expected: float | str | None = None,
        actual: float | str | None = None,
    ) -> Finding:
        """A policy finding that cites this clause (id and quoted text)."""
        return Finding(
            code=code.value,
            severity=severity or self.severity,
            message=message,
            fields=fields,
            expected=expected,
            actual=actual,
            source=FindingSource.policy,
            clause_id=self.id,
            clause_text=self.text,
        )

    def unknown_grade(self, ctx: Context) -> Finding:
        """Say so, rather than silently skipping a grade-based limit."""
        return self.finding(
            PolicyCode.grade_not_in_policy,
            f"Grade {ctx.employee.grade!r} is not one of the policy's grades, so the limit in "
            f"clause {self.id} ({self.title.lower()}) could not be applied.",
            severity=Severity.warn,
        )


def grade_coverage_error(table: Sequence[str], grades: Sequence[str], what: str) -> str | None:
    """Message when ``table`` (grade keys) does not match ``grades`` exactly, else ``None``."""
    missing, extra = set(grades) - set(table), set(table) - set(grades)
    if not missing and not extra:
        return None
    parts = []
    if missing:
        parts.append(f"missing {sorted(missing)}")
    if extra:
        parts.append(f"unknown {sorted(extra)}")
    return f"{what}: grades do not match the policy's grades ({'; '.join(parts)})"
