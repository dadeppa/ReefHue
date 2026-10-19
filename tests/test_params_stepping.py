"""Parameter registry: limits, stepping around zero/min/max, coupling rules (SPEC §7, §18)."""

from __future__ import annotations

import pytest

from reefhue.pipeline import params as P
from reefhue.pipeline.params import PARAMS, PARAMS_BY_KEY, quantize, step_value


def p(group, key):
    return PARAMS_BY_KEY[(group, key)]


def test_registry_matches_spec_table():
    expected = {
        ("underwater", "auto_strength"): (0, 100, 100, 1),
        ("underwater", "depth_m"): (0, 40, 0, 0.5),
        ("underwater", "red_comp"): (-100, 100, 0, 1),
        ("white_balance", "temperature"): (-100, 100, 0, 1),
        ("light", "exposure_ev"): (-5, 5, 0, 0.05),
        ("light", "blacks"): (-100, 100, 0, 1),
        ("color", "saturation"): (-100, 100, 0, 1),
        ("levels", "black"): (0, 0.95, 0, 0.005),
        ("levels", "white"): (0.05, 1, 1, 0.005),
        ("levels", "gamma"): (0.2, 5, 1.0, 0.01),
        ("details", "sharpen_radius"): (0.5, 3.0, 1.0, 0.1),
        ("details", "denoise"): (0, 100, 0, 1),
        ("geometry", "straighten_deg"): (-45, 45, 0, 0.1),
        ("geometry", "distortion"): (-100, 100, 0, 1),
        ("geometry", "vignette_corr"): (0, 100, 0, 1),
        ("lut", "intensity"): (0, 100, 100, 1),
    }
    for key, (lo, hi, default, step) in expected.items():
        param = PARAMS_BY_KEY[key]
        assert (param.minimum, param.maximum, param.default, param.step) == (lo, hi, default, step), key
    assert p("underwater", "water_type").choices == ("blue", "coastal", "green")
    assert p("lut", "position").choices == ("start", "end")
    for param in PARAMS:
        assert param.label_key == f"param.{param.group}.{param.key}"


def test_stepping_through_zero_has_no_drift():
    ev = p("light", "exposure_ev")
    value = 0.15
    for _ in range(3):
        value = step_value(ev, value, -1)
    assert value == 0.0 and str(value) == "0.0"  # exactly zero, never -0.0 or 1e-17
    for _ in range(7):
        value = step_value(ev, value, -1)
    assert value == -0.35
    for _ in range(1000):  # long walk stays on the grid
        value = step_value(ev, value, 1, "alt")
    assert value == 4.65


@pytest.mark.parametrize(
    "group, key, start, direction, modifier, expected",
    [
        ("light", "contrast", 95, 1, "shift", 100.0),       # stops at max, no overshoot
        ("light", "contrast", -95, -1, "shift", -100.0),    # stops at min
        ("light", "contrast", 100, 1, "normal", 100.0),
        ("light", "contrast", 0, 1, "alt", 0.1),
        ("light", "contrast", -0.1, 1, "alt", 0.0),
        ("light", "exposure_ev", 4.99, 1, "normal", 5.0),
        ("underwater", "depth_m", 0, -1, "normal", 0.0),
        ("underwater", "depth_m", 39.8, 1, "normal", 40.0),
        ("levels", "gamma", 1.0, -1, "shift", 0.9),
        ("levels", "black", 0.0, 1, "normal", 0.005),
        ("details", "sharpen_radius", 0.5, -1, "normal", 0.5),
        ("geometry", "straighten_deg", -0.05, 1, "alt", -0.04),
    ],
)
def test_step_value(group, key, start, direction, modifier, expected):
    assert step_value(p(group, key), start, direction, modifier) == expected


@pytest.mark.parametrize(
    "group, key, typed, expected",
    [
        ("light", "contrast", 250, 100.0),
        ("light", "contrast", -1e9, -100.0),
        ("light", "exposure_ev", 0.30000000000000004, 0.3),
        ("light", "exposure_ev", 1.23456, 1.235),
        ("levels", "white", 0.0, 0.05),
        ("levels", "gamma", 0.0, 0.2),
        ("light", "contrast", -0.00001, 0.0),
    ],
)
def test_typed_values_are_clamped_and_rounded(group, key, typed, expected):
    assert quantize(p(group, key), typed) == expected


def test_zero_snap_only_for_bipolar():
    assert P.snap_zero(p("light", "contrast"), 2.5) == 0.0
    assert P.snap_zero(p("light", "contrast"), 4.0) == 4.0
    assert P.snap_zero(p("details", "denoise"), 1.0) == 1.0


@pytest.mark.parametrize(
    "black, white, changed, expected",
    [
        (0.5, 0.51, "black", (0.49, 0.51)),
        (0.5, 0.51, "white", (0.5, 0.52)),
        (0.95, 1.0, "black", (0.95, 1.0)),
        (0.0, 0.05, "white", (0.0, 0.05)),
        (0.2, 0.9, "black", (0.2, 0.9)),
    ],
)
def test_levels_coupling(black, white, changed, expected):
    b, w = P.couple_levels(black, white, changed)
    assert (b, w) == expected
    assert w - b >= P.LEVELS_MIN_GAP - 1e-9


def test_paths_and_coercion():
    edit = P.default_edit()
    new = P.set_path(edit, ("levels", "r", "gamma"), 1.5)
    assert P.get_path(new, ("levels", "r", "gamma")) == 1.5
    assert P.get_path(edit, ("levels", "r", "gamma")) == 1.0  # original untouched
    assert P.param_for_path(("levels", "g", "black")).key == "black"
    assert P.coerce_value(p("geometry", "rotate_90"), 5) == 1
    assert P.coerce_value(p("underwater", "water_type"), "purple") == "coastal"
    assert P.coerce_value(p("geometry", "flip_h"), 1) is True
