"""LUT stages (SPEC §8 steps 1 and 13): input LUT (position "start", on the decoded
sRGB/Log-encoded image) and output LUT (position "end", on the display-space result)."""

from __future__ import annotations

import numpy as np

from reefhue.pipeline.context import RenderContext


def _apply_lut(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    lut = edit.get("lut") or {}
    if not lut.get("file") or ctx.lut_provider is None:
        return img
    table = ctx.lut_provider(lut["file"])
    if table is None:
        return img
    intensity = float(lut.get("intensity", 100)) / 100.0
    mapped = table(img)  # the LUT clips to its own domain
    return (img + (mapped - img) * np.float32(intensity)).astype(np.float32)


def input_lut(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    if (edit.get("lut") or {}).get("position") != "start":
        return img
    return _apply_lut(img, edit, ctx)


def output_lut(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    if (edit.get("lut") or {}).get("position", "end") != "end":
        return img
    return _apply_lut(img, edit, ctx)
