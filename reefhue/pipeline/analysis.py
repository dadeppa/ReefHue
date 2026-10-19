"""Histogram and clipping analysis of a rendered (display-space) image. No Qt."""

from __future__ import annotations

import numpy as np

from reefhue.pipeline.colorspace import LUMA

HIST_BINS = 256
MAX_SAMPLES = 400_000


def histogram(img_u8: np.ndarray) -> dict[str, np.ndarray]:
    """Counts per bin for r, g, b and luminance (normalised to the largest bin = 1)."""
    flat = img_u8.reshape(-1, 3)
    if flat.shape[0] > MAX_SAMPLES:
        flat = flat[:: flat.shape[0] // MAX_SAMPLES + 1]
    result = {}
    for i, key in enumerate("rgb"):
        result[key] = np.bincount(flat[:, i], minlength=HIST_BINS).astype(np.float32)
    lum = np.clip(np.rint(flat.astype(np.float32) @ LUMA), 0, 255).astype(np.int64)
    result["l"] = np.bincount(lum, minlength=HIST_BINS).astype(np.float32)
    peak = max(float(v[1:-1].max()) for v in result.values()) or 1.0  # ignore the clipped end bins
    return {k: np.minimum(v / peak, 1.0) for k, v in result.items()}


def clipping_masks(img_u8: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(blown, crushed)``: any channel at 255 / all channels at 0."""
    blown = (img_u8 == 255).any(axis=2)
    crushed = (img_u8 == 0).all(axis=2)
    return blown, crushed


def clipping_overlay(img_u8: np.ndarray) -> np.ndarray:
    """RGBA uint8 overlay: red for blown highlights, blue for crushed shadows."""
    blown, crushed = clipping_masks(img_u8)
    overlay = np.zeros(img_u8.shape[:2] + (4,), dtype=np.uint8)
    overlay[blown] = (255, 40, 40, 200)
    overlay[crushed] = (40, 110, 255, 200)
    return overlay
