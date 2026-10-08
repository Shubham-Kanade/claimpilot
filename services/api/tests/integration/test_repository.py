"""Repository operations on a real (sqlite + aiosqlite) database built by the Alembic migrations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from claimpilot.config import API_ROOT, Settings
from claimpilot.db import SessionFactory, create_engine, create_session_factory
from claimpilot.domain import DocType, ExpenseCategory, ExtractedReceipt
from claimpilot.domain.claims import (
    Claim,
    ClaimMode,
    ClaimStatus,
    Decisions,
    OpenQuestion,
    ProcessedDocument,
    QuestionKind,
)
from claimpilot.pipeline.repo import NewFile, Repository


def migrate(url: str) -> None:
    config = Config(API_ROOT / "alembic.ini")
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[SessionFactory]:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'repo.db').as_posix()}"
    await asyncio.to_thread(migrate, url)
    engine = create_engine(Settings(database_url=url))
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def repo(sessions: SessionFactory) -> Repository:
    return Repository(sessions)


FILES = [NewFile("a.png", "a" * 64, "b/a.png"), NewFile("b.pdf", "b" * 64, "b/b.pdf")]


def processed(doc_id: str) -> ProcessedDocument:
    return ProcessedDocument(
        id=doc_id,
        filename="a.png",
        sha256="a" * 64,
        receipt=ExtractedReceipt(doc_type=DocType.cab_receipt, total=320.0, date="2026-10-03"),
        decisions=Decisions(
            category=ExpenseCategory.local_conveyance,
            category_confidence=0.9,
            alcohol_present=0.0,
            personal_expense=0.1,
            engine="jev",
        ),
    )


def claim(claim_id: str = "clm-1", **overrides) -> Claim:
    fields = {
        "id": claim_id,
        "employee_id": "P001",
        "title": "Local conveyance Oct 2026",
        "mode": ClaimMode.period,
        "document_ids": ["d1"],
        "total": 320.0,
        "start_date": date(2026, 10, 3),
        "open_questions": [OpenQuestion(id="q1", kind=QuestionKind.other, text="What for?")],
    }
    return Claim(**{**fields, **overrides})


async def test_create_batch_preserves_upload_order(repo: Repository):
    batch_id, doc_ids = await repo.create_batch("P001", FILES)
    view = await repo.get_batch(batch_id)
    assert view is not None and view.status == "queued" and view.total == 2
    assert [d.id for d in view.documents] == doc_ids
    assert [d.filename for d in view.documents] == ["a.png", "b.pdf"]
    assert [d.position for d in view.documents] == [0, 1]
    assert await repo.get_batch("missing") is None


async def test_document_result_failure_and_batch_progress(repo: Repository):
    batch_id, (d1, d2) = await repo.create_batch("P001", FILES)
    await repo.mark_batch(batch_id, "processing")
    await repo.save_document(
        d1, processed(d1), phash="ab12", fingerprint="fp", trust={"score": 92, "verdict": "clean"}
    )
    await repo.fail_document(d2, "x" * 900)
    await repo.mark_batch(batch_id, "done", processed=1, failed=1)

    view = await repo.get_batch(batch_id)
    assert view is not None and view.status == "done" and view.finished_at is not None
    first, second = view.documents
    assert first.status == "processed" and first.document is not None
    assert first.document.receipt.total == 320.0 and first.trust_score == 92
    assert first.verdict == "clean"
    assert second.status == "failed" and second.error is not None and len(second.error) == 500
    assert (view.processed, view.failed) == (1, 1)

    seen = await repo.seen_documents()
    assert [d.id for d in seen] == [d1] and seen[0].phash == "ab12"
    assert await repo.seen_documents(exclude_batch=batch_id) == []
    row = await repo.get_document(d1)
    assert row is not None and row.sha256 == "a" * 64


async def test_processed_documents_come_back_in_the_order_asked_for(repo: Repository):
    _, (d1, d2) = await repo.create_batch("P001", FILES)
    await repo.save_document(d1, processed(d1), phash=None, fingerprint=None, trust=None)
    await repo.save_document(d2, processed(d2), phash=None, fingerprint=None, trust=None)

    assert [d.id for d in await repo.processed_documents([d2, d1])] == [d2, d1]
    assert [d.id for d in await repo.processed_documents([d1])] == [d1]
    assert await repo.processed_documents([]) == []


async def test_processed_documents_skip_what_is_unknown_or_has_no_result(repo: Repository):
    _, (done, failed) = await repo.create_batch("P001", FILES)
    await repo.save_document(done, processed(done), phash=None, fingerprint=None, trust=None)
    await repo.fail_document(failed, "unreadable")

    found = await repo.processed_documents(["missing", failed, done])

    assert [d.id for d in found] == [done]


async def test_the_cost_shown_covers_only_the_calls_since_the_oldest_remaining_batch(
    repo: Repository, sessions: SessionFactory
):
    from datetime import UTC, datetime, timedelta

    from claimpilot.db import LlmCall
    from claimpilot.pipeline.repo import collect_stats

    def call(when: datetime, cost: float) -> LlmCall:
        return LlmCall(
            created_at=when, route="extraction", model_key="haiku", model_id="m", mode="replay",
            cost_usd=cost, request_hash="h" * 64,
        )  # fmt: skip

    long_ago = datetime.now(UTC) - timedelta(days=2)
    async with sessions() as session:
        session.add_all([call(long_ago, 0.5), call(long_ago, 0.5)])  # an earlier, deleted run
        await session.commit()
    assert (await collect_stats(sessions))["llm_calls"] == 0  # no batch left: nothing to count

    batch_id, (doc_id, _) = await repo.create_batch("P001", FILES)
    await repo.save_document(doc_id, processed(doc_id), phash=None, fingerprint=None, trust=None)
    async with sessions() as session:
        session.add_all([call(datetime.now(UTC) + timedelta(seconds=1), 0.25)])
        await session.commit()

    stats = await collect_stats(sessions)
    assert stats["llm_calls"] == 1 and stats["llm_cost_usd"] == 0.25
    assert stats["llm_cost_per_document_usd"] == 0.25 and batch_id


async def test_unknown_ids_raise(repo: Repository):
    with pytest.raises(KeyError):
        await repo.mark_batch("nope", "done")
    with pytest.raises(KeyError):
        await repo.save_document("nope", processed("x"), phash=None, fingerprint=None, trust=None)
    with pytest.raises(KeyError):
        await repo.fail_document("nope", "e")
    with pytest.raises(KeyError):
        await repo.update_claim(claim())


async def test_claims_round_trip_filter_and_update(repo: Repository):
    batch_id, _ = await repo.create_batch("P001", FILES)
    c1, c2 = claim("clm-1"), claim("clm-2", employee_id="P002", title="Other")
    await repo.save_claims(batch_id, "P001", [c1, c2], {"clm-1": "finance_review"})

    got = await repo.get_claim("clm-1")
    assert got is not None and got.route == "finance_review" and got.batch_id == batch_id
    assert got.open_questions[0].text == "What for?"
    assert await repo.get_claim("nope") is None
    assert {c.id for c in await repo.list_claims()} == {"clm-1", "clm-2"}
    assert [c.id for c in await repo.list_claims(route="finance_review")] == ["clm-1"]
    newest_first = [c.id for c in await repo.list_claims(status="draft")]
    assert newest_first == ["clm-2", "clm-1"]  # most recently created first
    assert await repo.list_claims(status="submitted") == []
    view = await repo.get_batch(batch_id)
    assert view is not None and len(view.claims) == 2

    updated = c1.model_copy(
        update={"status": ClaimStatus.submitted, "submission_reference": "FIN-1"}
    )
    await repo.update_claim(updated, route="auto_approve", idempotency_key="idem-1")
    again = await repo.get_claim("clm-1")
    assert again is not None and again.status is ClaimStatus.submitted
    assert again.submission_reference == "FIN-1" and again.route == "auto_approve"
    by_key = await repo.claim_for_idempotency_key("idem-1")
    assert by_key is not None and by_key.id == "clm-1"
    assert await repo.claim_for_idempotency_key("other") is None


async def test_audit_trail_is_ordered_per_entity(repo: Repository):
    await repo.audit("jev", "decided", "document", "d1", {"category": "meals"})
    await repo.audit("P001", "answered", "claim", "clm-1")
    await repo.audit("system", "extracted", "document", "d1")
    trail = await repo.audit_trail("d1")
    assert [(e.actor, e.action) for e in trail] == [("jev", "decided"), ("system", "extracted")]
    assert trail[0].detail == {"category": "meals"} and trail[1].detail == {}


async def test_batch_documents_and_failure_message(repo: Repository):
    batch_id, doc_ids = await repo.create_batch("P001", FILES)
    rows = await repo.batch_documents(batch_id)
    assert [r.id for r in rows] == doc_ids and [r.status for r in rows] == ["queued", "queued"]

    await repo.mark_batch(batch_id, "failed", error="e" * 800)
    view = await repo.get_batch(batch_id)
    assert view is not None and view.status == "failed" and view.finished_at is not None
    assert view.error is not None and len(view.error) == 500
