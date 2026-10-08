from __future__ import annotations

import cv2
import numpy as np
import pytest

from synthgen.assemble import ALL_PRESETS, CLEAN, plan_degradation
from synthgen.degrade import degrade_image
from synthgen.rng import derive_rng
from synthgen.spec import PDF_DOC_TYPES


@pytest.fixture(scope="module")
def page() -> np.ndarray:
    image = np.full((420, 300, 3), 252, np.uint8)
    for row, text in enumerate(("SYNTH BILL", "Paneer Tikka 280.00", "TOTAL 294.00")):
        cv2.putText(image, text, (14, 60 + row * 50), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (20,) * 3, 1)
    return image


@pytest.mark.parametrize("preset", ALL_PRESETS)
def test_presets_are_deterministic(page: np.ndarray, preset: str) -> None:
    first = degrade_image(page.copy(), preset, seed=1234)
    second = degrade_image(page.copy(), preset, seed=1234)
    assert first.dtype == np.uint8
    assert first.ndim == 3
    assert first.shape[2] == 3
    assert np.array_equal(first, second)


@pytest.mark.parametrize("preset", [p for p in ALL_PRESETS if p != CLEAN])
def test_presets_change_the_image(page: np.ndarray, preset: str) -> None:
    out = degrade_image(page.copy(), preset, seed=7)
    assert out.shape != page.shape or not np.array_equal(out, page)


def test_photo_presets_keep_the_whole_page_with_desk_margin(page: np.ndarray) -> None:
    for preset in ("photo", "photo_folded", "photo_low_light", "thermal_faded"):
        out = degrade_image(page.copy(), preset, seed=3)
        assert out.shape[0] > page.shape[0]
        assert out.shape[1] > page.shape[1]


def test_unknown_preset_is_rejected(page: np.ndarray) -> None:
    with pytest.raises(ValueError, match="unknown degradation preset"):
        degrade_image(page, "sepia", seed=1)


def test_pdfs_are_never_degraded() -> None:
    rng = derive_rng(1, "plan")
    for doc_type in PDF_DOC_TYPES:
        assert {plan_degradation(doc_type, rng) for _ in range(20)} == {CLEAN}
