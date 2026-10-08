"""Turn uploaded bytes into Claude image content blocks (one per page).

The file type is detected from magic bytes; the client-declared type is never trusted. Images
are EXIF-rotated, flattened to RGB and downscaled so the long edge is at most
``max_long_edge`` px (larger images cost more tokens without reading better), then re-encoded
as JPEG. PDFs are rasterised page by page with pdfium at the same resolution (ADR-016): this
gives one uniform image path for extraction and click-to-verify, and avoids sending raw PDF
payloads, which some corporate proxies block.
"""

from __future__ import annotations

import base64
import hashlib
import io
import threading
from dataclasses import dataclass
from typing import Any, Literal

import pypdfium2 as pdfium
from PIL import Image, ImageOps, UnidentifiedImageError

DEFAULT_MAX_LONG_EDGE = 1568
JPEG_QUALITY = 85
# A receipt photo is a few megapixels. A small PNG can declare a huge canvas and cost gigabytes of
# memory to decode, so anything beyond this is refused from its header alone.
MAX_PIXELS = 40_000_000

# pdfium keeps global state and is not thread-safe: two renders at once corrupt each other's output
# (and, with three at once, the process). Pages are prepared in worker threads, so serialise them.
_PDFIUM_LOCK = threading.Lock()

MediaType = Literal["image/jpeg", "image/png", "image/webp", "application/pdf"]

_MAGIC: tuple[tuple[bytes, MediaType], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"%PDF-", "application/pdf"),
)


class UnsupportedDocumentError(ValueError):
    """The upload is not a supported, readable receipt document."""


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    sha256: str  # of the ORIGINAL bytes: the identity used for caching and duplicate checks
    media_type: MediaType  # of the upload (a PDF stays "application/pdf" here)
    blocks: tuple[dict[str, Any], ...]  # Claude image blocks, one per page
    width: int  # of the first page, after downscaling
    height: int
    pages: int = 1


def sniff_media_type(data: bytes) -> MediaType:
    for magic, media_type in _MAGIC:
        if data.startswith(magic):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise UnsupportedDocumentError("unsupported file type (expected JPEG, PNG, WebP or PDF)")


def prepare_document(
    data: bytes, *, max_long_edge: int = DEFAULT_MAX_LONG_EDGE, max_pdf_pages: int = 10
) -> PreparedDocument:
    if not data:
        raise UnsupportedDocumentError("empty file")
    digest = hashlib.sha256(data).hexdigest()
    media_type = sniff_media_type(data)
    if media_type == "application/pdf":
        return _prepare_pdf(data, digest, max_pdf_pages, max_long_edge)
    return _prepare_image(data, digest, media_type, max_long_edge)


def _prepare_pdf(data: bytes, digest: str, max_pages: int, max_long_edge: int) -> PreparedDocument:
    with _PDFIUM_LOCK:
        images, pages = _render_pdf(data, max_pages, max_long_edge)
    blocks = tuple(_jpeg_block(img) for img in images)
    return PreparedDocument(
        digest, "application/pdf", blocks, images[0].width, images[0].height, pages=pages
    )


def _render_pdf(data: bytes, max_pages: int, max_long_edge: int) -> tuple[list[Image.Image], int]:
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise UnsupportedDocumentError("PDF could not be opened") from exc
    try:
        pages = len(pdf)
        if pages == 0:
            raise UnsupportedDocumentError("PDF has no pages")
        if pages > max_pages:
            raise UnsupportedDocumentError(f"PDF has {pages} pages; the limit is {max_pages}")
        images = []
        for page in pdf:
            scale = max_long_edge / max(page.get_size())  # PDF points -> pixels
            images.append(page.render(scale=scale).to_pil())
            page.close()
    finally:
        pdf.close()
    return images, pages


def _prepare_image(
    data: bytes, digest: str, media_type: MediaType, max_long_edge: int
) -> PreparedDocument:
    try:
        with Image.open(io.BytesIO(data)) as opened:
            if opened.width * opened.height > MAX_PIXELS:  # known from the header: nothing decoded
                raise UnsupportedDocumentError(
                    f"image is too large ({opened.width}x{opened.height} pixels)"
                )
            image = ImageOps.exif_transpose(opened)
            image = image.convert("RGB")  # drops alpha / palette; JPEG needs RGB
    except (UnidentifiedImageError, OSError) as exc:
        raise UnsupportedDocumentError("image could not be decoded") from exc
    image.thumbnail((max_long_edge, max_long_edge), Image.Resampling.LANCZOS)
    return PreparedDocument(digest, media_type, (_jpeg_block(image),), image.width, image.height)


def _jpeg_block(image: Image.Image) -> dict[str, Any]:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
        },
    }
