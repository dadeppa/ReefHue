"""Tunable constants for the underwater depth prior (SPEC §9). The one place to tune them.

``K`` are rough diffuse attenuation coefficients (1/m) per channel (R, G, B),
loosely following Jerlov water types: "blue" ≈ clear oceanic, "coastal" ≈
coastal type 1–3, "green" ≈ lake / turbid coastal. They are starting values
and **must be tuned visually** with real footage (see docs/DECISIONS.md).
``depth_m`` is used as the effective light path length (surface → subject → camera).
"""

from __future__ import annotations

K: dict[str, tuple[float, float, float]] = {
    "blue": (0.45, 0.07, 0.03),
    "coastal": (0.50, 0.12, 0.15),
    "green": (0.60, 0.20, 0.35),
}

GAIN_SOFT_LIMIT = 8.0            # per-channel gain cap (limits noise amplification)
ARTIFICIAL_LIGHT_DEPTH_FACTOR = 0.5  # with a lamp, the effective depth is reduced
RED_COMP_STOPS = 1.0             # red_comp ±100 → red gain ×2^±1
