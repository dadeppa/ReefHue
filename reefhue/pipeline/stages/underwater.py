"""Underwater channel gains (SPEC §8 step 4, §9): depth prior × auto residual^strength × red offset."""

from __future__ import annotations

import math

import numpy as np

from reefhue.pipeline import water_model
from reefhue.pipeline.context import RenderContext


def _soft_limit(gain: float, cap: float = water_model.GAIN_SOFT_LIMIT) -> float:
    if gain <= 1.0:
        return gain
    return 1.0 + (cap - 1.0) * math.tanh((gain - 1.0) / (cap - 1.0))


def depth_prior(depth_m: float, water_type: str, artificial_light: bool) -> tuple[float, float, float]:
    """``gain_c = exp((K_c − K_ref)·depth)`` with K_ref = smallest coefficient (so gains ≥ 1)."""
    if not depth_m:
        return (1.0, 1.0, 1.0)
    k = water_model.K.get(water_type, water_model.K["coastal"])
    depth = depth_m * (water_model.ARTIFICIAL_LIGHT_DEPTH_FACTOR if artificial_light else 1.0)
    k_ref = min(k)
    return tuple(_soft_limit(math.exp((kc - k_ref) * depth)) for kc in k)  # type: ignore[return-value]


def channel_gains(uw: dict) -> np.ndarray:
    prior = depth_prior(float(uw.get("depth_m", 0) or 0), uw.get("water_type", "coastal"),
                        bool(uw.get("artificial_light")))
    auto = (uw.get("auto_result") or {}).get("gains_rgb") or [1, 1, 1]
    strength = float(uw.get("auto_strength", 100)) / 100.0
    residual = [max(1e-6, float(g)) ** strength for g in auto]
    red = 2.0 ** (float(uw.get("red_comp", 0) or 0) / 100.0 * water_model.RED_COMP_STOPS)
    gains = [prior[i] * residual[i] for i in range(3)]
    gains[0] *= red
    return np.array(gains, dtype=np.float32)


def underwater(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    gains = channel_gains(edit["underwater"])
    if np.allclose(gains, 1.0):
        return img
    return img * gains
