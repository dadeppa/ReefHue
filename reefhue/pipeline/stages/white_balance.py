"""White balance (temperature/tint) and exposure in linear light (SPEC §8 step 5)."""

from __future__ import annotations

import math

import numpy as np

from reefhue.pipeline.context import RenderContext

TEMPERATURE_STOPS = 1.0  # ±100 → red ×2^±1, blue ×2^∓1
TINT_STOPS = 0.5         # ±100 → green ×2^∓0.5 (positive tint = magenta)


def wb_gains(temperature: float, tint: float) -> np.ndarray:
    a = float(temperature) / 100.0 * TEMPERATURE_STOPS
    c = float(tint) / 100.0 * TINT_STOPS
    return np.array([2.0 ** a, 2.0 ** -c, 2.0 ** -a], dtype=np.float32)


def temperature_tint_for_neutral(rgb) -> tuple[float, float]:
    """Inverse of :func:`wb_gains`: values that make the linear colour ``rgb`` neutral.

    Results are clamped to ±100 (a stronger cast is only partially removed).
    """
    r, g, b = (max(1e-6, float(v)) for v in rgb)
    a = 0.5 * math.log2(b / r)
    gain_g = math.sqrt(r * b) / g
    c = -math.log2(gain_g)
    t = max(-100.0, min(100.0, a / TEMPERATURE_STOPS * 100.0))
    m = max(-100.0, min(100.0, c / TINT_STOPS * 100.0))
    return round(t), round(m)


def white_balance_exposure(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    wb = edit["white_balance"]
    gains = wb_gains(wb.get("temperature", 0) or 0, wb.get("tint", 0) or 0)
    gains = gains * np.float32(2.0 ** float(edit["light"].get("exposure_ev", 0) or 0))
    if np.allclose(gains, 1.0):
        return img
    return img * gains
