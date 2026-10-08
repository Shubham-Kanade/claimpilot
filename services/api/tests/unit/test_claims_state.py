"""The claim state machine and routing: valid and invalid transitions, idempotency, purity."""

from __future__ import annotations

import pytest

from claimpilot.claims import (
    AUTO_APPROVE_LIMIT,
    InvalidTransition,
    UnknownQuestion,
    answer_question,
    confirm_and_submit,
    decide,
    refresh_status,
    route,
)
from claimpilot.domain import (
    Claim,
    ClaimMode,
    ClaimStatus,
    Finding,
    FindingSource,
    OpenQuestion,
    QuestionKind,
    Severity,
)

LOCKED = [ClaimStatus.submitted, ClaimStatus.approved, ClaimStatus.rejected]


def question(qid: str = "q1", answer: str | None = None) -> OpenQuestion:
    return OpenQuestion(
        id=qid, kind=QuestionKind.business_purpose, text="Why?", document_ids=["d1"], answer=answer
    )


def claim(
    status: ClaimStatus = ClaimStatus.draft,
    *,
    questions: list[OpenQuestion] | None = None,
    findings: list[Finding] | None = None,
    total: float = 500.0,
    documents: list[str] | None = None,
    reference: str | None = None,
) -> Claim:
    return Claim(
        id="clm-P001-abc",
        employee_id="P001",
        title="Pune trip 12-14 Aug 2026",
        mode=ClaimMode.trip,
        status=status,
        document_ids=["d1"] if documents is None else documents,
        total=total,
        open_questions=questions or [],
        findings=findings or [],
        submission_reference=reference,
    )


def finding(severity: Severity, source: FindingSource = FindingSource.trust) -> Finding:
    return Finding(code="x", severity=severity, message="m", source=source)


# --- refresh_status -----------------------------------------------------------------------------


def test_no_questions_means_ready():
    assert refresh_status(claim()).status is ClaimStatus.ready


def test_an_unanswered_question_means_needs_info():
    assert refresh_status(claim(questions=[question()])).status is ClaimStatus.needs_info


def test_all_answered_means_ready():
    answered = claim(questions=[question("q1", "QBR"), question("q2", "Pune")])
    assert refresh_status(answered).status is ClaimStatus.ready


def test_one_open_question_among_answered_ones_is_enough():
    mixed = claim(questions=[question("q1", "QBR"), question("q2")])
    assert refresh_status(mixed).status is ClaimStatus.needs_info


def test_a_blank_answer_is_not_an_answer():
    assert refresh_status(claim(questions=[question("q1", "   ")])).status is ClaimStatus.needs_info


def test_a_claim_with_no_documents_stays_draft():
    assert refresh_status(claim(documents=[])).status is ClaimStatus.draft


def test_a_new_question_moves_a_ready_claim_back_to_needs_info():
    ready = refresh_status(claim())
    reopened = refresh_status(ready.model_copy(update={"open_questions": [question()]}))
    assert reopened.status is ClaimStatus.needs_info


@pytest.mark.parametrize("status", LOCKED)
def test_refresh_never_moves_a_locked_claim(status: ClaimStatus):
    locked = claim(status, questions=[question()], documents=[])
    assert refresh_status(locked) == locked


def test_findings_do_not_decide_readiness():
    flagged = claim(findings=[finding(Severity.high), finding(Severity.warn)])
    assert refresh_status(flagged).status is ClaimStatus.ready


def test_refresh_is_idempotent_and_pure():
    original = claim(questions=[question()])
    once = refresh_status(original)
    assert refresh_status(once) == once
    assert original.status is ClaimStatus.draft  # the input is untouched


# --- answer_question ----------------------------------------------------------------------------


def test_answering_the_last_question_makes_the_claim_ready():
    needs = refresh_status(claim(questions=[question("q1"), question("q2")]))
    half = answer_question(needs, "q1", "Client QBR")
    assert half.status is ClaimStatus.needs_info
    done = answer_question(half, "q2", "Pune")
    assert done.status is ClaimStatus.ready
    assert [q.answer for q in done.open_questions] == ["Client QBR", "Pune"]


def test_the_answer_is_stored_trimmed():
    needs = refresh_status(claim(questions=[question()]))
    assert answer_question(needs, "q1", "  QBR  \n").open_questions[0].answer == "QBR"


def test_an_answer_can_be_corrected_before_submission():
    ready = answer_question(refresh_status(claim(questions=[question()])), "q1", "QBR")
    corrected = answer_question(ready, "q1", "Annual planning")
    assert corrected.open_questions[0].answer == "Annual planning"
    assert corrected.status is ClaimStatus.ready


def test_answering_twice_with_the_same_text_changes_nothing():
    needs = refresh_status(claim(questions=[question()]))
    once = answer_question(needs, "q1", "QBR")
    assert answer_question(once, "q1", "QBR") == once


def test_answering_does_not_mutate_the_input():
    needs = refresh_status(claim(questions=[question()]))
    answer_question(needs, "q1", "QBR")
    assert needs.open_questions[0].answer is None and needs.status is ClaimStatus.needs_info


def test_unknown_question_id_is_rejected():
    with pytest.raises(UnknownQuestion):
        answer_question(claim(questions=[question()]), "q-nope", "x")


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_a_blank_answer_is_rejected(blank: str):
    with pytest.raises(ValueError, match="blank"):
        answer_question(claim(questions=[question()]), "q1", blank)


@pytest.mark.parametrize("status", LOCKED)
def test_a_locked_claim_cannot_be_answered(status: ClaimStatus):
    with pytest.raises(InvalidTransition):
        answer_question(claim(status, questions=[question()]), "q1", "late answer")


# --- confirm_and_submit -------------------------------------------------------------------------


def ready_claim(**kw) -> Claim:
    return refresh_status(claim(**kw))


def test_a_ready_claim_submits_with_confirmation():
    submitted = confirm_and_submit(ready_claim(), confirmed=True, reference="FIN-2026-000001")
    assert submitted.status is ClaimStatus.submitted
    assert submitted.submission_reference == "FIN-2026-000001"


def test_submission_needs_explicit_confirmation():
    ready = ready_claim()
    with pytest.raises(InvalidTransition, match="confirmed"):
        confirm_and_submit(ready, confirmed=False, reference="R1")
    assert ready.status is ClaimStatus.ready and ready.submission_reference is None


@pytest.mark.parametrize("status", [ClaimStatus.draft, ClaimStatus.needs_info])
def test_only_a_ready_claim_can_be_submitted(status: ClaimStatus):
    with pytest.raises(InvalidTransition, match="not ready"):
        confirm_and_submit(claim(status), confirmed=True, reference="R1")


def test_a_stale_ready_status_with_an_open_question_is_not_submittable():
    stale = claim(ClaimStatus.ready, questions=[question()])
    with pytest.raises(InvalidTransition, match="unanswered"):
        confirm_and_submit(stale, confirmed=True, reference="R1")


def test_blank_reference_is_rejected():
    with pytest.raises(ValueError, match="reference"):
        confirm_and_submit(ready_claim(), confirmed=True, reference="  ")


def test_retrying_a_submission_with_the_same_reference_is_idempotent():
    first = confirm_and_submit(ready_claim(), confirmed=True, reference="R1")
    assert confirm_and_submit(first, confirmed=True, reference="R1") == first


def test_a_different_reference_on_a_submitted_claim_is_an_error():
    first = confirm_and_submit(ready_claim(), confirmed=True, reference="R1")
    with pytest.raises(InvalidTransition):
        confirm_and_submit(first, confirmed=True, reference="R2")


def test_retry_still_needs_confirmation():
    first = confirm_and_submit(ready_claim(), confirmed=True, reference="R1")
    with pytest.raises(InvalidTransition):
        confirm_and_submit(first, confirmed=False, reference="R1")


@pytest.mark.parametrize("status", [ClaimStatus.approved, ClaimStatus.rejected])
def test_decided_claims_cannot_be_submitted_again(status: ClaimStatus):
    with pytest.raises(InvalidTransition):
        confirm_and_submit(claim(status, reference="R1"), confirmed=True, reference="R1")


# --- decide -------------------------------------------------------------------------------------


def submitted() -> Claim:
    return confirm_and_submit(ready_claim(), confirmed=True, reference="R1")


@pytest.mark.parametrize(
    ("approved", "status"), [(True, ClaimStatus.approved), (False, ClaimStatus.rejected)]
)
def test_finance_decides_a_submitted_claim(approved: bool, status: ClaimStatus):
    assert decide(submitted(), approved=approved).status is status


@pytest.mark.parametrize("status", [ClaimStatus.draft, ClaimStatus.needs_info, ClaimStatus.ready])
@pytest.mark.parametrize("approved", [True, False])
def test_only_a_submitted_claim_can_be_decided(status: ClaimStatus, approved: bool):
    with pytest.raises(InvalidTransition, match="submitted"):
        decide(claim(status), approved=approved)


def test_repeating_the_same_decision_changes_nothing():
    approved = decide(submitted(), approved=True)
    assert decide(approved, approved=True) == approved


def test_a_decision_cannot_be_reversed():
    approved = decide(submitted(), approved=True)
    with pytest.raises(InvalidTransition):
        decide(approved, approved=False)
    rejected = decide(submitted(), approved=False)
    with pytest.raises(InvalidTransition):
        decide(rejected, approved=True)


def test_the_full_lifecycle():
    c = refresh_status(claim(questions=[question()]))
    assert c.status is ClaimStatus.needs_info
    c = answer_question(c, "q1", "Client QBR")
    assert c.status is ClaimStatus.ready
    c = confirm_and_submit(c, confirmed=True, reference="FIN-1")
    assert c.status is ClaimStatus.submitted
    c = decide(c, approved=True)
    assert c.status is ClaimStatus.approved
    assert refresh_status(c).status is ClaimStatus.approved


# --- route --------------------------------------------------------------------------------------


def test_a_clean_answered_small_claim_is_auto_approved():
    assert route(claim(questions=[question("q1", "QBR")], total=9999.0)) == "auto_approve"


def test_the_auto_approve_limit_is_inclusive_at_ten_thousand():
    assert AUTO_APPROVE_LIMIT == 10_000.0
    assert route(claim(total=10_000.0)) == "auto_approve"
    assert route(claim(total=10_000.01)) == "finance_review"


def test_a_custom_limit_can_be_passed():
    assert route(claim(total=20_000.0), limit=25_000.0) == "auto_approve"
    assert route(claim(total=5_000.0), limit=1_000.0) == "finance_review"


def test_a_high_finding_always_goes_to_finance():
    assert route(claim(findings=[finding(Severity.high, FindingSource.policy)])) == "finance_review"


@pytest.mark.parametrize("source", list(FindingSource))
def test_a_warning_from_any_source_goes_to_finance(source: FindingSource):
    assert route(claim(findings=[finding(Severity.warn, source)])) == "finance_review"


def test_info_findings_do_not_block_auto_approval():
    assert route(claim(findings=[finding(Severity.info)])) == "auto_approve"


def test_an_unanswered_question_goes_to_finance():
    assert route(claim(questions=[question()])) == "finance_review"


def test_a_blank_answer_still_counts_as_unanswered():
    assert route(claim(questions=[question("q1", "  ")])) == "finance_review"
