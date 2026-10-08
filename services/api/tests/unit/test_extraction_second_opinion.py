"""A stronger model re-reads a document only when its first read looks suspicious (ADR-031)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from claimpilot.domain import DocType, ExtractedReceipt, LineItem, TaxBreakup
from claimpilot.extraction import ReceiptExtractor, prepare_document
from claimpilot.extraction.schema import WireReceipt, from_domain
from claimpilot.extraction.service import looks_suspicious, reconcile
from claimpilot.llm.fake import FakeLLM

VALID_GSTIN = "27AAPFU0939F1ZV"


def bill(**overrides) -> ExtractedReceipt:
    """A consistent 5% restaurant bill: 400 + 10 + 10 = 420."""
    base = {
        "doc_type": DocType.restaurant_bill,
        "merchant_name": "Chai Point",
        "merchant_gstin": VALID_GSTIN,
        "line_items": [
            LineItem(description="Thali", amount=250.0),
            LineItem(description="Lassi", amount=150.0),
        ],
        "subtotal": 400.0,
        "taxes": TaxBreakup(cgst=10.0, sgst=10.0, gst_rate_percent=5),
        "total": 420.0,
    }
    base.update(overrides)
    return ExtractedReceipt(**base)


# the printed total says 520 but the bill's own items and tax add up to 420
EDITED = bill(total=520.0)
EDITED_HEDGING = bill(total=520.0, low_confidence_fields=["total"])


@pytest.fixture
def doc():
    buf = io.BytesIO()
    Image.new("RGB", (50, 80), "white").save(buf, format="PNG")
    return prepare_document(buf.getvalue())


@pytest.fixture
def llm(models_registry):
    return FakeLLM(models_registry, env={})


def reads(llm: FakeLLM, first: ExtractedReceipt, second: ExtractedReceipt | None = None) -> None:
    llm.register(WireReceipt, from_domain(first), route="extraction")
    if second is not None:
        llm.register(WireReceipt, from_domain(second), route="extraction_retry")


# --- when a second look is warranted ------------------------------------------------------------


@pytest.mark.parametrize(
    "receipt",
    [
        EDITED,
        bill(subtotal=350.0),  # rows worth more than the subtotal
        bill(subtotal=450.0, total=470.0),  # rows worth less: one may be unread
        bill(taxes=TaxBreakup(cgst=10.0, sgst=10.0, gst_rate_percent=18)),  # tax contradicts rate
        bill(merchant_gstin="27AAPFU0939F1ZW"),  # fails its checksum: maybe a misread character
        bill(contains_instructions=True),
        bill(low_confidence_fields=["total"]),  # consistent, but unsure of the amount itself
    ],
)
def test_these_reads_deserve_a_second_opinion(receipt):
    assert looks_suspicious(receipt)


@pytest.mark.parametrize(
    "receipt",
    [
        bill(),
        bill(merchant_gstin=None, taxes=TaxBreakup(), total=400.0),  # no GST, no GSTIN: fine
        bill(merchant_gstin=None),  # GST but no GSTIN printed: a re-read will not conjure one
        bill(low_confidence_fields=["date"]),  # unsure about something that proves nothing
    ],
)
def test_these_reads_do_not(receipt):
    assert not looks_suspicious(receipt)


# --- the extractor -------------------------------------------------------------------------------


async def test_a_clean_read_costs_one_call(llm, doc):
    reads(llm, bill())
    result = await ReceiptExtractor(llm).extract(doc)
    assert not result.escalated and len(result.calls) == 1
    assert [r.route for r in llm.requests] == ["extraction"]


async def test_a_misread_figure_is_corrected_by_the_second_read(llm, doc):
    reads(llm, EDITED_HEDGING, bill())  # the stronger model reads 420: it was a smudged digit
    result = await ReceiptExtractor(llm).extract(doc)

    assert result.escalated and result.receipt.total == 420.0
    assert [c.model_key for c in result.calls] == ["haiku", "sonnet"]
    assert not looks_suspicious(result.receipt)


async def test_a_confirmed_mismatch_loses_its_hedge_so_it_stays_an_accusation(llm, doc):
    # both reads see 520: it is printed that way, however unsure the first one sounded
    reads(llm, EDITED_HEDGING, EDITED_HEDGING)
    result = await ReceiptExtractor(llm).extract(doc)

    assert result.escalated and result.receipt.total == 520.0
    assert "total" not in result.receipt.low_confidence_fields


async def test_the_stronger_read_arbitrates_a_wrong_injection_flag(llm, doc):
    reads(llm, bill(contains_instructions=True), bill(contains_instructions=False))
    result = await ReceiptExtractor(llm).extract(doc)
    assert result.escalated and not result.receipt.contains_instructions


async def test_a_genuine_injection_is_confirmed_by_both_reads(llm, doc):
    reads(llm, bill(contains_instructions=True), bill(contains_instructions=True))
    result = await ReceiptExtractor(llm).extract(doc)
    assert result.escalated and result.receipt.contains_instructions


async def test_the_second_opinion_can_be_switched_off(llm, doc):
    reads(llm, EDITED)
    result = await ReceiptExtractor(llm, second_opinion=False).extract(doc)
    assert not result.escalated and result.receipt.total == 520.0
    assert [r.route for r in llm.requests] == ["extraction"]


async def test_the_final_read_is_what_gets_cached(llm, doc):
    from claimpilot.extraction.cache import InMemoryExtractionCache

    reads(llm, EDITED_HEDGING, bill())
    extractor = ReceiptExtractor(llm, cache=InMemoryExtractionCache())
    first = await extractor.extract(doc)
    again = await extractor.extract(doc)

    assert first.receipt.total == again.receipt.total == 420.0
    assert again.cached and len(llm.requests) == 2  # the re-read is not repeated


# --- reconcile -----------------------------------------------------------------------------------


def test_disagreeing_reads_give_the_stronger_one_unchanged():
    second = bill(low_confidence_fields=["total"])
    assert reconcile(EDITED, second) == second  # 520 vs 420: the hedge stays


def test_agreeing_reads_drop_the_hedge_on_the_confirmed_figures_only():
    second = bill(total=520.0, low_confidence_fields=["total", "date", "time"])
    merged = reconcile(EDITED, second)
    assert merged.low_confidence_fields == ["date", "time"]
    assert merged.total == 520.0


def test_a_gstin_both_reads_give_is_what_is_printed():
    wrong = bill(merchant_gstin="27AAPFU0939F1ZW", low_confidence_fields=["merchant_gstin"])
    merged = reconcile(wrong, wrong)
    assert merged.low_confidence_fields == []


def test_a_gstin_the_reads_disagree_on_is_not_confirmed():
    first = bill(merchant_gstin="27AAPFU0939F1ZW", low_confidence_fields=["merchant_gstin"])
    second = bill(low_confidence_fields=["merchant_gstin"])
    assert reconcile(first, second).low_confidence_fields == ["merchant_gstin"]


def test_sub_rupee_differences_still_count_as_agreement():
    first = bill(total=520.0, low_confidence_fields=["total"])
    second = bill(total=520.4, low_confidence_fields=["total"])
    assert reconcile(first, second).low_confidence_fields == []
