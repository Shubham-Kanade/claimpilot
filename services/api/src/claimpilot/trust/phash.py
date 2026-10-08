"""Perceptual hash of a document's first page: a 64-bit difference hash (dHash) tuned for text.

Why a hash at all: ``sha256`` only catches byte-identical re-uploads. Employees re-send the same
receipt as a screenshot of a screenshot, a WhatsApp-recompressed copy, or a slightly different
photo. A perceptual hash changes by only a few bits when the picture is re-encoded, rescaled,
lightly rotated or cropped, or brightened, so near-identical pictures can be found by Hamming
distance.

Pure Pillow and the standard library (no scipy / imagehash). Recipe, in order:

1. grey-scale and downscale to a 256 px long edge (area average, which also anti-aliases);
2. Gaussian blur (about 3 % of the long edge), so a text line that moves a few pixels does not
   flip bits;
3. stretch the contrast (2 % clipped at each end), which cancels brightness and exposure changes;
4. average into a 9 x 8 grid of cells and set one bit per pair of horizontally adjacent cells:
   ``left - right > DEADBAND`` grey levels. The dead band makes near-equal cells (blank paper,
   flat backgrounds) read 0 in both copies instead of flipping on noise.

Limits, measured on the 100 synthetic documents (see ``claimpilot.trust.eval``):

* A 64-bit layout hash cannot tell two *different* bills that share a template apart: mobile bills
  from one operator, UPI screenshots from one app, cab receipts from one company sit at distance
  0 to a few bits. So an image match is never trusted on its own when the receipts' fields
  disagree; ``claimpilot.trust.duplicates`` combines the hash with the field fingerprint.
* Re-photographing the same paper on another desk moves the hash by 20-35 bits, far more than
  re-encoding does. Those copies are found through the field fingerprint instead.
"""

from __future__ import annotations

import base64
import binascii
import io
from typing import TYPE_CHECKING, Final, cast

from PIL import Image, ImageFilter, ImageOps

if TYPE_CHECKING:  # annotations only: importing extraction would load pdfium for nothing
    from claimpilot.extraction.preprocess import PreparedDocument

PHASH_BITS: Final = 64
GRID_ROWS: Final = 8
GRID_COLUMNS: Final = GRID_ROWS + 1  # one more column than bits per row: pairs of neighbours

WORK_EDGE: Final = 256  # long edge of the working image, in pixels
BLUR_FRACTION: Final = 8 / WORK_EDGE  # blur radius as a share of the working long edge
CONTRAST_CUTOFF: Final = 2  # percent of pixels clipped at each end by the contrast stretch
DEADBAND: Final = 6  # grey levels (of 255): smaller differences between neighbours read as 0
MIN_CONTRAST: Final = 12  # a flatter page (blank or uniform) has no usable layout to hash

# Distance thresholds, chosen from the sweep in ``claimpilot.trust.eval`` and pinned by
# tests/unit/test_trust_phash.py. On the 10 committed fixtures every transform the brief lists
# (JPEG re-encode, 70 % resize, 2 degree rotation, 2 % crop, brightness +-10 %) lands at
# distance <= 7, while the closest pair of different fixtures is 9 apart: 8 sits between them.
PHASH_MAX_DISTANCE: Final = 8  # "same picture" when the receipts do not contradict each other
# With no fingerprint to cross-check, only an identical hash counts: on the 100 documents, any
# looser bar paired a document with a missing date to a different bill of the same template.
PHASH_UNVERIFIED_MAX_DISTANCE: Final = 0


def hamming(a: str, b: str) -> int:
    """Number of differing bits between two hex-encoded hashes of equal length."""
    if len(a) != len(b):
        raise ValueError(f"hash length mismatch: {len(a)} != {len(b)}")
    return (int(a, 16) ^ int(b, 16)).bit_count()


def is_phash(value: str | None) -> bool:
    """True for a well-formed hash string as produced by :func:`dhash` (16 hex digits)."""
    if value is None or len(value) != PHASH_BITS // 4:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def dhash(image: Image.Image) -> str | None:
    """The 64-bit dHash of ``image`` as 16 lowercase hex digits; None for a blank/flat image."""
    gray = _working_gray(image)
    low, high = cast("tuple[int, int]", gray.getextrema())  # a grey image has one band
    if high - low < MIN_CONTRAST:
        return None
    blurred = gray.filter(ImageFilter.GaussianBlur(max(1, round(BLUR_FRACTION * max(gray.size)))))
    stretched = ImageOps.autocontrast(blurred, cutoff=CONTRAST_CUTOFF)
    cells = stretched.resize((GRID_COLUMNS, GRID_ROWS), Image.Resampling.BOX).tobytes()
    value = 0
    for row in range(GRID_ROWS):
        start = row * GRID_COLUMNS
        for column in range(GRID_ROWS):
            value = (value << 1) | (cells[start + column] - cells[start + column + 1] > DEADBAND)
    return f"{value:0{PHASH_BITS // 4}x}"


def page_phash(prepared: PreparedDocument, raw: bytes | None = None) -> str | None:
    """Hash of the first page: the prepared page image, else the raw upload when it is an image.

    ``prepared.blocks[0]`` is a base64 JPEG (an EXIF-rotated, downscaled photo or a rasterised
    PDF page), so every file type goes through the same pixels the extractor saw. Returns None
    when no page can be decoded or the page is blank.
    """
    for loader in (_from_block, _from_raw):
        image = loader(prepared, raw)
        if image is not None:
            return dhash(image)
    return None


def _working_gray(image: Image.Image) -> Image.Image:
    gray = image.convert("L")
    width, height = gray.size
    scale = WORK_EDGE / max(width, height)
    if scale >= 1:
        return gray  # small images are hashed as they are; upscaling invents no layout
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return gray.resize(size, Image.Resampling.BOX)


def _from_block(prepared: PreparedDocument, _raw: bytes | None) -> Image.Image | None:
    if not prepared.blocks:
        return None
    try:
        data = base64.b64decode(prepared.blocks[0]["source"]["data"], validate=True)
        return _decode(data)
    except (KeyError, TypeError, ValueError, binascii.Error):
        return None


def _from_raw(prepared: PreparedDocument, raw: bytes | None) -> Image.Image | None:
    if raw is None or prepared.media_type == "application/pdf":
        return None
    return _decode(raw)


def _decode(data: bytes) -> Image.Image | None:
    try:
        with Image.open(io.BytesIO(data)) as opened:
            return ImageOps.exif_transpose(opened).convert("RGB")
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None
