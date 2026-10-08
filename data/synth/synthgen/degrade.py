"""Make clean renders look like real-world captures: scans, phone photos, faded thermal paper.

Paper and ink effects come from Augraphy; the phone-photo look (perspective warp onto a desk,
uneven light, blur, sensor noise, JPEG) is done with OpenCV/numpy. Everything is seeded from the
spec's ``render_seed``. Augraphy draws from the global ``random``/``numpy.random`` state, so we
seed both before every call; ``Folding`` is used with ``fold_noise=0`` because its noise comes
from an unseeded numba generator.

The document is never cropped, so every printed field in the truth stays visible.
"""

from __future__ import annotations

import random
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path

import augraphy
import cv2
import numpy as np

from synthgen.assemble import CLEAN, SCREEN_CAPTURE
from synthgen.spec import DocSpec, OutputLayout

Image = np.ndarray
MAX_SIDE = 2000
DESK_COLOURS_BGR = (
    (52, 84, 128),  # oak
    (40, 58, 92),  # walnut
    (148, 150, 152),  # grey laminate
    (205, 210, 214),  # white table
    (60, 58, 62),  # dark granite
)


def _seed_globals(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed % 2**32)


def _augment(image: Image, augmentation: Callable[[Image], Image], seed: int) -> Image:
    _seed_globals(seed)
    return augmentation(image)


def paper_effects(image: Image, rng: np.random.Generator, seed: int, heavy: bool = False) -> Image:
    """Off-white paper tint, texture and slight ink bleed."""
    steps: list[Callable[[Image], Image]] = [
        augraphy.ColorPaper(hue_range=(18, 40), saturation_range=(4, 22 if heavy else 12)),
        augraphy.BrightnessTexturize(texturize_range=(0.9, 0.99), deviation=0.03),
        augraphy.NoiseTexturize(sigma_range=(2, 5), turbulence_range=(2, 4)),
    ]
    if heavy or rng.random() < 0.5:
        steps.append(augraphy.InkBleed(intensity_range=(0.1, 0.3), severity=(0.2, 0.3)))
    for offset, step in enumerate(steps):
        image = _augment(image, step, seed + offset)
    return image


def fade_thermal(image: Image, rng: np.random.Generator, seed: int) -> Image:
    """Thermal paper that has faded: lower ink contrast plus streaky low-ink lines."""
    keep = rng.uniform(0.45, 0.7)
    faded = 255 - (255 - image.astype(np.float32)) * keep
    image = _augment(
        faded.astype(np.uint8),
        augraphy.LowInkRandomLines(count_range=(4, 10), use_consistent_lines=False),
        seed,
    )
    return image


def fold(image: Image, rng: np.random.Generator, seed: int) -> Image:
    folding = augraphy.Folding(
        fold_count=int(rng.integers(1, 4)),
        fold_noise=0,
        fold_angle_range=(-5, 5),
        gradient_width=(0.1, 0.2),
        gradient_height=(0.005, 0.012),
    )
    return _augment(image, folding, seed)


def desk_background(height: int, width: int, rng: np.random.Generator) -> Image:
    """A plain desk-like surface: base colour, soft blotches and (sometimes) wood grain."""
    colour = np.array(DESK_COLOURS_BGR[int(rng.integers(len(DESK_COLOURS_BGR)))], np.float32)
    blotches = cv2.resize(
        rng.normal(0, 1, (height // 80 + 2, width // 80 + 2)).astype(np.float32),
        (width, height),
        interpolation=cv2.INTER_CUBIC,
    )
    surface = np.ones((height, width, 3), np.float32) * colour + blotches[..., None] * 10
    if rng.random() < 0.5:
        rows = np.arange(height, dtype=np.float32)[:, None]
        grain = np.sin((rows + blotches * 25) / rng.uniform(5, 12)) * rng.uniform(4, 10)
        surface += grain[..., None]
    surface += rng.normal(0, 3, surface.shape)
    return np.clip(surface, 0, 255).astype(np.uint8)


def warp_onto_desk(image: Image, rng: np.random.Generator) -> Image:
    """Place the page on a desk with a random tilt, perspective and drop shadow."""
    height, width = image.shape[:2]
    pad = int(rng.uniform(0.07, 0.2) * max(height, width))
    canvas_h, canvas_w = height + 2 * pad, width + 2 * pad
    jitter = 0.045 * min(height, width)
    src = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    dst = src + pad + rng.uniform(-jitter, jitter, (4, 2)).astype(np.float32)
    rotation = cv2.getRotationMatrix2D((canvas_w / 2, canvas_h / 2), rng.uniform(-7, 7), 1.0)
    dst = cv2.transform(dst[None], rotation)[0].astype(np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst)

    size = (canvas_w, canvas_h)
    page = cv2.warpPerspective(image, matrix, size, flags=cv2.INTER_LINEAR)
    mask = cv2.warpPerspective(np.ones((height, width), np.float32), matrix, size)
    mask = cv2.GaussianBlur(mask, (3, 3), 0)[..., None]

    shift = np.float32([[1, 0, pad * 0.04], [0, 1, pad * 0.06]])
    shadow = cv2.GaussianBlur(cv2.warpAffine(mask[..., 0], shift, size), (0, 0), pad * 0.08 + 1)
    desk = desk_background(canvas_h, canvas_w, rng).astype(np.float32)
    desk *= 1 - 0.45 * shadow[..., None]
    photo = desk * (1 - mask) + page.astype(np.float32) * mask
    return np.clip(photo, 0, 255).astype(np.uint8)


def uneven_light(image: Image, rng: np.random.Generator, low_light: bool) -> Image:
    """Directional light falloff; optionally underexposed with a warm cast."""
    height, width = image.shape[:2]
    angle = rng.uniform(0, 2 * np.pi)
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    ramp = (xs / width - 0.5) * np.cos(angle) + (ys / height - 0.5) * np.sin(angle)
    gain = 1.0 + ramp * rng.uniform(0.2, 0.45)
    if low_light:
        gain *= rng.uniform(0.5, 0.7)
    lit = image.astype(np.float32) * gain[..., None]
    if low_light:
        lit *= np.array([0.85, 0.95, 1.08], np.float32)  # BGR: warm indoor bulb
    return np.clip(lit, 0, 255).astype(np.uint8)


def camera_noise(image: Image, rng: np.random.Generator, sigma: float, blur: float) -> Image:
    blurred = cv2.GaussianBlur(image, (0, 0), blur) if blur > 0 else image
    noisy = blurred.astype(np.float32) + rng.normal(0, sigma, image.shape)
    return np.clip(noisy, 0, 255).astype(np.uint8)


def limit_size(image: Image, max_side: int = MAX_SIDE) -> Image:
    scale = max_side / max(image.shape[:2])
    if scale >= 1:
        return image
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def shadow_cast(image: Image, seed: int) -> Image:
    shadow = augraphy.ShadowCast(
        shadow_opacity_range=(0.15, 0.4), shadow_blur_kernel_range=(101, 201)
    )
    return _augment(image, shadow, seed)


def _photo(image: Image, rng: np.random.Generator, seed: int, *, low_light: bool) -> Image:
    photo = limit_size(warp_onto_desk(image, rng), int(MAX_SIDE * 0.9))
    if rng.random() < 0.5:
        photo = shadow_cast(photo, seed + 7)
    photo = uneven_light(photo, rng, low_light)
    sigma = rng.uniform(6, 11) if low_light else rng.uniform(2, 5)
    return camera_noise(photo, rng, sigma=sigma, blur=rng.uniform(0.4, 1.3))


def degrade_image(image: Image, preset: str, seed: int) -> Image:
    """Apply a named preset to a BGR uint8 image."""
    rng = np.random.default_rng(seed)
    if preset == CLEAN:
        return image
    if preset == SCREEN_CAPTURE:
        crop_top = int(rng.integers(0, int(image.shape[0] * 0.04) + 1))
        scale = rng.uniform(0.55, 0.85)
        return cv2.resize(image[crop_top:], None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    if preset == "scan":
        page = paper_effects(image, rng, seed)
        border = int(0.03 * max(page.shape[:2]))  # scanner bed margin; rotation never clips
        page = cv2.copyMakeBorder(
            page, border, border, border, border, cv2.BORDER_CONSTANT, value=(250, 250, 250)
        )
        rotation = cv2.getRotationMatrix2D(
            (page.shape[1] / 2, page.shape[0] / 2), rng.uniform(-1.5, 1.5), 1.0
        )
        page = cv2.warpAffine(
            page, rotation, (page.shape[1], page.shape[0]), borderValue=(250, 250, 250)
        )
        return camera_noise(page, rng, sigma=2, blur=rng.uniform(0.3, 0.7))
    if preset == "thermal_faded":
        return _photo(
            paper_effects(fade_thermal(image, rng, seed), rng, seed + 1), rng, seed, low_light=False
        )
    if preset == "photo_folded":
        page = fold(paper_effects(image, rng, seed, heavy=True), rng, seed + 3)
        return _photo(page, rng, seed, low_light=False)
    if preset == "photo":
        return _photo(paper_effects(image, rng, seed), rng, seed, low_light=False)
    if preset == "photo_low_light":
        return _photo(paper_effects(image, rng, seed), rng, seed, low_light=True)
    raise ValueError(f"unknown degradation preset {preset!r}")


def degrade_spec(spec: DocSpec, layout: OutputLayout) -> Path:
    target = layout.document_path(spec)
    clean_png = layout.clean / f"{spec.id}.png"
    if spec.is_pdf:
        shutil.copyfile(layout.clean / f"{spec.id}.pdf", target)
        shutil.copyfile(clean_png, layout.docs / f"{spec.id}.preview.png")
        return target
    if spec.degrade == CLEAN:
        shutil.copyfile(clean_png, target)
        return target
    image = cv2.imread(str(clean_png), cv2.IMREAD_COLOR)
    result = degrade_image(image, spec.degrade, spec.render_seed)
    quality = int(np.random.default_rng(spec.render_seed).integers(70, 92))
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if target.suffix == ".jpg" else []
    if not cv2.imwrite(str(target), result, params):
        raise OSError(f"could not write {target}")
    return target


def degrade_specs(specs: Iterable[DocSpec], layout: OutputLayout) -> int:
    layout.ensure()
    done = 0
    for spec in specs:
        degrade_spec(spec, layout)
        done += 1
    return done
