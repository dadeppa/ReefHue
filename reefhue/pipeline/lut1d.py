"""Fast 1-D lookup (dense table + nearest-index gather, much faster than np.interp). No Qt."""

from __future__ import annotations

import numpy as np

DENSE_SIZE = 16384


def resample(curve: np.ndarray, x_max: float, size: int = DENSE_SIZE) -> np.ndarray:
    """Resample a curve sampled uniformly on [0, x_max] to a dense table of ``size`` entries."""
    src = np.linspace(0.0, x_max, len(curve))
    return np.interp(np.linspace(0.0, x_max, size), src, curve).astype(np.float32)


def apply(x: np.ndarray, table: np.ndarray, x_max: float = 1.0) -> np.ndarray:
    """``table`` sampled uniformly on [0, x_max]; inputs are clipped to that range."""
    n = len(table) - 1
    idx = np.clip(x, 0.0, x_max) * np.float32(n / x_max) + np.float32(0.5)
    return table[idx.astype(np.int32)]
