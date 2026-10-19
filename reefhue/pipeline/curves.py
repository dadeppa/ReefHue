"""Tone curves: point editing rules (SPEC §7 coupling) and monotone evaluation. No Qt.

Rules: at most 16 points, strictly increasing x with a minimum spacing of 0.01,
the two endpoints (x = 0 and x = 1) can only move vertically.
"""

from __future__ import annotations

import numpy as np

MAX_POINTS = 16
MIN_SPACING = 0.01
IDENTITY = [[0.0, 0.0], [1.0, 1.0]]
LUT_SIZE = 4096

PRESETS: dict[str, list[list[float]]] = {
    "linear": [[0.0, 0.0], [1.0, 1.0]],
    "light_s": [[0.0, 0.0], [0.25, 0.21], [0.75, 0.79], [1.0, 1.0]],
    "strong_s": [[0.0, 0.0], [0.25, 0.17], [0.75, 0.84], [1.0, 1.0]],
    "lift_shadows": [[0.0, 0.04], [0.25, 0.3], [1.0, 1.0]],
    "fade": [[0.0, 0.08], [0.5, 0.52], [1.0, 0.95]],
}


def _clip01(v: float) -> float:
    return min(1.0, max(0.0, float(v)))


def normalize_points(points) -> list[list[float]]:
    """Sanitise a point list from the sidecar: sorted, spaced, endpoints present, ≤ 16 points."""
    pts = sorted(([_clip01(x), _clip01(y)] for x, y in (points or [])), key=lambda p: p[0])
    if not pts or pts[0][0] > 0.0:
        pts.insert(0, [0.0, pts[0][1] if pts and pts[0][0] == 0 else 0.0])
    pts[0][0] = 0.0
    if pts[-1][0] < 1.0:
        pts.append([1.0, 1.0])
    pts[-1][0] = 1.0
    result = [pts[0]]
    for p in pts[1:-1]:
        if p[0] - result[-1][0] >= MIN_SPACING and 1.0 - p[0] >= MIN_SPACING:
            result.append(p)
    result.append(pts[-1])
    if len(result) > MAX_POINTS:
        result = result[: MAX_POINTS - 1] + [result[-1]]
    return [[round(x, 4), round(y, 4)] for x, y in result]


def is_identity(points) -> bool:
    return normalize_points(points) == [[0.0, 0.0], [1.0, 1.0]]


def add_point(points, x: float, y: float) -> tuple[list[list[float]], int | None]:
    """Insert a point; returns ``(points, index)`` or ``(points, None)`` if not allowed."""
    pts = normalize_points(points)
    x, y = _clip01(x), _clip01(y)
    if len(pts) >= MAX_POINTS:
        return pts, None
    for px, _py in pts:
        if abs(px - x) < MIN_SPACING:
            return pts, None
    pts.append([round(x, 4), round(y, 4)])
    pts.sort(key=lambda p: p[0])
    return pts, pts.index([round(x, 4), round(y, 4)])


def move_point(points, index: int, x: float, y: float) -> list[list[float]]:
    """Move a point; x is limited by its neighbours (min spacing), endpoints keep their x."""
    pts = normalize_points(points)
    y = _clip01(y)
    if index == 0 or index == len(pts) - 1:
        pts[index][1] = round(y, 4)
        return pts
    lo = pts[index - 1][0] + MIN_SPACING
    hi = pts[index + 1][0] - MIN_SPACING
    pts[index] = [round(min(hi, max(lo, float(x))), 4), round(y, 4)]
    return pts


def delete_point(points, index: int) -> list[list[float]]:
    pts = normalize_points(points)
    if 0 < index < len(pts) - 1:
        del pts[index]
    return pts


def evaluate(points, xs: np.ndarray) -> np.ndarray:
    """Monotone cubic (Fritsch–Carlson) interpolation; clipped to [0, 1]."""
    pts = np.array(normalize_points(points), dtype=np.float64)
    x, y = pts[:, 0], pts[:, 1]
    xs = np.clip(np.asarray(xs, dtype=np.float64), 0.0, 1.0)
    if len(x) == 2:
        return np.clip(y[0] + (y[1] - y[0]) * xs, 0, 1).astype(np.float32)
    h = np.diff(x)
    delta = np.diff(y) / h
    m = np.zeros_like(y)
    m[1:-1] = np.where(delta[:-1] * delta[1:] > 0, (delta[:-1] + delta[1:]) / 2, 0.0)
    m[0], m[-1] = delta[0], delta[-1]
    for k in range(len(delta)):
        if delta[k] == 0:
            m[k] = m[k + 1] = 0.0
            continue
        a, b = m[k] / delta[k], m[k + 1] / delta[k]
        s = a * a + b * b
        if s > 9:
            t = 3 / np.sqrt(s)
            m[k], m[k + 1] = t * a * delta[k], t * b * delta[k]
    idx = np.clip(np.searchsorted(x, xs, side="right") - 1, 0, len(x) - 2)
    t = (xs - x[idx]) / h[idx]
    t2, t3 = t * t, t * t * t
    h00, h10, h01, h11 = 2 * t3 - 3 * t2 + 1, t3 - 2 * t2 + t, -2 * t3 + 3 * t2, t3 - t2
    out = h00 * y[idx] + h10 * h[idx] * m[idx] + h01 * y[idx + 1] + h11 * h[idx] * m[idx + 1]
    return np.clip(out, 0, 1).astype(np.float32)


def lut(points, size: int = LUT_SIZE) -> np.ndarray:
    return evaluate(points, np.linspace(0, 1, size))
