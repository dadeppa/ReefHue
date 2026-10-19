"""Tone (SPEC §8 step 9): contrast, highlights, shadows, whites, blacks.

A 1-D curve is built on display-space luma and applied hue-preserving (ratio)
in the mid/highlights and additively near black (so blacks can be lifted).
"""

from __future__ import annotations

import numpy as np

from reefhue.pipeline import lut1d
from reefhue.pipeline.colorspace import luma
from reefhue.pipeline.context import RenderContext

LUT_MAX = 2.0
LUT_SIZE = 4096
_KEYS = ("contrast", "highlights", "shadows", "whites", "blacks")


def _smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def tone_curve(contrast=0.0, highlights=0.0, shadows=0.0, whites=0.0, blacks=0.0, xs=None) -> np.ndarray:
    """Values in −100…100 → curve samples on ``xs`` (default: LUT grid over [0, 2])."""
    x = np.linspace(0.0, LUT_MAX, LUT_SIZE) if xs is None else np.asarray(xs, dtype=np.float64)
    c, h, s, w, b = (float(v) / 100.0 for v in (contrast, highlights, shadows, whites, blacks))
    y = x.copy()
    y = y + w * 0.15 * _smoothstep(0.6, 1.0, x)
    y = y + b * 0.10 * (1 - _smoothstep(0.0, 0.4, x))
    xm = np.clip(x, 0.0, 1.0)
    y = y + s * 0.25 * (27 / 4) * xm * (1 - xm) ** 2
    y = y + h * 0.30 * _smoothstep(0.4, 1.0, x)
    if c > 1e-3:
        k = 8.0 * c
        sig = lambda v: 1.0 / (1.0 + np.exp(-k * (v - 0.5)))  # noqa: E731
        lo, hi = sig(0.0), sig(1.0)
        inside = (sig(np.clip(y, 0, 1)) - lo) / (hi - lo)
        slope_end = k * sig(1.0) * (1 - sig(1.0)) / (hi - lo)
        y = np.where(y > 1, 1 + (y - 1) * slope_end, np.where(y < 0, y * slope_end, inside))
    elif c < -1e-3:
        y = 0.5 + (y - 0.5) * (1 + 0.5 * c)
    y = np.maximum.accumulate(y)  # keep it monotone
    return y.astype(np.float32)


def tone(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    light = edit["light"]
    values = [float(light.get(k, 0) or 0) for k in _KEYS]
    if not any(values):
        return img
    table = lut1d.resample(tone_curve(*values), LUT_MAX)
    y = luma(img)
    y_new = lut1d.apply(y, table, LUT_MAX) + np.maximum(y - LUT_MAX, 0)
    delta = (y_new - y)[..., None]
    ratio = (y_new / np.maximum(y, 1e-4))[..., None]
    w = np.clip(y / 0.1, 0.0, 1.0)[..., None]
    out = w * (img * ratio) + (1 - w) * (img + delta)
    return out.astype(np.float32)
