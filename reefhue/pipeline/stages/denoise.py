"""Light spatial denoise (SPEC §8 step 3), in linear light before channel gains."""

from __future__ import annotations

import cv2
import numpy as np

from reefhue.pipeline.context import RenderContext


def denoise(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    amount = float(edit["details"].get("denoise", 0) or 0) / 100.0
    if amount <= 0:
        return img
    # Chroma noise: blur colour differences; luma noise: small edge-preserving bilateral.
    sigma_c = max(0.3, ctx.px(1.0 + 3.0 * amount))
    luma = img.mean(axis=2, keepdims=True)
    chroma = img - luma
    chroma_blur = cv2.GaussianBlur(chroma, (0, 0), sigma_c)
    chroma = chroma + (chroma_blur - chroma) * np.float32(min(1.0, 1.5 * amount))
    sigma_s = max(0.5, ctx.px(1.0 + 2.0 * amount))
    luma2d = np.ascontiguousarray(luma[..., 0])
    luma_f = cv2.bilateralFilter(luma2d, d=0, sigmaColor=0.01 + 0.06 * amount, sigmaSpace=sigma_s)
    luma_out = luma2d + (luma_f - luma2d) * np.float32(amount)
    return (chroma + luma_out[..., None]).astype(np.float32)
