"""Curves (SPEC §8 step 11): master, then R, G, B."""

from __future__ import annotations

import numpy as np

from reefhue.pipeline import curves as curve_math
from reefhue.pipeline import lut1d
from reefhue.pipeline.context import RenderContext


def _active(spec: dict) -> bool:
    return bool(spec.get("enabled", True)) and not curve_math.is_identity(spec.get("points"))


def curves(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    cv = edit["curves"]
    if not any(_active(cv[ch]) for ch in ("master", "r", "g", "b")):
        return img
    out = np.clip(img, 0.0, 1.0)
    if _active(cv["master"]):
        out = lut1d.apply(out, curve_math.lut(cv["master"]["points"], lut1d.DENSE_SIZE))
    for i, ch in enumerate(("r", "g", "b")):
        if _active(cv[ch]):
            out[..., i] = lut1d.apply(out[..., i], curve_math.lut(cv[ch]["points"], lut1d.DENSE_SIZE))
    return out.astype(np.float32)
