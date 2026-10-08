"""FinanceStore: idempotent submission, references, the status machine, persistence."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from claimpilot_mcp_finance.models import SubmissionReceipt
from claimpilot_mcp_finance.store import (
    SCHEMA_VERSION,
    ClaimNotFoundError,
    FinanceError,
    FinanceStore,
    IdempotencyConflictError,
    InvalidClaimError,
    InvalidTransitionError,
    utc_now,
)

NEW_YORK = timezone(timedelta(hours=-5))


def payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "claim_id": "clm-001",
        "employee_id": "P001",
        "title": "Pune trip 12-14 Aug",
        "total": 4500.0,
        "currency": "INR",
        "document_ids": ["doc-a", "doc-b"],
        "idempotency_key": "key-1",
    }
    return {**base, **overrides}


def submit(store: FinanceStore, **overrides: Any) -> SubmissionReceipt:
    return store.submit(**payload(**overrides))


# -- submission ---------------------------------------------------------------------------------


def test_submit_returns_a_received_receipt(store: FinanceStore, clock):
    start = clock.now
    receipt = submit(store)
    assert receipt.reference == "FIN-2026-000001"
    assert receipt.status == "received"
    assert receipt.received_at == start
    assert receipt.duplicate is False


def test_references_follow_a_deterministic_counter(store: FinanceStore):
    refs = [submit(store, idempotency_key=f"k{i}").reference for i in range(3)]
    assert refs == ["FIN-2026-000001", "FIN-2026-000002", "FIN-2026-000003"]


def test_reference_year_follows_the_clock():
    store = FinanceStore(clock=lambda: datetime(2027, 1, 2, tzinfo=UTC))
    assert submit(store).reference == "FIN-2027-000001"
    store.close()


def test_naive_clock_readings_are_taken_as_utc():
    store = FinanceStore(clock=lambda: datetime(2026, 10, 8, 9, 30))
    assert submit(store).received_at == datetime(2026, 10, 8, 9, 30, tzinfo=UTC)
    store.close()


def test_clock_readings_in_other_zones_are_converted_to_utc():
    store = FinanceStore(clock=lambda: datetime(2026, 10, 8, 4, 30, tzinfo=NEW_YORK))
    receipt = submit(store)
    assert receipt.received_at == datetime(2026, 10, 8, 9, 30, tzinfo=UTC)
    assert receipt.received_at.utcoffset() == timedelta(0)
    store.close()


def test_utc_now_is_aware_and_whole_seconds():
    now = utc_now()
    assert now.tzinfo is not None and now.utcoffset() == timedelta(0)
    assert now.microsecond == 0


def test_submitted_claim_is_stored_with_normalised_values(store: FinanceStore):
    receipt = submit(
        store,
        claim_id="  clm-9 ",
        title="  Mumbai client dinner  ",
        total=1234.5678,
        currency=" inr ",
        document_ids=[" doc-1 ", "doc-2"],
    )
    claim = store.get(receipt.reference)
    assert claim.claim_id == "clm-9"
    assert claim.title == "Mumbai client dinner"
    assert claim.total == 1234.57
    assert claim.currency == "INR"
    assert claim.document_ids == ["doc-1", "doc-2"]
    assert claim.employee_id == "P001"
    assert claim.status == "received"
    assert [(e.status, e.actor) for e in claim.history] == [("received", None)]


# -- idempotency --------------------------------------------------------------------------------


def test_same_key_and_payload_returns_the_original_receipt(store: FinanceStore):
    first = submit(store)
    again = submit(store)
    assert again.duplicate is True
    assert again.reference == first.reference
    assert again.received_at == first.received_at
    assert len(store.find()) == 1


def test_retries_do_not_advance_the_counter(store: FinanceStore):
    submit(store)
    submit(store)
    assert submit(store, idempotency_key="key-2").reference == "FIN-2026-000002"


def test_document_order_does_not_change_the_payload(store: FinanceStore):
    first = submit(store, document_ids=["doc-a", "doc-b"])
    again = submit(store, document_ids=["doc-b", "doc-a"])
    assert again.duplicate is True
    assert again.reference == first.reference


@pytest.mark.parametrize(
    "change",
    [
        {"claim_id": "clm-002"},
        {"employee_id": "P002"},
        {"title": "Another title"},
        {"total": 4500.01},
        {"currency": "USD"},
        {"document_ids": ["doc-a"]},
    ],
)
def test_same_key_with_a_different_payload_is_an_error(store: FinanceStore, change: dict[str, Any]):
    first = submit(store)
    with pytest.raises(IdempotencyConflictError) as caught:
        submit(store, **change)
    assert first.reference in str(caught.value)
    assert len(store.find()) == 1


def test_replay_after_a_decision_still_returns_the_original_receipt(store: FinanceStore):
    first = submit(store)
    store.decide(first.reference, "approved", "DEMO-RAVI")
    again = submit(store)
    assert again.duplicate is True
    assert again.status == "received"  # the receipt is what was returned the first time
    assert store.get(first.reference).status == "approved"


# -- validation ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"claim_id": "  "}, "claim_id must not be empty"),
        ({"employee_id": ""}, "employee_id must not be empty"),
        ({"title": ""}, "title must not be empty"),
        ({"title": "x" * 201}, "title must be at most 200"),
        ({"idempotency_key": " "}, "idempotency_key must not be empty"),
        ({"total": 0}, "greater than zero"),
        ({"total": -5.0}, "greater than zero"),
        ({"total": 0.004}, "greater than zero"),
        ({"total": float("nan")}, "finite"),
        ({"total": float("inf")}, "finite"),
        ({"currency": "RS"}, "3-letter"),
        ({"currency": "INR1"}, "3-letter"),
        ({"currency": "₹₹₹"}, "3-letter"),
        ({"document_ids": []}, "at least one"),
        ({"document_ids": ["a", "a"]}, "duplicates"),
        ({"document_ids": ["a", " "]}, "document_ids entry must not be empty"),
        ({"document_ids": [f"d{i}" for i in range(201)]}, "at most 200"),
    ],
)
def test_invalid_submissions_are_rejected(
    store: FinanceStore, change: dict[str, Any], message: str
):
    with pytest.raises(InvalidClaimError, match=message):
        submit(store, **change)
    assert store.find() == []


def test_every_domain_error_is_a_finance_error():
    for error in (
        InvalidClaimError,
        ClaimNotFoundError,
        IdempotencyConflictError,
        InvalidTransitionError,
    ):
        assert issubclass(error, FinanceError)


# -- queries ------------------------------------------------------------------------------------


def test_get_is_case_and_whitespace_insensitive(store: FinanceStore):
    reference = submit(store).reference
    assert store.get(f"  {reference.lower()} ").reference == reference


def test_get_unknown_reference_fails(store: FinanceStore):
    with pytest.raises(ClaimNotFoundError, match="FIN-2026-009999"):
        store.get("fin-2026-009999")


def test_find_filters_by_status_and_employee_in_submission_order(store: FinanceStore):
    first = submit(store, idempotency_key="k1", employee_id="P001")
    second = submit(store, idempotency_key="k2", employee_id="P002")
    third = submit(store, idempotency_key="k3", employee_id="P001")
    store.decide(second.reference, "approved", "DEMO-RAVI")
    assert [c.reference for c in store.find()] == [
        first.reference,
        second.reference,
        third.reference,
    ]
    assert [c.reference for c in store.find(status="received")] == [
        first.reference,
        third.reference,
    ]
    assert [c.reference for c in store.find(employee_id=" P001 ")] == [
        first.reference,
        third.reference,
    ]
    assert [c.reference for c in store.find(status="approved", employee_id="P002")] == [
        second.reference
    ]
    assert store.find(status="rejected") == []


# -- status machine -----------------------------------------------------------------------------


def test_start_review_moves_received_to_under_review(store: FinanceStore):
    reference = submit(store).reference
    claim = store.start_review(reference, "DEMO-RAVI")
    assert claim.status == "under_review"
    assert [(e.status, e.actor) for e in claim.history] == [
        ("received", None),
        ("under_review", "DEMO-RAVI"),
    ]
    assert claim.updated_at > claim.received_at


def test_start_review_only_works_on_received_claims(store: FinanceStore):
    reference = submit(store).reference
    store.start_review(reference, "DEMO-RAVI")
    with pytest.raises(InvalidTransitionError, match="under_review"):
        store.start_review(reference, "DEMO-RAVI")
    store.decide(reference, "approved", "DEMO-RAVI")
    with pytest.raises(InvalidTransitionError, match="approved"):
        store.start_review(reference, "DEMO-RAVI")


def test_deciding_a_received_claim_passes_through_review(store: FinanceStore):
    reference = submit(store).reference
    claim = store.decide(reference, "approved", "DEMO-RAVI", "Looks good")
    assert claim.status == "approved"
    assert [(e.status, e.actor, e.comment) for e in claim.history] == [
        ("received", None, ""),
        ("under_review", "DEMO-RAVI", "review started by the decision"),
        ("approved", "DEMO-RAVI", "Looks good"),
    ]


def test_deciding_an_under_review_claim(store: FinanceStore):
    reference = submit(store).reference
    store.start_review(reference, "DEMO-RAVI")
    claim = store.decide(reference, "rejected", "DEMO-RAVI", " Missing hotel invoice ")
    assert claim.status == "rejected"
    assert [e.status for e in claim.history] == ["received", "under_review", "rejected"]
    assert claim.history[-1].comment == "Missing hotel invoice"
    assert claim.updated_at == claim.history[-1].at


def test_rejecting_requires_a_comment(store: FinanceStore):
    reference = submit(store).reference
    with pytest.raises(InvalidClaimError, match="comment"):
        store.decide(reference, "rejected", "DEMO-RAVI", "   ")
    assert store.get(reference).status == "received"


def test_decisions_are_final(store: FinanceStore):
    reference = submit(store).reference
    store.decide(reference, "approved", "DEMO-RAVI")
    for decision in ("approved", "rejected"):
        with pytest.raises(InvalidTransitionError, match="already approved"):
            store.decide(reference, decision, "DEMO-RAVI", "changed my mind")
    claim = store.get(reference)
    assert claim.status == "approved"
    assert len(claim.history) == 3


def test_a_rejected_claim_stays_rejected(store: FinanceStore):
    reference = submit(store).reference
    store.decide(reference, "rejected", "DEMO-RAVI", "Duplicate of an earlier claim")
    with pytest.raises(InvalidTransitionError, match="already rejected"):
        store.decide(reference, "approved", "DEMO-RAVI")


def test_unknown_decision_value_is_rejected(store: FinanceStore):
    reference = submit(store).reference
    with pytest.raises(InvalidClaimError, match="'approved' or 'rejected'"):
        store.decide(reference, "maybe", "DEMO-RAVI")  # type: ignore[arg-type]


def test_decision_inputs_are_validated(store: FinanceStore):
    reference = submit(store).reference
    with pytest.raises(InvalidClaimError, match="approver_id"):
        store.decide(reference, "approved", " ")
    with pytest.raises(InvalidClaimError, match="at most 1000"):
        store.decide(reference, "approved", "DEMO-RAVI", "x" * 1001)
    with pytest.raises(InvalidClaimError, match="reviewer_id"):
        store.start_review(reference, "")


def test_deciding_an_unknown_claim_fails(store: FinanceStore):
    with pytest.raises(ClaimNotFoundError):
        store.decide("FIN-2026-000042", "approved", "DEMO-RAVI")
    with pytest.raises(ClaimNotFoundError):
        store.start_review("FIN-2026-000042", "DEMO-RAVI")


def test_a_failing_decision_leaves_no_partial_history(
    store: FinanceStore, monkeypatch: pytest.MonkeyPatch
):
    reference = submit(store).reference
    original = FinanceStore._move
    calls = {"n": 0}

    def flaky(self: FinanceStore, db: sqlite3.Connection, *args: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 2:  # the implicit under_review step succeeds, the decision fails
            raise RuntimeError("disk full")
        original(self, db, *args)

    monkeypatch.setattr(FinanceStore, "_move", flaky)
    with pytest.raises(RuntimeError, match="disk full"):
        store.decide(reference, "approved", "DEMO-RAVI")
    monkeypatch.undo()
    claim = store.get(reference)
    assert claim.status == "received"
    assert [e.status for e in claim.history] == ["received"]


# -- persistence --------------------------------------------------------------------------------


def test_claims_survive_a_restart(tmp_path: Path):
    db = tmp_path / "finance.db"
    first = FinanceStore(db)
    receipt = submit(first)
    first.decide(receipt.reference, "approved", "DEMO-RAVI", "ok")
    first.close()

    second = FinanceStore(db)
    claim = second.get(receipt.reference)
    assert claim.status == "approved"
    assert [e.status for e in claim.history] == ["received", "under_review", "approved"]
    # the counter continues and the idempotency key still maps to the original claim
    assert submit(second, idempotency_key="key-2").reference.endswith("000002")
    replay = submit(second)
    assert replay.duplicate is True and replay.reference == receipt.reference
    second.close()


def test_missing_parent_directories_are_created(tmp_path: Path):
    db = tmp_path / "nested" / "dir" / "finance.db"
    store = FinanceStore(db)
    store.ping()
    store.close()
    assert db.exists()


def test_a_database_from_a_newer_schema_is_refused(tmp_path: Path):
    db = tmp_path / "future.db"
    raw = sqlite3.connect(db)
    raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    raw.close()
    with pytest.raises(RuntimeError, match="schema version"):
        FinanceStore(db)


def test_ping_fails_once_closed():
    store = FinanceStore()
    store.ping()
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        store.ping()


# -- concurrency --------------------------------------------------------------------------------


def run_threads(count: int, target: Any) -> list[Any]:
    results: list[Any] = [None] * count
    barrier = threading.Barrier(count)

    def worker(index: int) -> None:
        barrier.wait()
        results[index] = target(index)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def test_concurrent_submissions_get_unique_sequential_references(store: FinanceStore):
    receipts = run_threads(20, lambda i: submit(store, idempotency_key=f"k{i}"))
    refs = sorted(r.reference for r in receipts)
    assert refs == [f"FIN-2026-{n:06d}" for n in range(1, 21)]


def test_concurrent_retries_of_one_key_create_a_single_claim(store: FinanceStore):
    receipts = run_threads(10, lambda i: submit(store))
    assert len({r.reference for r in receipts}) == 1
    assert sum(1 for r in receipts if not r.duplicate) == 1
    assert len(store.find()) == 1
