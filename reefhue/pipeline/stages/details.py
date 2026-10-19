"""Details (SPEC §8 step 12): clarity, then sharpening (last). Display space, luma only."""

from __future__ import annotations

import cv2
import numpy as np

from reefhue.pipeline.colorspace import luma
from reefhue.pipeline.context import RenderContext

CLARITY_RADIUS_REL = 0.008  # Gaussian sigma: 0.8 % of the short side
SHARPEN_MAX_AMOUNT = 1.5


def clarity(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    amount = float(edit["details"].get("clarity", 0) or 0) / 100.0
    if not amount:
        return img
    y = luma(img)
    sigma = max(0.5, ctx.rel(CLARITY_RADIUS_REL))
    detail = y - cv2.GaussianBlur(y, (0, 0), sigma)
    midtones = np.clip(1.0 - (2.0 * np.clip(y, 0, 1) - 1.0) ** 2, 0.0, 1.0)
    return (img + (np.float32(0.8 * amount) * detail * midtones)[..., None]).astype(np.float32)


def sharpen(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    amount = float(edit["details"].get("sharpen_amount", 0) or 0) / 100.0
    if not amount:
        return img
    sigma = ctx.px(float(edit["details"].get("sharpen_radius", 1.0) or 1.0))
    if sigma < 0.25:  # below a quarter pixel the effect is invisible at this preview scale
        return img
    y = luma(img)
    detail = y - cv2.GaussianBlur(y, (0, 0), sigma)
    return (img + (np.float32(amount * SHARPEN_MAX_AMOUNT) * detail)[..., None]).astype(np.float32)
