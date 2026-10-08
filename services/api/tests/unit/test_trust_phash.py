"""The 64-bit page hash: format, robustness to re-photographing, and separation of documents."""

from __future__ import annotations

import base64
import itertools

import pytest
from PIL import Image
from test_trust_support import (
    TRANSFORMS,
    encode,
    fixture_ids,
    jpeg_bytes,
    load_fixture,
    needs_fixtures,
    png_bytes,
    prepared,
    receipt_image,
    reupload,
)

from claimpilot.extraction.preprocess import PreparedDocument
from claimpilot.trust.phash import (
    PHASH_BITS,
    PHASH_MAX_DISTANCE,
    PHASH_UNVERIFIED_MAX_DISTANCE,
    dhash,
    hamming,
    is_phash,
    page_phash,
)


def hash_of(raw: bytes) -> str:
    result = page_phash(prepared(raw), raw)
    assert result is not None
    return result


# --- format and arithmetic ------------------------------------------------------------------


def test_hash_is_sixteen_lowercase_hex_digits_which_fits_string_16():
    value = dhash(receipt_image(3))
    assert value is not None and len(value) == 16 == PHASH_BITS // 4
    assert value == value.lower() and is_phash(value)


def test_hash_is_deterministic():
    assert dhash(receipt_image(5)) == dhash(receipt_image(5))
    assert dhash(receipt_image(5)) != dhash(receipt_image(6))


def test_hamming_distance():
    assert hamming("0000000000000000", "0000000000000000") == 0
    assert hamming("0000000000000000", "ffffffffffffffff") == 64
    assert hamming("00000000000000f0", "000000000000000f") == 8
    with pytest.raises(ValueError, match="length"):
        hamming("00", "0000")


@pytest.mark.parametrize("value", [None, "", "abc", "g" * 16, "0" * 17])
def test_is_phash_rejects_malformed_values(value):
    assert not is_phash(value)


def test_thresholds_are_ordered_and_documented_values():
    assert 0 <= PHASH_UNVERIFIED_MAX_DISTANCE <= PHASH_MAX_DISTANCE < PHASH_BITS // 4


# --- degenerate pages -----------------------------------------------------------------------


def test_blank_page_has_no_hash():
    assert dhash(Image.new("RGB", (300, 400), "white")) is None
    blank = encode(Image.new("RGB", (300, 400), "white"), "PNG")
    assert page_phash(prepared(blank), blank) is None


def test_small_images_are_hashed_without_upscaling():
    small = receipt_image(2, size=(120, 160))
    assert is_phash(dhash(small))


def test_page_hash_falls_back_to_the_raw_image_when_the_block_is_unreadable():
    raw = png_bytes(receipt_image(9))
    good = prepared(raw)
    broken = PreparedDocument(good.sha256, good.media_type, ({"source": {"data": "!!"}},), 1, 1)
    assert hamming(page_phash(broken, raw) or "", page_phash(good, raw) or "") <= 2  # JPEG vs PNG
    not_an_image = PreparedDocument(
        good.sha256,
        good.media_type,
        ({"source": {"data": base64.b64encode(b"nope").decode()}},),
        1,
        1,
    )
    assert page_phash(not_an_image, raw) is not None
    assert page_phash(PreparedDocument(good.sha256, good.media_type, (), 1, 1), raw) is not None


def test_no_hash_when_nothing_can_be_decoded():
    empty = PreparedDocument("0" * 64, "application/pdf", (), 1, 1)
    assert page_phash(empty, b"%PDF-1.4") is None
    assert page_phash(PreparedDocument("0" * 64, "image/png", (), 1, 1), None) is None
    assert page_phash(PreparedDocument("0" * 64, "image/png", (), 1, 1), b"garbage") is None


def test_exif_orientation_is_applied_before_hashing():
    upright = receipt_image(4)
    exif_rotated = jpeg_bytes(
        upright.rotate(90, expand=True), exif={0x0112: 6}
    )  # 6 = rotate 90 clockwise to display
    plain = jpeg_bytes(upright)
    assert hamming(hash_of(exif_rotated), hash_of(plain)) <= PHASH_MAX_DISTANCE


# --- the brief's robustness requirement, on the committed fixtures ---------------------------


@needs_fixtures
@pytest.mark.parametrize("name", sorted(TRANSFORMS))
def test_every_fixture_survives_each_rephotographing_transform(name):
    """Re-encode, resize 70 %, rotate 2 degrees, crop 2 %, brightness +-10 %: same document."""
    for doc_id in fixture_ids():
        raw, _ = load_fixture(doc_id)
        changed = reupload(raw, TRANSFORMS[name])
        assert hamming(hash_of(raw), hash_of(changed)) <= PHASH_MAX_DISTANCE, (doc_id, name)


@needs_fixtures
def test_different_fixtures_never_collide():
    """No pair of the ten different documents is closer than the match threshold."""
    hashes = {doc_id: hash_of(load_fixture(doc_id)[0]) for doc_id in fixture_ids()}
    for a, b in itertools.combinations(hashes, 2):
        assert hamming(hashes[a], hashes[b]) > PHASH_MAX_DISTANCE, (a, b)


@needs_fixtures
def test_pdf_fixtures_are_hashed_from_their_first_page():
    pdfs = [i for i in fixture_ids() if load_fixture(i)[0].startswith(b"%PDF")]
    assert pdfs
    for doc_id in pdfs:
        raw, _ = load_fixture(doc_id)
        assert prepared(raw).media_type == "application/pdf"
        assert is_phash(hash_of(raw))


def test_synthetic_pages_separate_and_survive_reencoding():
    pages = [jpeg_bytes(receipt_image(seed)) for seed in range(6)]
    hashes = [hash_of(p) for p in pages]
    for (i, a), (j, b) in itertools.combinations(enumerate(hashes), 2):
        assert hamming(a, b) > PHASH_UNVERIFIED_MAX_DISTANCE, (i, j)  # never identical
    for page, original in zip(pages, hashes, strict=True):
        resaved = reupload(page, TRANSFORMS["jpeg_q40"])
        assert hamming(original, hash_of(resaved)) <= PHASH_MAX_DISTANCE
