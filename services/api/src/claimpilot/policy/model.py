"""``Policy``: the parsed, validated policy document and its evaluation entry points."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from claimpilot.config import API_ROOT
from claimpilot.domain import (
    Claim,
    Employee,
    Finding,
    FindingSource,
    ProcessedDocument,
    Severity,
)
from claimpilot.policy.codes import PolicyCode
from claimpilot.policy.rules import (
    REQUIRED_RULES,
    CityTiersClause,
    Clause,
    ClauseBase,
    Context,
    PersonalExpenseClause,
    ReceiptsClause,
    RuleClause,
)
from claimpilot.policy.rules.base import is_inr

DEFAULT_POLICY_PATH = API_ROOT / "config" / "policy.yaml"


class UnknownClause(KeyError):
    """There is no clause with that number in this policy."""


class Policy(BaseModel):
    """A company expense policy as code: numbered clauses with quotable text and parameters.

    Parsed from ``config/policy.yaml``. Unknown keys are rejected anywhere in the file, every
    enforced rule must be present exactly once, and grade tables must cover exactly ``grades``,
    so a bad edit fails at load time rather than silently disabling a check. Evaluation is pure
    and deterministic: no network, no LLM, no clock (pass ``today``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    effective_date: date
    currency: Literal["INR"] = "INR"
    synthetic: bool = Field(
        default=True, description="A made-up demo policy, not a real employer's"
    )
    grades: tuple[str, ...] = Field(min_length=1, description="Junior to senior, e.g. L1..L5")
    clauses: tuple[Clause, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_structure(self) -> Self:
        ids = [c.id for c in self.clauses]
        if dupes := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"duplicate clause ids: {dupes}")
        rules = [c.rule for c in self.clauses if c.rule != "info"]
        if missing := sorted(REQUIRED_RULES - set(rules)):
            raise ValueError(f"policy has no clause for rule(s): {missing}")
        if repeated := sorted({r for r in rules if rules.count(r) > 1}):
            raise ValueError(f"rule(s) appear in more than one clause: {repeated}")
        if len(set(self.grades)) != len(self.grades) or any(
            g != g.strip().upper() or not g for g in self.grades
        ):
            raise ValueError("grades must be unique, upper-case names such as 'L3'")
        for clause in self.clauses:
            clause.validate_grades(self.grades)
        return self

    # --- loading and lookup ----------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> Policy:
        """Read and validate a policy file (default: ``services/api/config/policy.yaml``)."""
        target = path or DEFAULT_POLICY_PATH
        return cls.model_validate(yaml.safe_load(target.read_text(encoding="utf-8")))

    @property
    def title(self) -> str:
        suffix = " (synthetic)" if self.synthetic else ""
        return f"{self.name} v{self.version}{suffix}"

    def clause(self, clause_id: str) -> Clause:
        """The clause numbered ``clause_id`` (e.g. ``"4.1"``); ``UnknownClause`` if absent."""
        for clause in self.clauses:
            if clause.id == clause_id:
                return clause
        raise UnknownClause(clause_id)

    def _one[C: ClauseBase](self, kind: type[C]) -> C:
        """The one clause of this class (``load`` guarantees each enforced rule appears once)."""
        return next(c for c in self.clauses if isinstance(c, kind))

    @property
    def receipt_threshold(self) -> float:
        """Expenses above this need a receipt; conveyance up to it can be self-declared (3.1)."""
        return self._one(ReceiptsClause).params.receipt_threshold

    @property
    def personal_threshold(self) -> float:
        """System One probability at or above which an expense is treated as personal (10.1)."""
        return self._one(PersonalExpenseClause).params.probability_threshold

    # --- evaluation ------------------------------------------------------------------------

    def _context(self, employee: Employee, today: date | None) -> Context:
        return Context(
            employee=employee,
            today=today or date.today(),
            tiers=self._one(CityTiersClause),
            grades=self.grades,
        )

    def evaluate_document(
        self, employee: Employee, doc: ProcessedDocument, *, today: date | None = None
    ) -> list[Finding]:
        """Rules decidable from one document. Every finding cites its clause and the document.

        ``today`` is accepted so both entry points share a signature and tests can pin the clock;
        no document-level rule depends on it today (timing is a claim-level rule, 2.1).
        """
        ctx = self._context(employee, today)
        findings: list[Finding] = []
        for clause in self.clauses:
            if isinstance(clause, RuleClause):
                findings += clause.check_document(doc, ctx)
        if not is_inr(doc):
            findings.append(self._not_rupees(doc))
        return [f.for_document(doc.id) for f in findings]

    def evaluate_claim(
        self,
        employee: Employee,
        claim: Claim,
        docs: Sequence[ProcessedDocument],
        *,
        today: date | None = None,
    ) -> list[Finding]:
        """Rules that need several documents: daily totals, per-trip limits, late submission.

        Only the claim's own documents are considered. Per-document findings are not repeated
        here; use :meth:`evaluate_document` for those. Findings about one specific document
        carry its id; the rest are about the claim as a whole.
        """
        ctx = self._context(employee, today)
        wanted = set(claim.document_ids)
        members = [d for d in docs if d.id in wanted]
        findings: list[Finding] = []
        for clause in self.clauses:
            if isinstance(clause, RuleClause):
                findings += clause.check_claim(claim, members, ctx)
        return findings

    @staticmethod
    def _not_rupees(doc: ProcessedDocument) -> Finding:
        return Finding(
            code=PolicyCode.currency_not_inr.value,
            severity=Severity.info,
            message=f"This document is in {doc.receipt.currency}. The policy limits are in "
            "rupees, so the amount limits were not applied to it.",
            source=FindingSource.policy,
            fields=("currency",),
            actual=doc.receipt.currency,
        )
