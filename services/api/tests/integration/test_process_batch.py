"""The whole pipeline on the committed synthetic fixtures, with fake models.

The extraction and decision models are replaced by fakes that answer from each fixture's ground
truth, so the test is free and deterministic; everything else is real: file preparation, the
trust checks, policy, grouping, persistence (sqlite via Alembic) and the event stream.
"""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from PIL import Image

from claimpilot.config import API_ROOT, REPO_ROOT, Settings
from claimpilot.db import create_engine, create_session_factory
from claimpilot.decisions import DecisionResult
from claimpilot.decisions.types import Answer, Question
from claimpilot.domain import DocType, ExpenseCategory, ExtractedReceipt, LineItem, ReceiptTruth
from claimpilot.evals.golden import has_alcohol, load_employees
from claimpilot.extraction import ReceiptExtractor, prepare_document
from claimpilot.extraction.schema import WireReceipt, from_domain
from claimpilot.llm.fake import FakeLLM
from claimpilot.pipeline import events as ev
from claimpilot.pipeline.dupindex import DbDuplicateIndex
from claimpilot.pipeline.process import PipelineDeps, process_batch
from claimpilot.pipeline.repo import NewFile, Repository
from claimpilot.policy import Policy
from claimpilot.ports import CalendarEvent, StaticCalendar, StaticDirectory
from claimpilot.storage import InMemoryStorage

FIXTURES = REPO_ROOT / "data" / "synth" / "fixtures"
TODAY = date(2026, 10, 12)

pytestmark = pytest.mark.skipif(not FIXTURES.exists(), reason="synthetic fixtures not present")


def migrate(url: str) -> None:
    config = Config(API_ROOT / "alembic.ini")
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


@dataclass(frozen=True)
class Fixture:
    id: str
    raw: bytes
    truth: ReceiptTruth


def load_fixtures() -> dict[str, Fixture]:
    out = {}
    for line in (FIXTURES / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        truth = ReceiptTruth.model_validate_json(
            (FIXTURES / row["truth_path"]).read_text(encoding="utf-8")
        )
        out[row["id"]] = Fixture(row["id"], (FIXTURES / row["path"]).read_bytes(), truth)
    return out


class TruthDecisions:
    """A System One stand-in that answers from ground truth (confidence overridable per id)."""

    name = "truth"

    def __init__(
        self, fixtures: Mapping[str, Fixture], confidence: Mapping[str, float] | None = None
    ):
        self._by_key = {
            (f.truth.receipt.merchant_name, f.truth.receipt.total): f for f in fixtures.values()
        }
        self._confidence = confidence or {}

    async def decide(
        self, state: Mapping[str, Any] | str, questions: Sequence[Question]
    ) -> DecisionResult:
        assert isinstance(state, Mapping)
        fixture = self._by_key[(state.get("merchant"), state.get("total"))]
        conf = self._confidence.get(fixture.id, 0.95)

        def answer(key: str, kind: str, value: str | float, confidence: float) -> Answer:
            return Answer(key=key, kind=kind, value=value, confidence=confidence, engine=self.name)  # type: ignore[arg-type]

        values = {
            "category": answer("category", "choice", fixture.truth.category.value, conf),
            "alcohol_present": answer(
                "alcohol_present", "noul", float(has_alcohol(fixture.truth)), 1.0
            ),
            "personal_expense": answer("personal_expense", "noul", 0.0, 1.0),
        }
        return DecisionResult(
            answers={q.key: values[q.key] for q in questions},
            engine=self.name,
            latency_ms=5,
            cost_usd=0.00002,
        )


@dataclass
class Harness:
    deps: PipelineDeps
    repo: Repository
    events: ev.InMemoryEventBus
    storage: InMemoryStorage
    fixtures: dict[str, Fixture]

    async def upload(self, files: Sequence[tuple[str, bytes]], employee: str = "P001") -> str:
        news = []
        for i, (name, data) in enumerate(files):
            key = f"b/{i:02d}-{name}"
            await self.storage.put(key, data)
            import hashlib

            news.append(NewFile(name, hashlib.sha256(data).hexdigest(), key))
        batch_id, _ = await self.repo.create_batch(employee, news)
        return batch_id

    async def upload_fixtures(self, *ids: str, employee: str = "P001") -> str:
        return await self.upload([(i, self.fixtures[i].raw) for i in ids], employee)

    async def run(self, batch_id: str):
        await process_batch(self.deps, batch_id)
        view = await self.repo.get_batch(batch_id)
        assert view is not None
        return view

    async def event_types(self, batch_id: str) -> list[str]:
        return [e.type for e in await self.events.read(batch_id)]


def build_harness(
    sessions,
    models_registry,
    fixtures,
    *,
    confidence=None,
    calendar=None,
    responder=None,
    settings: Settings | None = None,
) -> Harness:
    by_image = {prepare_document(f.raw).blocks[0]["source"]["data"]: f for f in fixtures.values()}

    def read(request) -> WireReceipt:
        data = request.body["messages"][0]["content"][0]["source"]["data"]
        return from_domain(by_image[data].truth.receipt)

    llm = FakeLLM(models_registry, env={})
    for route in ("extraction", "extraction_retry"):  # the second opinion reads it again
        llm.register(WireReceipt, responder or read, route=route)
    repo = Repository(sessions)
    storage = InMemoryStorage()
    events = ev.InMemoryEventBus()
    employees = load_employees(FIXTURES / "personas.json")
    deps = PipelineDeps(
        repo=repo,
        storage=storage,
        events=events,
        extractor=ReceiptExtractor(llm),
        decisions=TruthDecisions(fixtures, confidence),  # type: ignore[arg-type]
        policy=Policy.load(),
        directory=StaticDirectory(list(employees.values())),
        calendar=calendar or StaticCalendar(),
        index=DbDuplicateIndex(sessions),
        settings=settings or Settings(demo_today=TODAY),
    )
    return Harness(deps, repo, events, storage, fixtures)


@pytest.fixture
def fixtures() -> dict[str, Fixture]:
    return load_fixtures()


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[Any]:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'pipeline.db').as_posix()}"
    await asyncio.to_thread(migrate, url)
    engine = create_engine(Settings(database_url=url))
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def harness(sessions, models_registry, fixtures) -> Harness:
    return build_harness(sessions, models_registry, fixtures)


ALL = tuple(load_fixtures()) if FIXTURES.exists() else ()


async def test_a_pile_of_receipts_becomes_routed_claims(harness: Harness, fixtures):
    batch_id = await harness.upload_fixtures(*ALL)
    view = await harness.run(batch_id)

    assert (view.status, view.total, view.processed, view.failed) == ("done", 10, 10, 0)
    assert [d.status for d in view.documents] == ["processed"] * 10
    assert [d.filename for d in view.documents] == list(ALL)  # upload order kept

    types = await harness.event_types(batch_id)
    assert types[0] == "batch_started" and types[-1] == "batch_done"
    assert types.count("document_extracted") == 10 and types.count("document_checked") == 10
    assert types.index("claims_ready") == len(types) - 2
    done = (await harness.events.read(batch_id))[-1]
    assert (
        isinstance(done, ev.BatchDone) and done.claims == len(view.claims) > 0 and done.cost_usd > 0
    )

    # every document lands in exactly one claim, and claim totals add up
    claimed = [d for c in view.claims for d in c.document_ids]
    assert sorted(claimed) == sorted(d.id for d in view.documents)
    docs = {d.id: d for d in view.documents}
    for claim in view.claims:
        expected = round(sum(docs[i].document.receipt.total or 0.0 for i in claim.document_ids), 2)  # type: ignore[union-attr]
        assert claim.total == pytest.approx(expected)
        assert claim.route in ("auto_approve", "finance_review")

    # the audit trail records the AI decision for every document
    for d in view.documents:
        trail = await harness.repo.audit_trail(d.id)
        assert [e.action for e in trail] == ["document_processed"] and trail[0].actor == "truth"


async def test_tampered_and_injected_documents_are_flagged_and_never_auto_approved(
    harness: Harness,
):
    batch_id = await harness.upload_fixtures("s42-0082", "s42-0097", "s42-0086")
    view = await harness.run(batch_id)
    by_name = {d.filename: d for d in view.documents}

    # tampered bills: the hotel folio's items disagree with its subtotal, the invoice's total
    # disagrees with its own items and taxes (and its tax rate is not the one it states)
    for name, code in (("s42-0082", "items_subtotal_mismatch"), ("s42-0097", "total_mismatch")):
        doc = by_name[name]
        assert any(f.code == code and f.severity.value == "high" for f in doc.document.findings)  # type: ignore[union-attr]
        assert doc.verdict == "block" and (doc.trust_score or 100) < 80
    injected = by_name["s42-0086"]
    assert any(f.code == "prompt_injection" for f in injected.document.findings)  # type: ignore[union-attr]
    assert injected.verdict == "block"

    assert view.claims and all(c.route == "finance_review" for c in view.claims)


async def test_the_second_copy_is_the_duplicate_not_whichever_finished_first(
    harness: Harness,
):
    fx = harness.fixtures["s42-0005"]
    view = await harness.run(await harness.upload([("a.png", fx.raw), ("b.png", fx.raw)]))
    first, second = view.documents
    assert not any(f.code.startswith("duplicate") for f in first.document.findings)  # type: ignore[union-attr]
    dupes = [f for f in second.document.findings if f.code.startswith("duplicate")]  # type: ignore[union-attr]
    assert dupes and dupes[0].severity.value == "high"


async def test_a_receipt_uploaded_again_later_is_caught_across_batches(harness: Harness):
    await harness.run(await harness.upload_fixtures("s42-0005"))
    again = await harness.run(await harness.upload_fixtures("s42-0005"))
    assert any(f.code.startswith("duplicate") for f in again.documents[0].document.findings)  # type: ignore[union-attr]


async def test_one_unreadable_file_does_not_sink_the_batch(harness: Harness):
    good = harness.fixtures["s42-0005"].raw
    batch_id = await harness.upload([("ok.png", good), ("broken.png", b"\x89PNG\r\n\x1a\ngarbage")])
    view = await harness.run(batch_id)

    assert (view.status, view.processed, view.failed) == ("done", 1, 1)
    ok, broken = view.documents
    assert ok.status == "processed" and broken.status == "failed"
    assert broken.error and "could not be decoded" in broken.error
    types = await harness.event_types(batch_id)
    assert types.count("document_failed") == 1 and types.count("document_checked") == 1


async def test_a_finished_batch_is_not_processed_twice(harness: Harness):
    batch_id = await harness.upload_fixtures("s42-0005", "s42-0006")
    await harness.run(batch_id)
    before = await harness.event_types(batch_id)
    claims_before = [c.id for c in (await harness.repo.get_batch(batch_id)).claims]  # type: ignore[union-attr]
    await process_batch(harness.deps, batch_id)  # e.g. the queue delivered the job again
    assert await harness.event_types(batch_id) == before
    assert [c.id for c in (await harness.repo.get_batch(batch_id)).claims] == claims_before  # type: ignore[union-attr]


async def test_an_unknown_employee_fails_the_batch_cleanly(harness: Harness):
    batch_id = await harness.upload_fixtures("s42-0005", employee="NOBODY")
    view = await harness.run(batch_id)
    assert view.status == "failed" and "unknown employee" in (view.error or "")
    last = (await harness.events.read(batch_id))[-1]
    assert isinstance(last, ev.BatchFailed)


async def test_unsure_categories_become_questions_instead_of_guesses(
    sessions, models_registry, fixtures
):
    harness = build_harness(sessions, models_registry, fixtures, confidence={"s42-0013": 0.4})
    view = await harness.run(await harness.upload_fixtures("s42-0013", "s42-0005"))
    upi = next(d for d in view.documents if d.filename == "s42-0013")
    claim = next(c for c in view.claims if upi.id in c.document_ids)
    question = next(q for q in claim.open_questions if q.id.startswith("q-category-"))
    assert question.document_ids == [upi.id] and "CHAMELI GARG" in question.text
    assert claim.status.value == "needs_info" and claim.route == "finance_review"


def unique_png(seed: int) -> bytes:
    buf = io.BytesIO()
    image = Image.new("RGB", (200, 300), (255, 255, 255))
    for x in range(0, 200, 7 + seed % 5):
        for y in range(0, 300, 11 + seed % 3):
            image.putpixel((x, y), (seed * 37 % 255, x % 255, y % 255))
    image.save(buf, format="PNG")
    return buf.getvalue()


async def test_the_calendar_answers_the_client_dinner_questions(
    sessions, models_registry, fixtures
):
    dinner = ExtractedReceipt(
        doc_type=DocType.restaurant_bill,
        merchant_name="Spice Route Dining",
        merchant_city="Hyderabad",
        date="2026-09-14",
        line_items=[LineItem(description=f"Dish {i}", amount=500.0) for i in range(8)],
        total=4200.0,
    )

    def read(_request) -> WireReceipt:
        return from_domain(dinner)

    event = CalendarEvent(
        id="e1",
        title="Client dinner - Orion Retail",
        date=date(2026, 9, 14),
        kind="client_dinner",
        attendees=["Neha Rao (Orion Retail)", "A. Menon"],
    )
    calendar = StaticCalendar({"P001": [event]})
    harness = build_harness(sessions, models_registry, fixtures, calendar=calendar, responder=read)
    harness.deps.decisions.__dict__["_by_key"][("Spice Route Dining", 4200.0)] = Fixture(  # type: ignore[attr-defined]
        "synthetic",
        b"",
        ReceiptTruth(id="synthetic", receipt=dinner, category=ExpenseCategory.client_entertainment),
    )
    view = await harness.run(await harness.upload([("dinner.png", unique_png(1))]))

    claim = view.claims[0]
    attendees = next(q for q in claim.open_questions if q.kind.value == "attendees")
    assert attendees.answered and "from calendar" in (attendees.answer or "")
    assert "Orion Retail" in (attendees.answer or "")


async def test_attendees_from_the_calendar_reach_the_per_head_cap_on_the_first_pass(
    sessions, models_registry, fixtures
):
    dinner = ExtractedReceipt(
        doc_type=DocType.restaurant_bill,
        merchant_name="Spice Route Dining",
        merchant_city="Hyderabad",
        date="2026-09-14",
        line_items=[LineItem(description=f"Dish {i}", amount=1000.0) for i in range(9)],
        total=9000.0,  # three people (the employee and two guests): 3,000 a head, cap is 2,500
    )
    event = CalendarEvent(
        id="e1",
        title="Client dinner - Orion Retail",
        date=date(2026, 9, 14),
        kind="client_dinner",
        attendees=["Neha Rao (Orion Retail)", "A. Menon"],
    )
    harness = build_harness(
        sessions,
        models_registry,
        fixtures,
        calendar=StaticCalendar({"P001": [event]}),
        responder=lambda _request: from_domain(dinner),
    )
    harness.deps.decisions.__dict__["_by_key"][("Spice Route Dining", 9000.0)] = Fixture(  # type: ignore[attr-defined]
        "synthetic",
        b"",
        ReceiptTruth(id="synthetic", receipt=dinner, category=ExpenseCategory.client_entertainment),
    )
    view = await harness.run(await harness.upload([("dinner.png", unique_png(2))]))

    [claim] = view.claims
    assert [f.code for f in claim.findings] == ["entertainment_over_cap"]
    assert claim.route == "finance_review"
