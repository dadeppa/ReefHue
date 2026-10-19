"""Per-tab Auto buttons (SPEC §7): Light (exposure/contrast) and Color (white balance). No Qt.

The underwater "magic button" (SPEC §9) is milestone 6; these are simple,
deterministic estimates that write ordinary, editable values.
"""

from __future__ import annotations

import copy
import math

import numpy as np

from reefhue.pipeline import pipeline as pl
from reefhue.pipeline.colorspace import LUMA, srgb_to_linear
from reefhue.pipeline.context import RenderContext
from reefhue.pipeline.params import PARAMS_BY_KEY, quantize
from reefhue.pipeline.stages.white_balance import temperature_tint_for_neutral

TARGET_MID_GREY = 0.42  # display-space median target
LOW, HIGH = 0.02, 0.98  # exclude near-clipped pixels (SPEC §9)


def _valid_pixels(img: np.ndarray) -> np.ndarray:
    flat = img.reshape(-1, 3)
    mask = (flat.min(axis=1) > LOW) & (flat.max(axis=1) < HIGH)
    return flat[mask] if mask.sum() > 100 else flat


def auto_white_balance(pre_wb_linear: np.ndarray) -> tuple[float, float]:
    """Gray-world on the image entering the WB stage → (temperature, tint)."""
    pixels = _valid_pixels(np.clip(pre_wb_linear, 0, 1))
    return temperature_tint_for_neutral(pixels.mean(axis=0))


def eyedropper_white_balance(pre_wb_linear: np.ndarray, x: int, y: int, size: int) -> tuple[float, float]:
    """Average a ``size``×``size`` patch around (x, y) and neutralise it."""
    h, w = pre_wb_linear.shape[:2]
    half = max(0, size // 2)
    x0, x1 = max(0, x - half), min(w, x + half + 1)
    y0, y1 = max(0, y - half), min(h, y + half + 1)
    patch = pre_wb_linear[y0:y1, x0:x1].reshape(-1, 3)
    return temperature_tint_for_neutral(patch.mean(axis=0))


def auto_light(geom_linear: np.ndarray, edit: dict, ctx: RenderContext) -> dict[str, float]:
    """Exposure + contrast suggestion from a render without Light adjustments."""
    neutral = copy.deepcopy(edit)
    for key in neutral["light"]:
        neutral["light"][key] = 0
    out = pl.finalize(pl.process_color(geom_linear, neutral, ctx))
    pixels = _valid_pixels(out)
    lum = pixels @ LUMA
    median = float(np.median(lum)) if lum.size else TARGET_MID_GREY
    target_lin = float(srgb_to_linear(np.float32(TARGET_MID_GREY)))
    median_lin = max(1e-4, float(srgb_to_linear(np.float32(median))))
    ev = max(-2.5, min(2.5, math.log2(target_lin / median_lin)))
    spread = float(np.percentile(lum, 95) - np.percentile(lum, 5)) if lum.size else 0.6
    contrast = max(-20.0, min(40.0, (0.6 - spread) * 80.0))
    return {
        "exposure_ev": quantize(PARAMS_BY_KEY[("light", "exposure_ev")], ev),
        "contrast": quantize(PARAMS_BY_KEY[("light", "contrast")], round(contrast)),
    }
