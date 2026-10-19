"""Curve editing rules (max 16 points, spacing ≥ 0.01, endpoints y-only) and evaluation."""

from __future__ import annotations

import numpy as np

from reefhue.pipeline import curves as C


def test_identity_and_normalize():
    assert C.is_identity(C.IDENTITY)
    assert C.normalize_points([[0.5, 0.5]]) == [[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]]
    assert C.normalize_points([[1, 1], [0, 0], [0.5, 0.6], [0.505, 0.7]]) == [[0, 0], [0.5, 0.6], [1, 1]]
    xs = np.linspace(0, 1, 11)
    assert np.allclose(C.evaluate(C.IDENTITY, xs), xs, atol=1e-6)


def test_add_point_rules():
    pts, idx = C.add_point(C.IDENTITY, 0.5, 0.6)
    assert idx == 1 and pts[1] == [0.5, 0.6]
    _, idx = C.add_point(pts, 0.505, 0.2)
    assert idx is None  # too close
    many = C.IDENTITY
    for i in range(1, 20):
        many, _ = C.add_point(many, i / 20, i / 20)
    assert len(many) == C.MAX_POINTS


def test_move_point_respects_neighbours_and_endpoints():
    pts, _ = C.add_point(C.IDENTITY, 0.5, 0.5)
    pts, _ = C.add_point(pts, 0.7, 0.7)
    moved = C.move_point(pts, 1, 0.95, 0.4)
    assert moved[1][0] == 0.69  # limited by next point (0.7 - 0.01)
    end = C.move_point(pts, 0, 0.3, 0.1)
    assert end[0] == [0.0, 0.1]  # endpoint: only y moves
    end = C.move_point(pts, len(pts) - 1, 0.2, 0.9)
    assert end[-1] == [1.0, 0.9]


def test_delete_point_keeps_endpoints():
    pts, _ = C.add_point(C.IDENTITY, 0.5, 0.6)
    assert C.delete_point(pts, 0) == pts
    assert C.delete_point(pts, 1) == [[0.0, 0.0], [1.0, 1.0]]


def test_evaluation_is_monotone_and_passes_through_points():
    pts = C.PRESETS["strong_s"]
    xs = np.linspace(0, 1, 1001)
    ys = C.evaluate(pts, xs)
    assert np.all(np.diff(ys) >= -1e-7)
    for x, y in pts:
        assert abs(float(C.evaluate(pts, np.array([x]))[0]) - y) < 1e-5
