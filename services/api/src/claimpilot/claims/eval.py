"""Grouping evaluation on the golden set: does ``group_documents`` recover the generator's trips?

For every persona, the golden documents are turned into ``ProcessedDocument``s (perfect
extraction), grouped, and compared with the ground-truth ``trip_id``. The metric is pairwise: a pair
of documents is *positive* when both belong to the same trip, and the grouping predicts positive
when it puts them in the same trip claim. Precision and recall over those pairs, F1 their mean.

    python -m claimpilot.claims.eval [--dataset data/synth/golden] [--today 2026-09-30]

The objective is >= 0.9 precision and recall on the non-adversarial documents. Adversarial
documents (duplicates, injections, tampering, over-policy, missing dates) are reported separately
because the grouping is not expected to repair them.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import combinations
from pathlib import Path

from claimpilot.claims.grouping import group_documents
from claimpilot.claims.trips import is_stay, is_ticket
from claimpilot.config import REPO_ROOT
from claimpilot.domain import Claim, ClaimMode
from claimpilot.evals.golden import GOLDEN_DIR, GoldenCase, load_golden, to_processed

ADVERSARIAL_TAGS = frozenset({"duplicate", "injection", "tampered", "over_policy", "missing_date"})
# The dataset spans 3 Jul to 27 Sep 2026; this is the end of that quarter, so every claim in it
# is still inside the policy's 90-day submission window. Pinned so results never depend on the
# wall clock.
CALIBRATION_TODAY = date(2026, 9, 30)


@dataclass(frozen=True, slots=True)
class PairScores:
    documents: int
    true_pairs: int  # pairs the ground truth says share a trip
    predicted_pairs: int  # pairs the grouping put in the same trip claim
    correct_pairs: int

    @property
    def precision(self) -> float:
        return self.correct_pairs / self.predicted_pairs if self.predicted_pairs else 1.0

    @property
    def recall(self) -> float:
        return self.correct_pairs / self.true_pairs if self.true_pairs else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


@dataclass(frozen=True, slots=True)
class Stray:
    """A document with no ``trip_id`` that ended up in a trip claim."""

    document_id: str
    claim_title: str
    justified: bool
    reason: str


@dataclass(frozen=True, slots=True)
class GroupingReport:
    clean: PairScores  # non-adversarial documents: the objective
    everything: PairScores
    strays: tuple[Stray, ...]
    claims: int
    trip_claims: int

    @property
    def unjustified_strays(self) -> tuple[Stray, ...]:
        return tuple(s for s in self.strays if not s.justified)


def is_adversarial(case: GoldenCase) -> bool:
    return bool(ADVERSARIAL_TAGS & set(case.tags))


def _scores(cases: Sequence[GoldenCase], placed: dict[str, Claim]) -> PairScores:
    by_persona: dict[str, list[GoldenCase]] = defaultdict(list)
    for case in cases:
        by_persona[case.employee.id].append(case)
    true_pairs = predicted = correct = 0
    for members in by_persona.values():
        for a, b in combinations(members, 2):
            same_truth = a.truth.trip_id is not None and a.truth.trip_id == b.truth.trip_id
            ca, cb = placed[a.truth.id], placed[b.truth.id]
            same_pred = ca.id == cb.id and ca.mode is ClaimMode.trip
            true_pairs += same_truth
            predicted += same_pred
            correct += same_truth and same_pred
    return PairScores(len(cases), true_pairs, predicted, correct)


def evaluate_grouping(
    cases: Sequence[GoldenCase], *, today: date = CALIBRATION_TODAY
) -> GroupingReport:
    """Group each persona's documents and score the trips against the ground-truth ``trip_id``."""
    by_persona: dict[str, list[GoldenCase]] = defaultdict(list)
    for case in cases:
        by_persona[case.employee.id].append(case)
    placed: dict[str, Claim] = {}
    strays: list[Stray] = []
    claim_count = trip_count = 0
    for members in by_persona.values():
        docs = {c.truth.id: to_processed(c.truth) for c in members}
        claims = group_documents(members[0].employee, list(docs.values()), today=today)
        claim_count += len(claims)
        for claim in claims:
            trip_count += claim.mode is ClaimMode.trip
            for doc_id in claim.document_ids:
                placed[doc_id] = claim
        truth = {c.truth.id: c.truth for c in members}
        for claim in claims:
            if claim.mode is not ClaimMode.trip:
                continue
            for doc_id in claim.document_ids:
                if truth[doc_id].trip_id is None:
                    anchor = is_ticket(docs[doc_id]) or is_stay(docs[doc_id])
                    strays.append(
                        Stray(
                            doc_id,
                            claim.title,
                            justified=anchor,
                            reason=(
                                "stand-alone ticket or hotel away from the base city"
                                if anchor
                                else "pulled in by date and city"
                            ),
                        )
                    )
    clean = [c for c in cases if not is_adversarial(c)]
    return GroupingReport(
        clean=_scores(clean, placed),
        everything=_scores(cases, placed),
        strays=tuple(sorted(strays, key=lambda s: s.document_id)),
        claims=claim_count,
        trip_claims=trip_count,
    )


def format_report(report: GroupingReport, dataset: Path, today: date) -> str:
    lines = [
        f"Grouping evaluation on {dataset} (today pinned to {today.isoformat()})",
        f"{report.claims} claims, {report.trip_claims} of them trips",
        "",
        f"{'documents':<28}{'pairs':>8}{'precision':>11}{'recall':>9}{'F1':>7}",
    ]
    for label, scores in (
        (f"non-adversarial ({report.clean.documents})", report.clean),
        (f"all ({report.everything.documents})", report.everything),
    ):
        lines.append(
            f"{label:<28}{scores.true_pairs:>8}{scores.precision:>11.3f}"
            f"{scores.recall:>9.3f}{scores.f1:>7.3f}"
        )
    lines += ["", "Documents with no trip_id inside a trip claim:"]
    if report.strays:
        lines += [
            f"  {s.document_id}  {s.claim_title}  [{'justified' if s.justified else 'UNJUSTIFIED'}"
            f": {s.reason}]"
            for s in report.strays
        ]
    else:
        lines.append("  none")
    return "\n".join(lines)


def _resolve(path: Path) -> Path:
    """Accept a dataset path relative to the current directory or to the repository root."""
    return path if path.is_absolute() or path.exists() else REPO_ROOT / path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m claimpilot.claims.eval", description=__doc__)
    parser.add_argument("--dataset", type=Path, default=GOLDEN_DIR, help="golden dataset directory")
    parser.add_argument("--today", type=date.fromisoformat, default=CALIBRATION_TODAY)
    args = parser.parse_args(argv)
    dataset = _resolve(args.dataset)
    report = evaluate_grouping(load_golden(dataset), today=args.today)
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):  # Windows consoles default to a legacy code page
        reconfigure(encoding="utf-8")
    print(format_report(report, dataset, args.today))
    ok = report.clean.precision >= 0.9 and report.clean.recall >= 0.9
    return 0 if ok and not any(not s.justified for s in report.strays) else 1


if __name__ == "__main__":
    raise SystemExit(main())
