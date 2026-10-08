"""Turn uploaded bytes into a Claude content block.

The file type is detected from magic bytes; the client-declared type is never trusted. Images
are EXIF-rotated, flattened to RGB and downscaled so the long edge is at most
``max_long_edge`` px (larger images cost more tokens without reading better), then re-encoded
as JPEG. PDFs are passed through as document blocks after a page-count check.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
from dataclasses import dataclass
from typing import Any, Literal

from PIL import Image, ImageOps, UnidentifiedImageError

DEFAULT_MAX_LONG_EDGE = 1568
JPEG_QUALITY = 85

MediaType = Literal["image/jpeg", "image/png", "image/webp", "application/pdf"]

_MAGIC: tuple[tuple[bytes, MediaType], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"%PDF-", "application/pdf"),
)
_PDF_PAGE = re.compile(rb"/Type\s*/Page(?!s)")


class UnsupportedDocumentError(ValueError):
    """The upload is not a supported, readable receipt document."""


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    sha256: str  # of the ORIGINAL bytes: the identity used for caching and duplicate checks
    media_type: MediaType
    block: dict[str, Any]  # Claude content block (image or document)
    width: int | None = None  # after downscaling (images only)
    height: int | None = None
    pages: int | None = None  # PDFs only


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
        return _prepare_pdf(data, digest, max_pdf_pages)
    return _prepare_image(data, digest, max_long_edge)


def _prepare_pdf(data: bytes, digest: str, max_pages: int) -> PreparedDocument:
    pages = len(_PDF_PAGE.findall(data)) or 1  # approximation; Claude reads the PDF itself
    if pages > max_pages:
        raise UnsupportedDocumentError(f"PDF has {pages} pages; the limit is {max_pages}")
    block = {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.b64encode(data).decode("ascii"),
        },
    }
    return PreparedDocument(digest, "application/pdf", block, pages=pages)


def _prepare_image(data: bytes, digest: str, max_long_edge: int) -> PreparedDocument:
    try:
        with Image.open(io.BytesIO(data)) as opened:
            image = ImageOps.exif_transpose(opened)
            image = image.convert("RGB")  # drops alpha / palette; JPEG needs RGB
    except (UnidentifiedImageError, OSError) as exc:
        raise UnsupportedDocumentError("image could not be decoded") from exc

    image.thumbnail((max_long_edge, max_long_edge), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
        },
    }
    return PreparedDocument(digest, "image/jpeg", block, width=image.width, height=image.height)
