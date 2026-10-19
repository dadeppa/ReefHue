"""Levels (SPEC §8 step 8): master, then R, G, B – in display space."""

from __future__ import annotations

import numpy as np

from reefhue.pipeline.colorspace import fast_pow
from reefhue.pipeline.context import RenderContext


def _is_identity(p: dict) -> bool:
    return float(p["black"]) == 0 and float(p["white"]) == 1 and float(p["gamma"]) == 1


def _apply(x: np.ndarray, p: dict) -> np.ndarray:
    black, white, gamma = float(p["black"]), float(p["white"]), float(p["gamma"])
    t = np.maximum((x - np.float32(black)) / np.float32(max(white - black, 1e-4)), 0).astype(np.float32)
    if gamma != 1:
        t = fast_pow(t, 1.0 / gamma)
    return t


def levels(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    lv = edit["levels"]
    out = img if _is_identity(lv["master"]) else _apply(img, lv["master"])
    active = [i for i, ch in enumerate(("r", "g", "b")) if not _is_identity(lv[ch])]
    if not active:
        return out
    out = out.copy() if out is img else out
    for i in active:
        out[..., i] = _apply(out[..., i], lv["rgb"[i]])
    return out
