"""Run per-pixel work on horizontal strips in parallel (numpy releases the GIL). No Qt.

Only for *pointwise* operations (a pixel's result depends on that pixel alone), so
strips give exactly the same result as one call. Calls from inside a strip run
inline, so nesting cannot deadlock the shared pool.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import numpy as np

WORKERS = max(1, min(8, (os.cpu_count() or 2) - 1))
MIN_PIXELS = 256 * 1024   # below this, threading costs more than it saves
MIN_ROWS = 16

_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()
_inside = threading.local()


def _executor() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(WORKERS, thread_name_prefix="reefhue-strip")
        return _pool


def _run(fn: Callable[[np.ndarray], np.ndarray], part: np.ndarray) -> np.ndarray:
    _inside.active = True
    try:
        return fn(part)
    finally:
        _inside.active = False


def map_rows(fn: Callable[[np.ndarray], np.ndarray], img: np.ndarray) -> np.ndarray:
    """``fn`` applied to ``img`` (H×W×C), split into row strips processed in parallel."""
    h = img.shape[0]
    pixels = h * (img.shape[1] if img.ndim > 1 else 1)
    strips = min(WORKERS, h // MIN_ROWS, pixels // MIN_PIXELS)
    if strips < 2 or getattr(_inside, "active", False):
        return fn(img)
    step = -(-h // strips)
    parts = list(_executor().map(lambda y: _run(fn, img[y:y + step]), range(0, h, step)))
    return np.concatenate(parts, axis=0)
