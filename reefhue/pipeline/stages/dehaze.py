"""Dehaze (SPEC §8 step 6): light dark-channel-prior method in linear light."""

from __future__ import annotations

import cv2
import numpy as np

from reefhue.pipeline.context import RenderContext

RADIUS_REL = 0.01      # dark-channel window radius: 1 % of the short side
MIN_TRANSMISSION = 0.12
WORK_RADIUS = 3.0  # dark-channel window radius (px) at the working resolution


def estimate_airlight(img: np.ndarray) -> np.ndarray:
    """Mean colour of the 0.1 % pixels with the brightest dark channel (on a small copy)."""
    h, w = img.shape[:2]
    factor = min(1.0, 256 / max(h, w))
    small = cv2.resize(img, (max(1, int(w * factor)), max(1, int(h * factor))), interpolation=cv2.INTER_AREA)
    dark = cv2.erode(small.min(axis=2), np.ones((5, 5), np.uint8))
    flat = dark.reshape(-1)
    count = max(1, flat.size // 1000)
    idx = np.argpartition(flat, -count)[-count:]
    airlight = small.reshape(-1, 3)[idx].mean(axis=0)
    return np.maximum(airlight, 1e-3).astype(np.float32)


def dehaze(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    amount = float(edit["details"].get("dehaze", 0) or 0) / 100.0
    if amount == 0:
        return img
    airlight = ctx.stats.get("airlight")
    if airlight is None:
        airlight = estimate_airlight(img)
        ctx.stats["airlight"] = airlight
    airlight = np.asarray(airlight, dtype=np.float32)
    if amount < 0:  # add haze
        k = np.float32(-amount * 0.6)
        return img * (1 - k) + airlight * k
    t = np.maximum(1.0 - np.float32(0.95 * amount) * dark_channel(img, airlight, ctx.rel(RADIUS_REL)),
                   MIN_TRANSMISSION)[..., None]
    return ((img - airlight) / t + airlight).astype(np.float32)


def dark_channel(img: np.ndarray, airlight: np.ndarray, radius: float) -> np.ndarray:
    """Smoothed dark channel of ``img / airlight`` at full size.

    Computed where the window radius is ~WORK_RADIUS px and upsampled: the map is
    blurred anyway, and the working resolution follows the radius, so preview,
    export and region renders compute the same thing (and it is fast).
    """
    h, w = img.shape[:2]
    f = min(1.0, WORK_RADIUS / radius) if radius > 0 else 1.0
    small = img if f >= 1.0 else cv2.resize(img, (max(1, round(w * f)), max(1, round(h * f))),
                                            interpolation=cv2.INTER_AREA)
    small = small / airlight
    darkest = np.minimum(np.minimum(small[..., 0], small[..., 1]), small[..., 2])
    r = max(1, int(round(radius * f)))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2 * r + 1, 2 * r + 1))
    darkest = cv2.blur(cv2.erode(np.ascontiguousarray(darkest, dtype=np.float32), kernel), (4 * r + 1, 4 * r + 1))
    if darkest.shape[:2] != (h, w):
        darkest = cv2.resize(darkest, (w, h), interpolation=cv2.INTER_LINEAR)
    return darkest
