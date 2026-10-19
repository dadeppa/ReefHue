"""Underwater "magic button" – automatic start values (SPEC §9, algorithm v1). No Qt.

Runs only when asked; writes concrete, editable values (depth, auto residual
gains, per-channel levels) into the edit; export never re-runs it. All
statistics are taken on small images (≈512 px) in linear light, after geometry,
excluding near-black and near-clipped pixels. Videos pool several frames.

Steps:
1. Depth prior from ``depth_m``/``water_type`` (estimated from the red/blue
   balance when ``depth_m`` is 0 or was estimated before).
2. Residual cast on the prior-corrected image: gray world on green/blue, red
   scaled from green with an over-compensation guard (partial, and prior × residual
   within the soft gain limit) → ``auto_result.gains_rgb``. Pixels are weighted by
   local detail so large areas of open water do not dominate the estimate.
3. Per-channel levels from robust percentiles (0.5 % / 99.5 %) of the image as
   it reaches the levels stage, with a limited stretch.
"""

from __future__ import annotations

import copy
import math
from datetime import datetime

import numpy as np

from reefhue.core.timeutil import format_wallclock
from reefhue.pipeline import pipeline as pl
from reefhue.pipeline import water_model
from reefhue.pipeline.colorspace import LUMA, linear_to_srgb
from reefhue.pipeline.params import AUTO_ALGO_VERSION, PARAMS_BY_KEY, couple_levels, quantize
from reefhue.pipeline.stages.underwater import depth_prior

ANALYSIS_SIZE = 512
LOW, HIGH = 0.02, 0.98          # display-space thresholds for "near black" / "near clipped"
DEPTH_CONFIDENCE = 0.8          # the prior covers most of the cast, the residual the rest
RED_EXPONENT = 0.85             # partial red compensation (over-compensation guard)
RED_GAIN_LIMIT = 4.0
BLUE_GREEN_LIMITS = (0.5, 2.5)
LEVEL_PERCENTILES = (0.5, 99.5)
MAX_BLACK = 0.12                # limited stretch: never cut more than this …
MIN_WHITE = 0.75                # … or lift white above this
MIN_PIXELS = 200
TEXTURE_FLOOR = 0.15            # weight of featureless areas (open water) in the cast estimate


def _smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def analysis_image(src_srgb: np.ndarray, edit: dict) -> np.ndarray:
    """Linear RGB image of the geometry-corrected source, at analysis size."""
    h, w = src_srgb.shape[:2]
    ctx = pl.make_context((w, h), (w, h), edit)
    return pl.process_geometry(src_srgb, edit, ctx)


def pixel_weights(lin: np.ndarray, artificial_light: bool) -> np.ndarray:
    """1 for usable pixels, 0 for near-black / near-clipped; bright regions down-weighted with a lamp."""
    disp = linear_to_srgb(np.clip(lin, 0, None))
    luma = disp @ LUMA
    w = ((disp.max(axis=1) < HIGH) & (luma > LOW)).astype(np.float32)
    if artificial_light:
        w *= 1.0 - _smoothstep(0.55, 0.95, luma)  # lamp-lit foreground is not representative
    return w


def texture_weights(lin: np.ndarray) -> np.ndarray:
    """Per-pixel weight from local detail: open water (smooth) counts little, reef/sand/fish fully."""
    import cv2  # noqa: PLC0415

    luma = np.ascontiguousarray(linear_to_srgb(np.clip(lin, 0, None)) @ LUMA, dtype=np.float32)
    gx = cv2.Sobel(luma, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(luma, cv2.CV_32F, 0, 1, ksize=3)
    detail = cv2.blur(np.sqrt(gx * gx + gy * gy), (9, 9))
    ref = float(np.percentile(detail, 75)) or 1e-6
    return np.clip(detail / ref, TEXTURE_FLOOR, 1.0).reshape(-1)


def weighted_means(pixels: np.ndarray, weights: np.ndarray) -> np.ndarray:
    total = float(weights.sum())
    if total < MIN_PIXELS * 0.1:
        return pixels.mean(axis=0) if len(pixels) else np.ones(3, np.float32)
    return (pixels * weights[:, None]).sum(axis=0) / total


def estimate_depth(means: np.ndarray, water_type: str, artificial_light: bool) -> float:
    """Depth (m) whose prior would balance red against blue (gray-world assumption)."""
    k = water_model.K.get(water_type, water_model.K["coastal"])
    dk = k[0] - k[2]
    r, b = max(float(means[0]), 1e-5), max(float(means[2]), 1e-5)
    if dk <= 0.01 or r >= b:
        return 0.0
    depth = math.log(b / r) / dk * DEPTH_CONFIDENCE
    if artificial_light:
        depth /= water_model.ARTIFICIAL_LIGHT_DEPTH_FACTOR
    return quantize(PARAMS_BY_KEY[("underwater", "depth_m")], depth)


def residual_gains(means: np.ndarray, prior: np.ndarray | None = None) -> list[float]:
    """Gray world with green as reference; red only partially and limited.

    The red residual is also limited so that prior × residual stays within the
    soft gain limit (more red gain only amplifies noise).
    """
    r, g, b = (max(float(v), 1e-5) for v in means)
    gain_b = min(BLUE_GREEN_LIMITS[1], max(BLUE_GREEN_LIMITS[0], g / b))
    red_cap = RED_GAIN_LIMIT
    if prior is not None:
        red_cap = min(red_cap, max(1.0, water_model.GAIN_SOFT_LIMIT / max(float(prior[0]), 1e-6)))
    gain_r = min(red_cap, max(BLUE_GREEN_LIMITS[0], (g / r) ** RED_EXPONENT))
    return [round(gain_r, 4), 1.0, round(gain_b, 4)]


def channel_levels(display: np.ndarray, weights: np.ndarray) -> dict:
    """Per-channel black/white points from robust percentiles, with a limited stretch."""
    usable = display[weights > 0.5] if (weights > 0.5).sum() >= MIN_PIXELS else display
    black_p = PARAMS_BY_KEY[("levels", "black")]
    white_p = PARAMS_BY_KEY[("levels", "white")]
    result = {}
    for i, ch in enumerate("rgb"):
        lo, hi = np.percentile(usable[:, i], LEVEL_PERCENTILES) if len(usable) else (0.0, 1.0)
        black = quantize(black_p, min(max(float(lo), 0.0), MAX_BLACK))
        white = quantize(white_p, max(min(float(hi), 1.0), MIN_WHITE))
        black, white = couple_levels(black, white, "black")
        result[ch] = {"black": black, "white": white}
    return result


def analyze(images: list[np.ndarray], edit: dict) -> dict:
    """Run the analysis on one (photo, video frame) or several (video) sRGB float images."""
    uw = edit["underwater"]
    probe = copy.deepcopy(edit)
    # Analyse the source without previous auto/levels/WB results.
    probe["underwater"]["auto_result"]["gains_rgb"] = [1, 1, 1]
    probe["white_balance"].update(temperature=0, tint=0)
    for ch in ("master", "r", "g", "b"):
        probe["levels"][ch].update(black=0, white=1, gamma=1)
    linear = [analysis_image(img, probe) for img in images]
    pixels = np.concatenate([lin.reshape(-1, 3) for lin in linear], axis=0)
    usable = pixel_weights(pixels, bool(uw.get("artificial_light")))
    weights = usable * np.concatenate([texture_weights(lin) for lin in linear])

    depth = float(uw.get("depth_m") or 0)
    estimated = bool((uw.get("auto_result") or {}).get("depth_estimated"))
    if depth == 0 or estimated:
        depth = estimate_depth(weighted_means(pixels, weights), uw.get("water_type", "coastal"),
                               bool(uw.get("artificial_light")))
        estimated = True
    prior = np.array(depth_prior(depth, uw.get("water_type", "coastal"), bool(uw.get("artificial_light"))),
                     np.float32)
    gains = residual_gains(weighted_means(pixels * prior, weights), prior)

    # Levels: measure each image where the levels stage will actually see it, then pool.
    probe["underwater"]["depth_m"] = depth
    probe["underwater"]["auto_result"]["gains_rgb"] = gains
    at_levels = []
    for lin in linear:
        h, w = lin.shape[:2]
        ctx = pl.make_context((w, h), (w, h), probe)
        at_levels.append(pl.run_stages(lin, probe, ctx, part="color", until="levels").reshape(-1, 3))
    levels = channel_levels(np.concatenate(at_levels, axis=0), usable)
    return {"depth_m": depth, "depth_estimated": estimated, "gains_rgb": gains, "levels": levels}


def apply_result(edit: dict, result: dict, source: str, now: datetime | None = None) -> dict:
    """New edit with the analysis written as ordinary values (WB reset to neutral)."""
    new = copy.deepcopy(edit)
    uw = new["underwater"]
    uw["depth_m"] = result["depth_m"]
    uw["auto_result"] = {
        "analyzed_at": format_wallclock((now or datetime.now()).replace(microsecond=0)),
        "source": source,
        "gains_rgb": list(result["gains_rgb"]),
        "levels": copy.deepcopy(result["levels"]),
        "algo_version": AUTO_ALGO_VERSION,
        "depth_estimated": bool(result["depth_estimated"]),
    }
    for ch, values in result["levels"].items():
        new["levels"][ch]["black"] = values["black"]
        new["levels"][ch]["white"] = values["white"]
    new["white_balance"].update(temperature=0, tint=0)
    return new


def downscale(img: np.ndarray, size: int = ANALYSIS_SIZE) -> np.ndarray:
    import cv2  # noqa: PLC0415

    h, w = img.shape[:2]
    if max(h, w) <= size:
        return img
    f = size / max(h, w)
    return cv2.resize(img, (max(1, round(w * f)), max(1, round(h * f))), interpolation=cv2.INTER_AREA)


def segment_times(start: float, end: float, count: int = 7) -> list[float]:
    """Frame times spread evenly over a video segment (avoiding the very first/last frame)."""
    if end <= start:
        return [start]
    step = (end - start) / count
    return [start + step * (i + 0.5) for i in range(count)]
