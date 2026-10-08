"""Shared builders for the trust tests, plus a sanity test of the builders themselves.

Nothing here touches the network. Documents come from the committed fixtures
(``data/synth/fixtures``, 10 small synthetic documents) or are drawn with Pillow.
"""

from __future__ import annotations

import base64
import io
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageEnhance, PngImagePlugin

from claimpilot.config import REPO_ROOT
from claimpilot.domain import DocType, ExtractedReceipt, LineItem, ReceiptTruth, TaxBreakup
from claimpilot.extraction.preprocess import PreparedDocument, prepare_document

FIXTURES = REPO_ROOT / "data" / "synth" / "fixtures"
GOLDEN = REPO_ROOT / "data" / "synth" / "golden"
VALID_GSTIN = "27AAPFU0939F1ZV"

needs_fixtures = pytest.mark.skipif(not FIXTURES.exists(), reason="synthetic fixtures missing")
needs_golden = pytest.mark.skipif(not GOLDEN.exists(), reason="golden dataset missing")


# --- fixtures -------------------------------------------------------------------------------


def fixture_ids() -> list[str]:
    rows = (FIXTURES / "manifest.jsonl").read_text("utf-8").splitlines()
    return sorted(json.loads(row)["id"] for row in rows if row.strip())


def fixture_path(doc_id: str) -> Path:
    for row in (FIXTURES / "manifest.jsonl").read_text("utf-8").splitlines():
        if row.strip() and json.loads(row)["id"] == doc_id:
            return FIXTURES / json.loads(row)["path"]
    raise KeyError(doc_id)


def load_fixture(doc_id: str) -> tuple[bytes, ReceiptTruth]:
    truth = ReceiptTruth.model_validate_json(
        (FIXTURES / "truth" / f"{doc_id}.json").read_text("utf-8")
    )
    return fixture_path(doc_id).read_bytes(), truth


# --- receipts -------------------------------------------------------------------------------


def bill(**overrides: Any) -> ExtractedReceipt:
    """A consistent intra-state 5% restaurant bill with every field the fingerprint needs."""
    base: dict[str, Any] = {
        "doc_type": DocType.restaurant_bill,
        "merchant_name": "Rasoi Ghar Eatery",
        "merchant_gstin": VALID_GSTIN,
        "merchant_city": "Pune",
        "invoice_number": "R01061",
        "date": "2026-08-07",
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


# --- images and files -----------------------------------------------------------------------

_WORDS = ("Thali", "Lassi", "Naan", "Paneer", "Coffee", "Tea", "Dosa", "Fare", "Tax", "Total")


def receipt_image(seed: int = 0, size: tuple[int, int] = (480, 640)) -> Image.Image:
    """A white page with dark blocks, "text" lines and rules: distinct per seed, cheap to draw.

    The blocks (a header band, a logo, a stamp, a footer) give each seed its own layout; text
    alone gives every page the same left-heavy look at 64-bit resolution.
    """
    rng = random.Random(seed)
    width, height = size
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for _ in range(rng.randint(2, 4)):
        x0, y0 = rng.randint(0, max(0, width - 120)), rng.randint(0, max(0, height - 120))
        draw.rectangle(
            (x0, y0, x0 + rng.randint(60, 240), y0 + rng.randint(20, 110)),
            fill=(rng.randint(0, 90),) * 3,
        )
    y = rng.randint(10, 160)
    for _ in range(rng.randint(8, 16)):
        line = " ".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 4)))
        draw.text((rng.randint(10, 200), y), f"{line}  {rng.randint(10, 999)}.00", fill="black")
        y += rng.randint(22, 38)
        if rng.random() < 0.3:
            draw.line((10, y, width - 10, y), fill="black", width=rng.randint(1, 4))
    return image


def encode(image: Image.Image, fmt: str = "JPEG", **options: Any) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def jpeg_bytes(
    image: Image.Image | None = None,
    *,
    exif: dict[int, Any] | None = None,
    comment: bytes | None = None,
    quality: int = 90,
) -> bytes:
    options: dict[str, Any] = {"quality": quality}
    if exif is not None:
        tags = Image.Exif()
        for tag, value in exif.items():
            tags[tag] = value
        options["exif"] = tags
    if comment is not None:
        options["comment"] = comment
    return encode(image or receipt_image(), "JPEG", **options)


def png_bytes(
    image: Image.Image | None = None,
    *,
    text: dict[str, str] | None = None,
    exif: dict[int, Any] | None = None,
) -> bytes:
    options: dict[str, Any] = {}
    if text:
        info = PngImagePlugin.PngInfo()
        for key, value in text.items():
            info.add_text(key, value)
        options["pnginfo"] = info
    if exif is not None:
        tags = Image.Exif()
        for tag, value in exif.items():
            tags[tag] = value
        options["exif"] = tags
    return encode(image or receipt_image(), "PNG", **options)


def xmp_packet(body: str) -> str:
    return (
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        f"<rdf:Description rdf:about=''{body}</rdf:Description></rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>'
    )


def with_xmp(jpeg: bytes, packet: str) -> bytes:
    """Insert an XMP packet as an APP1 segment right after the JPEG start-of-image marker."""
    header = b"http://ns.adobe.com/xap/1.0/\x00"
    payload = header + packet.encode("utf-8")
    segment = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    return jpeg[:2] + segment + jpeg[2:]


def pdf_bytes(info: bytes = b"", extra: bytes = b"") -> bytes:
    """A skeletal PDF: enough structure for the metadata scanner (not for a PDF renderer)."""
    return (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
        b"4 0 obj\n<< "
        + info
        + b" >>\nendobj\n"
        + extra
        + b"trailer\n<< /Root 1 0 R /Info 4 0 R >>\n"
        b"%%EOF\n"
    )


# --- the re-photographing transformations the brief names -----------------------------------

Transform = Callable[[Image.Image], Image.Image]

TRANSFORMS: dict[str, Transform] = {
    "jpeg_q40": lambda im: Image.open(io.BytesIO(encode(im, "JPEG", quality=40))),
    "resize_70": lambda im: im.resize(
        (round(im.width * 0.7), round(im.height * 0.7)), Image.Resampling.LANCZOS
    ),
    "rotate_+2": lambda im: im.rotate(2, Image.Resampling.BICUBIC, fillcolor=(255, 255, 255)),
    "rotate_-2": lambda im: im.rotate(-2, Image.Resampling.BICUBIC, fillcolor=(255, 255, 255)),
    "crop_2pct": lambda im: im.crop(
        (
            round(im.width * 0.02),
            round(im.height * 0.02),
            round(im.width * 0.98),
            round(im.height * 0.98),
        )
    ),
    "brighter_10": lambda im: ImageEnhance.Brightness(im).enhance(1.1),
    "darker_10": lambda im: ImageEnhance.Brightness(im).enhance(0.9),
}


def page_image(raw: bytes) -> Image.Image:
    """The first page as the extractor sees it (a rasterised page for a PDF)."""
    block = prepare_document(raw).blocks[0]["source"]["data"]
    with Image.open(io.BytesIO(base64.b64decode(block))) as opened:
        return opened.convert("RGB")


def reupload(raw: bytes, transform: Transform, *, fmt: str = "JPEG") -> bytes:
    """What an employee re-sending the picture after ``transform`` would upload."""
    return encode(transform(page_image(raw)).convert("RGB"), fmt)


def prepared(raw: bytes) -> PreparedDocument:
    return prepare_document(raw)


# --- sanity tests of the builders -------------------------------------------------------------


@needs_fixtures
def test_fixtures_load_and_have_receipts():
    ids = fixture_ids()
    assert len(ids) == 10
    raw, truth = load_fixture(ids[0])
    assert raw and truth.receipt.doc_type is not None


def test_builders_produce_decodable_files():
    for raw in (jpeg_bytes(), png_bytes(text={"Software": "x"}), encode(receipt_image(), "WEBP")):
        assert prepare_document(raw).pages == 1
    assert bill().total == 420.0
    assert receipt_image(1).tobytes() != receipt_image(2).tobytes()


def test_xmp_and_pdf_builders():
    packet = xmp_packet(' xmp:CreatorTool="X">')
    assert with_xmp(jpeg_bytes(), packet).startswith(b"\xff\xd8\xff\xe1")
    assert b"/Producer (Y)" in pdf_bytes(b"/Producer (Y)")
    assert set(TRANSFORMS) >= {"jpeg_q40", "resize_70", "rotate_+2", "crop_2pct"}
