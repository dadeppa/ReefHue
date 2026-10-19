"""Color (SPEC §8 step 10): vibrance and saturation in display space."""

from __future__ import annotations

import numpy as np

from reefhue.pipeline.colorspace import luma
from reefhue.pipeline.context import RenderContext


def color(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    vib = float(edit["color"].get("vibrance", 0) or 0) / 100.0
    sat = float(edit["color"].get("saturation", 0) or 0) / 100.0
    if not vib and not sat:
        return img
    y = luma(img)[..., None]
    factor = np.float32(1.0 + sat)
    if vib:
        mx, mn = img.max(axis=2), img.min(axis=2)
        current = np.clip((mx - mn) / np.maximum(mx, 1e-4), 0.0, 1.0)
        # vibrance boosts weakly saturated colours most (and reduces them most when negative)
        factor = factor * (1.0 + np.float32(vib) * (1.0 - current))[..., None]
    return (y + (img - y) * factor).astype(np.float32)
