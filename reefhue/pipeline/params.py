"""Parameter registry (SPEC §7): single source for defaults, ranges, steps and label keys.

Milestone 2 uses it for sidecar defaults and status derivation; slider
stepping/clamping helpers are added in milestone 3. No Qt here.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

PIPELINE_VERSION = 1
AUTO_ALGO_VERSION = 1
LEVEL_CHANNELS = ("master", "r", "g", "b")
CURVE_CHANNELS = ("master", "r", "g", "b")


@dataclass(frozen=True)
class Param:
    group: str
    key: str
    kind: str  # "float" | "int" | "bool" | "choice"
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str = ""
    choices: tuple[str, ...] = ()

    @property
    def label_key(self) -> str:
        return f"param.{self.group}.{self.key}"

    @property
    def tip_key(self) -> str:
        return f"param.{self.group}.{self.key}.tip"


def _bipolar(group: str, key: str) -> Param:
    return Param(group, key, "int", 0, -100, 100, 1)


PARAMS: tuple[Param, ...] = (
    Param("underwater", "auto_strength", "int", 100, 0, 100, 1, "%"),
    Param("underwater", "depth_m", "float", 0.0, 0, 40, 0.5, "m"),
    Param("underwater", "water_type", "choice", "coastal", choices=("blue", "coastal", "green")),
    _bipolar("underwater", "red_comp"),
    Param("underwater", "artificial_light", "bool", False),
    _bipolar("white_balance", "temperature"),
    _bipolar("white_balance", "tint"),
    Param("light", "exposure_ev", "float", 0.0, -5, 5, 0.05, "EV"),
    *(_bipolar("light", k) for k in ("contrast", "highlights", "shadows", "whites", "blacks")),
    _bipolar("color", "vibrance"),
    _bipolar("color", "saturation"),
    Param("levels", "black", "float", 0.0, 0, 0.95, 0.005),
    Param("levels", "white", "float", 1.0, 0.05, 1, 0.005),
    Param("levels", "gamma", "float", 1.0, 0.2, 5, 0.01),
    _bipolar("details", "dehaze"),
    _bipolar("details", "clarity"),
    Param("details", "sharpen_amount", "int", 0, 0, 100, 1),
    Param("details", "sharpen_radius", "float", 1.0, 0.5, 3.0, 0.1, "px"),
    Param("details", "denoise", "int", 0, 0, 100, 1),
    Param("geometry", "distortion", "int", 0, -100, 100, 1),
    Param("geometry", "vignette_corr", "int", 0, 0, 100, 1),
    Param("geometry", "straighten_deg", "float", 0.0, -45, 45, 0.1, "°"),
    Param("geometry", "rotate_90", "int", 0, 0, 3, 1),
    Param("geometry", "flip_h", "bool", False),
    Param("geometry", "flip_v", "bool", False),
    Param("lut", "intensity", "int", 100, 0, 100, 1, "%"),
    Param("lut", "position", "choice", "end", choices=("start", "end")),
)

PARAMS_BY_KEY: dict[tuple[str, str], Param] = {(p.group, p.key): p for p in PARAMS}


def _group_defaults(group: str) -> dict[str, Any]:
    return {p.key: p.default for p in PARAMS if p.group == group}


def default_auto_result() -> dict[str, Any]:
    return {
        "analyzed_at": None,
        "source": None,
        "gains_rgb": [1, 1, 1],
        "levels": None,
        "algo_version": AUTO_ALGO_VERSION,
        "depth_estimated": False,
    }


def default_edit() -> dict[str, Any]:
    """A complete, neutral ``edit`` section (SPEC §13)."""
    underwater = _group_defaults("underwater")
    underwater["auto_result"] = default_auto_result()
    geometry = _group_defaults("geometry")
    geometry["crop"] = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "aspect": "free"}
    lut = {"file": None, "sha256": None, **_group_defaults("lut")}
    return copy.deepcopy(
        {
            "pipeline_version": PIPELINE_VERSION,
            "underwater": underwater,
            "white_balance": _group_defaults("white_balance"),
            "light": _group_defaults("light"),
            "color": _group_defaults("color"),
            "levels": {ch: _group_defaults("levels") for ch in LEVEL_CHANNELS},
            "curves": {ch: {"enabled": True, "points": [[0, 0], [1, 1]]} for ch in CURVE_CHANNELS},
            "details": _group_defaults("details"),
            "geometry": geometry,
            "lut": lut,
        }
    )


# --------------------------------------------------------------------------- stepping / clamping
FINE_FACTOR = 0.1     # Alt/Option multiplier; also the stored resolution of every numeric parameter
SHIFT_FACTOR = 10.0
ZERO_SNAP_FRACTION = 0.015  # bipolar sliders snap to 0 within 1.5 % of the range while dragging
LEVELS_MIN_GAP = 0.02


def resolution(param: Param) -> float:
    """Smallest representable change (step × 0.1 so that Alt-stepping lands on the grid)."""
    return float(param.step) * FINE_FACTOR


def decimals(value: float) -> int:
    text = f"{value:.10f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def quantize(param: Param, value: float) -> float:
    """Round to the parameter's grid (anchored at 0) and clamp to [min, max] – no float drift."""
    res = resolution(param)
    value = round(round(float(value) / res) * res, decimals(res) + 1)
    value = min(float(param.maximum), max(float(param.minimum), value))
    value = round(value, decimals(res) + 1)
    return 0.0 if value == 0 else value  # never "-0.0"


def step_value(param: Param, value: float, direction: int, modifier: str = "normal") -> float:
    """One keyboard step (+/−, arrows). ``modifier``: "normal", "shift" (10×) or "alt" (0.1×)."""
    factor = {"normal": 1.0, "shift": SHIFT_FACTOR, "alt": FINE_FACTOR}[modifier]
    return quantize(param, float(value) + direction * float(param.step) * factor)


def is_bipolar(param: Param) -> bool:
    return param.minimum is not None and param.minimum < 0 < param.maximum


def snap_zero(param: Param, value: float) -> float:
    if is_bipolar(param) and abs(value) <= (param.maximum - param.minimum) * ZERO_SNAP_FRACTION:
        return 0.0
    return value


def couple_levels(black: float, white: float, changed: str) -> tuple[float, float]:
    """Keep ``white - black >= 0.02``; the handle that was moved is limited by the other."""
    black_p, white_p = PARAMS_BY_KEY[("levels", "black")], PARAMS_BY_KEY[("levels", "white")]
    black, white = quantize(black_p, black), quantize(white_p, white)
    if white - black < LEVELS_MIN_GAP:
        if changed == "black":
            black = quantize(black_p, white - LEVELS_MIN_GAP)
            if white - black < LEVELS_MIN_GAP - 1e-9:  # grid rounding went the wrong way
                black = quantize(black_p, black - resolution(black_p))
        else:
            white = quantize(white_p, black + LEVELS_MIN_GAP)
            if white - black < LEVELS_MIN_GAP - 1e-9:
                white = quantize(white_p, white + resolution(white_p))
    return black, white


# --------------------------------------------------------------------------- edit paths
def param_for_path(path: tuple) -> Param | None:
    """``("light", "contrast")`` or ``("levels", "r", "black")`` → registry entry."""
    if len(path) == 3 and path[0] == "levels":
        return PARAMS_BY_KEY.get(("levels", path[2]))
    if len(path) == 2:
        return PARAMS_BY_KEY.get((path[0], path[1]))
    return None


def get_path(edit: dict, path: tuple) -> Any:
    node: Any = edit
    for key in path:
        node = node[key]
    return node


def set_path(edit: dict, path: tuple, value: Any) -> dict:
    """Return a deep copy of ``edit`` with ``path`` set to ``value``."""
    result = copy.deepcopy(edit)
    node = result
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return result


def coerce_value(param: Param, value: Any) -> Any:
    if param.kind == "bool":
        return bool(value)
    if param.kind == "choice":
        return value if value in param.choices else param.default
    if param.key == "rotate_90":
        return int(value) % 4
    return quantize(param, value)
