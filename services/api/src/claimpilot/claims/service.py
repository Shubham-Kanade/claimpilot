"""Composing the pieces for the pipeline: group, check policy, ask questions, set the status.

Grouping, policy and questions are separate pure functions on purpose; this module is the one
place that wires them together so the worker and the API do it the same way.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from claimpilot.claims.grouping import group_documents
from claimpilot.claims.questions import build_questions
from claimpilot.claims.state import refresh_status
from claimpilot.domain import Claim, Employee, Finding, FindingSource, ProcessedDocument
from claimpilot.policy import Policy

_Key = tuple[str, str | None, str | None, str | None]


def _key(finding: Finding) -> _Key:
    return (finding.code, finding.document_id, finding.clause_id, finding.message)


def finalize_claim(
    claim: Claim,
    docs: Sequence[ProcessedDocument],
    policy: Policy,
    employee: Employee,
    *,
    today: date | None = None,
) -> Claim:
    """Attach every finding, build the questions and refresh the status of a grouped claim.

    ``claim.findings`` ends up holding all the findings that matter for the claim: those already on
    its documents (trust, decisions), the policy findings per document and the claim-level policy
    findings. That one list is what :func:`route` and the approver view read.

    Safe to call again after answers or documents change: policy findings are always recomputed
    from scratch (a per-head cap check appears once the attendees are answered, and policy
    findings stored earlier on a document are replaced, not doubled), findings from other sources
    are kept, duplicates are dropped, and answers already given are carried over.
    """
    wanted = set(claim.document_ids)
    members = [d for d in docs if d.id in wanted]
    # Claim-level findings from other sources stay; policy findings are recomputed below.
    kept = [
        f for f in claim.findings if f.source is not FindingSource.policy and f.document_id is None
    ]
    findings: list[Finding] = []
    seen: set[_Key] = set()

    def add(items: Sequence[Finding], document_id: str | None = None) -> None:
        for item in items:
            stamped = (
                item if item.document_id or not document_id else item.for_document(document_id)
            )
            if (key := _key(stamped)) not in seen:
                seen.add(key)
                findings.append(stamped)

    add(kept)
    for doc in members:
        add([f for f in doc.findings if f.source is not FindingSource.policy], doc.id)
        add(policy.evaluate_document(employee, doc, today=today), doc.id)
    add(policy.evaluate_claim(employee, claim, members, today=today))

    updated = claim.model_copy(update={"findings": findings})
    questions = build_questions(
        updated,
        members,
        self_declaration_limit=policy.receipt_threshold,
        personal_threshold=policy.personal_threshold,
    )
    return refresh_status(updated.model_copy(update={"open_questions": questions}))


def build_claims(
    employee: Employee,
    docs: Sequence[ProcessedDocument],
    policy: Policy,
    *,
    today: date | None = None,
    id_prefix: str = "clm",
) -> list[Claim]:
    """Group an employee's documents and finalize every claim: the whole pile in one call."""
    by_id = {d.id: d for d in docs}
    return [
        finalize_claim(claim, [by_id[i] for i in claim.document_ids], policy, employee, today=today)
        for claim in group_documents(employee, docs, today=today, id_prefix=id_prefix)
    ]
