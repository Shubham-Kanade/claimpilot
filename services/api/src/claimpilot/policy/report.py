"""Calibration report: do the policy rules agree with the golden set?

    python -m claimpilot.policy.report [--dataset data/synth/golden] [--policy FILE] [--today DATE]

Every golden document is run through the same path the pipeline uses (group into claims, evaluate
each document and each claim) and the findings are compared with labels:

* ``dataset tag``: the generator labelled the document ``over_policy`` (an alcohol dinner or a
  hotel night above 10,000) or ``missing_date``.
* ``clause arithmetic``: the clause itself decides (a course above the 9.1 threshold must carry a
  pre-approval warning), because the dataset has no tag for it.

Everything else is expected to raise nothing, so any other finding on a golden document counts as a
false positive. Codes with no labelled positive in the dataset show ``n/a`` for recall; their
behaviour is covered by unit tests instead.

Two objectives decide the exit code: (a) no ``high`` finding on a document without an adversarial
tag, and (b) every ``over_policy`` document gets at least one ``high`` finding.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from claimpilot.claims import build_claims
from claimpilot.claims.eval import CALIBRATION_TODAY, is_adversarial
from claimpilot.config import REPO_ROOT
from claimpilot.domain import DocType, ExpenseCategory, Severity
from claimpilot.evals.golden import GOLDEN_DIR, GoldenCase, has_alcohol, load_golden, to_processed
from claimpilot.policy.codes import PolicyCode
from claimpilot.policy.model import Policy
from claimpilot.policy.rules import LearningPreapprovalClause, ReceiptsClause, RuleClause

# Which rule (``rule:`` in policy.yaml) raises which code, to show clause and severity per row.
_RULE_OF_CODE: dict[PolicyCode, str] = {
    PolicyCode.late_submission: "submission_window",
    PolicyCode.receipt_required: "receipts",
    PolicyCode.hotel_over_cap: "hotel_cap",
    PolicyCode.meals_over_limit: "meals_daily_limit",
    PolicyCode.entertainment_over_cap: "entertainment",
    PolicyCode.alcohol_not_reimbursable: "alcohol",
    PolicyCode.air_class_not_economy: "air_class",
    PolicyCode.train_class_above_grade: "train_class",
    PolicyCode.trip_conveyance_over_limit: "trip_conveyance",
    PolicyCode.mobile_over_cap: "mobile_cap",
    PolicyCode.preapproval_required: "learning_preapproval",
    PolicyCode.personal_expense_flagged: "personal_expense",
}
Unit = tuple[str, str]  # (document id, or "claim:<id>" for a whole claim, and the finding code)


@dataclass(frozen=True, slots=True)
class CodeRow:
    code: str
    severity: str
    clause_id: str
    flagged: int
    tp: int
    fp: int
    fn: int
    labels: str

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None


@dataclass(frozen=True, slots=True)
class PolicyReport:
    policy_title: str
    documents: int
    clean_documents: int
    rows: tuple[CodeRow, ...]
    high_on_clean: tuple[Unit, ...]  # objective (a): must be empty
    over_policy_total: int
    over_policy_flagged: int  # objective (b): must equal over_policy_total

    @property
    def passed(self) -> bool:
        return not self.high_on_clean and self.over_policy_flagged == self.over_policy_total


def expected_findings(case: GoldenCase, policy: Policy) -> dict[str, str]:
    """Codes this golden document should raise, with where the label comes from."""
    truth = case.truth
    receipt = truth.receipt
    total = receipt.total or 0.0
    expected: dict[str, str] = {}
    if "over_policy" in case.tags:
        if receipt.doc_type is DocType.hotel_folio:
            expected[PolicyCode.hotel_over_cap.value] = "dataset tag"
        elif has_alcohol(truth):
            expected[PolicyCode.alcohol_not_reimbursable.value] = "dataset tag"
    receipts = next(c for c in policy.clauses if isinstance(c, ReceiptsClause))
    if "missing_date" in case.tags and total > receipts.params.receipt_threshold:
        expected[PolicyCode.receipt_required.value] = "dataset tag"
    learning = next(c for c in policy.clauses if isinstance(c, LearningPreapprovalClause))
    if truth.category is ExpenseCategory.learning and total > learning.params.threshold:
        expected[PolicyCode.preapproval_required.value] = "clause arithmetic"
    return expected


def evaluate_policy(
    cases: Sequence[GoldenCase],
    policy: Policy | None = None,
    *,
    today: date = CALIBRATION_TODAY,
) -> PolicyReport:
    """Run the policy over the golden cases and score it against the labels."""
    policy = policy or Policy.load()
    by_persona: dict[str, list[GoldenCase]] = defaultdict(list)
    for case in cases:
        by_persona[case.employee.id].append(case)
    by_id = {c.truth.id: c for c in cases}

    flagged: set[Unit] = set()
    high_docs: set[str] = set()
    high_on_clean: list[Unit] = []
    for members in by_persona.values():
        docs = [to_processed(c.truth) for c in members]
        for claim in build_claims(members[0].employee, docs, policy, today=today):
            claim_clean = all(not is_adversarial(by_id[i]) for i in claim.document_ids)
            for finding in claim.findings:
                unit = (finding.document_id or f"claim:{claim.id}", finding.code)
                flagged.add(unit)
                if finding.severity is not Severity.high:
                    continue
                if finding.document_id:
                    high_docs.add(finding.document_id)
                clean = (
                    not is_adversarial(by_id[finding.document_id])
                    if finding.document_id
                    else claim_clean
                )
                if clean:
                    high_on_clean.append(unit)

    expected: dict[Unit, str] = {}
    for case in cases:
        for code, source in expected_findings(case, policy).items():
            expected[(case.truth.id, code)] = source

    rows = []
    for code, rule in _RULE_OF_CODE.items():
        clause = next(c for c in policy.clauses if c.rule == rule)
        mine = {u for u in flagged if u[1] == code.value}
        wanted = {u for u in expected if u[1] == code.value}
        rows.append(
            CodeRow(
                code=code.value,
                severity=clause.severity.value if isinstance(clause, RuleClause) else "-",
                clause_id=clause.id,
                flagged=len(mine),
                tp=len(mine & wanted),
                fp=len(mine - wanted),
                fn=len(wanted - mine),
                labels=" + ".join(sorted({expected[u] for u in wanted})) or "none in dataset",
            )
        )
    over_policy = [c.truth.id for c in cases if "over_policy" in c.tags]
    return PolicyReport(
        policy_title=policy.title,
        documents=len(cases),
        clean_documents=sum(not is_adversarial(c) for c in cases),
        rows=tuple(rows),
        high_on_clean=tuple(sorted(high_on_clean)),
        over_policy_total=len(over_policy),
        over_policy_flagged=sum(doc_id in high_docs for doc_id in over_policy),
    )


def _ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def format_report(report: PolicyReport, dataset: Path, today: date) -> str:
    lines = [
        f"{report.policy_title} on {dataset}",
        f"{report.documents} documents ({report.clean_documents} without an adversarial tag); "
        f"today pinned to {today.isoformat()}",
        "",
        f"{'code':<28}{'sev':<6}{'clause':<8}{'flagged':>8}{'TP':>4}{'FP':>4}{'FN':>4}"
        f"{'precision':>11}{'recall':>8}  labels",
    ]
    for row in report.rows:
        lines.append(
            f"{row.code:<28}{row.severity:<6}{row.clause_id:<8}{row.flagged:>8}{row.tp:>4}"
            f"{row.fp:>4}{row.fn:>4}{_ratio(row.precision):>11}{_ratio(row.recall):>8}"
            f"  {row.labels}"
        )
    a_status = "PASS" if not report.high_on_clean else f"FAIL {list(report.high_on_clean)}"
    b_ok = report.over_policy_flagged == report.over_policy_total
    lines += [
        "",
        f"(a) high findings on non-adversarial documents: {len(report.high_on_clean)} "
        f"of {report.clean_documents}  {a_status}",
        f"(b) over_policy documents with a high finding: {report.over_policy_flagged} "
        f"of {report.over_policy_total}  {'PASS' if b_ok else 'FAIL'}",
    ]
    return "\n".join(lines)


def _resolve(path: Path) -> Path:
    """Accept a dataset path relative to the current directory or to the repository root."""
    return path if path.is_absolute() or path.exists() else REPO_ROOT / path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m claimpilot.policy.report", description=__doc__)
    parser.add_argument("--dataset", type=Path, default=GOLDEN_DIR, help="golden dataset directory")
    parser.add_argument("--policy", type=Path, default=None, help="policy file (default: config)")
    parser.add_argument("--today", type=date.fromisoformat, default=CALIBRATION_TODAY)
    args = parser.parse_args(argv)
    dataset = _resolve(args.dataset)
    report = evaluate_policy(load_golden(dataset), Policy.load(args.policy), today=args.today)
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):  # Windows consoles default to a legacy code page
        reconfigure(encoding="utf-8")
    print(format_report(report, dataset, args.today))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
