"""sRGB transfer functions and luma (float32, vectorised). No Qt."""

from __future__ import annotations

import cv2
import numpy as np

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)  # Rec.709 / sRGB primaries
_A = 0.055


def fast_pow(x: np.ndarray, exponent: float) -> np.ndarray:
    """``x ** exponent`` for non-negative float32 arrays (OpenCV for large images, numpy otherwise).

    cv2.pow treats 1-D arrays as column vectors and rejects scalars, hence the guard.
    """
    x = np.asarray(x, dtype=np.float32)
    if x.ndim >= 2 and x.size >= 4096:
        return cv2.pow(np.ascontiguousarray(x), float(exponent)).reshape(x.shape)
    return np.power(x, np.float32(exponent)).astype(np.float32)


def _srgb_to_linear_float(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    low = x * np.float32(1 / 12.92)
    high = fast_pow(np.maximum((x + _A) / (1 + _A), 0), 2.4)
    return np.where(x <= 0.04045, low, high).astype(np.float32)


# 8-bit sources: the exact transfer function sampled at all 256 code values.
_LINEAR_LUT_8 = _srgb_to_linear_float(np.arange(256, dtype=np.float32) / 255.0)


def srgb_to_linear(x: np.ndarray) -> np.ndarray:
    """sRGB-encoded values → linear light. ``uint8`` input (0–255) uses an exact lookup table."""
    if isinstance(x, np.ndarray) and x.dtype == np.uint8:
        return _LINEAR_LUT_8[x]
    return _srgb_to_linear_float(x)


def linear_to_srgb(x: np.ndarray) -> np.ndarray:
    """Values above 1 follow the same curve (highlight recovery happens later in display space)."""
    x = np.asarray(x, dtype=np.float32)
    low = x * np.float32(12.92)
    high = np.float32(1 + _A) * fast_pow(np.maximum(x, 0), 1 / 2.4) - np.float32(_A)
    return np.where(x <= 0.0031308, low, high).astype(np.float32)


def luma(img: np.ndarray) -> np.ndarray:
    return (img @ LUMA).astype(np.float32)
