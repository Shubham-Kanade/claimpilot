from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from claimpilot.domain import DocType, ExtractedReceipt
from claimpilot.extraction import (
    PROMPT_VERSION,
    ReceiptExtractor,
    UnsupportedDocumentError,
    prepare_document,
)
from claimpilot.extraction.cache import InMemoryExtractionCache, cache_key
from claimpilot.extraction.preprocess import sniff_media_type
from claimpilot.extraction.schema import WireReceipt, from_domain
from claimpilot.extraction.service import needs_escalation, system_prompt
from claimpilot.llm.fake import FakeLLM


def _png(width: int, height: int, mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


def _pdf(pages: int = 1, size: tuple[int, int] = (595, 842)) -> bytes:
    buf = io.BytesIO()
    imgs = [Image.new("RGB", size, "white") for _ in range(pages)]
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def _decoded_size(block: dict) -> tuple[int, int]:
    raw = base64.b64decode(block["source"]["data"])
    with Image.open(io.BytesIO(raw)) as img:
        return img.size


# --- preprocessing -------------------------------------------------------------------------


def test_large_image_is_downscaled_keeping_aspect_ratio():
    doc = prepare_document(_png(1000, 4000), max_long_edge=1568)
    assert doc.media_type == "image/png"  # the upload's type; the block is always JPEG
    assert doc.blocks[0]["source"]["media_type"] == "image/jpeg"
    assert (doc.width, doc.height) == (392, 1568)
    assert _decoded_size(doc.blocks[0]) == (392, 1568)


def test_small_image_is_not_upscaled_and_alpha_is_flattened():
    doc = prepare_document(_png(300, 200, mode="RGBA"))
    assert (doc.width, doc.height) == (300, 200)


def test_hash_is_of_original_bytes():
    data = _png(10, 10)
    assert prepare_document(data).sha256 == prepare_document(data).sha256
    assert prepare_document(data).sha256 != prepare_document(_png(11, 10)).sha256


def test_pdf_pages_are_rasterised_to_image_blocks():
    doc = prepare_document(_pdf(pages=2), max_long_edge=1000)
    assert doc.media_type == "application/pdf"
    assert doc.pages == 2 and len(doc.blocks) == 2
    assert all(b["type"] == "image" for b in doc.blocks)
    assert max(_decoded_size(doc.blocks[0])) == 1000
    assert (doc.width, doc.height) == _decoded_size(doc.blocks[0])


def test_pdf_page_limit():
    with pytest.raises(UnsupportedDocumentError, match="3 pages"):
        prepare_document(_pdf(pages=3), max_pdf_pages=2)


def test_corrupt_pdf_is_rejected():
    with pytest.raises(UnsupportedDocumentError, match="PDF could not be opened"):
        prepare_document(b"%PDF-1.4 this is not really a pdf")


@pytest.mark.parametrize(
    ("data", "match"),
    [(b"", "empty"), (b"GIF89a....", "unsupported"), (b"\x89PNG\r\n\x1a\ncorrupt", "decoded")],
)
def test_rejects_bad_uploads(data, match):
    with pytest.raises(UnsupportedDocumentError, match=match):
        prepare_document(data)


def test_sniff_webp():
    assert sniff_media_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"


# --- extraction service --------------------------------------------------------------------

GOOD = ExtractedReceipt(doc_type=DocType.restaurant_bill, merchant_name="Chai Point", total=189.0)
UNSURE = GOOD.model_copy(update={"total": 18.9, "low_confidence_fields": ["total"]})


@pytest.fixture
def doc():
    return prepare_document(_png(50, 80))


@pytest.fixture
def llm(models_registry):
    return FakeLLM(models_registry, env={})


async def test_extracts_with_prompt_and_document(llm, doc):
    llm.register(WireReceipt, from_domain(GOOD), route="extraction")
    result = await ReceiptExtractor(llm).extract(doc)

    assert result.receipt == GOOD
    assert not result.cached and not result.escalated
    assert result.model_key == "haiku"
    request = llm.requests[0]
    assert request.body["system"][0]["text"] == system_prompt()
    assert request.body["messages"][0]["content"][0] == doc.blocks[0]  # pages first, then text
    assert "thinking" in request.body  # Haiku 5.5 thinking off is sent as {type: disabled}
    assert request.body["thinking"] == {"type": "disabled"}


async def test_cache_hit_skips_llm(llm, doc):
    llm.register(WireReceipt, from_domain(GOOD), route="extraction")
    extractor = ReceiptExtractor(llm, cache=InMemoryExtractionCache())
    first = await extractor.extract(doc)
    second = await extractor.extract(doc)

    assert not first.cached and second.cached
    assert second.receipt == GOOD
    assert len(llm.requests) == 1
    assert second.cost_usd == 0


async def test_escalates_when_critical_field_is_unsure(llm, doc):
    llm.register(WireReceipt, from_domain(UNSURE), route="extraction")
    llm.register(WireReceipt, from_domain(GOOD), route="extraction_retry")
    result = await ReceiptExtractor(llm, escalate=True).extract(doc)

    assert result.escalated
    assert result.receipt.total == 189.0
    assert [c.model_key for c in result.calls] == ["haiku", "sonnet"]
    assert result.cost_usd == pytest.approx(sum(c.cost_usd for c in result.calls))


async def test_escalation_is_off_by_default(llm, doc):
    llm.register(WireReceipt, from_domain(UNSURE), route="extraction")
    result = await ReceiptExtractor(llm).extract(doc)
    assert not result.escalated and result.receipt.total == 18.9


def test_needs_escalation_only_for_critical_fields():
    assert needs_escalation(UNSURE)
    assert not needs_escalation(GOOD.model_copy(update={"low_confidence_fields": ["time"]}))


def test_cache_key_varies_by_prompt_model_and_effort():
    base = cache_key("abc", PROMPT_VERSION, "haiku", "low")
    assert base != cache_key("abc", "extract_v2", "haiku", "low")
    assert base != cache_key("abc", PROMPT_VERSION, "sonnet", "low")
    assert base != cache_key("abc", PROMPT_VERSION, "haiku", None)


def test_prompt_mentions_injection_rule():
    assert "contains_instructions" in system_prompt()
