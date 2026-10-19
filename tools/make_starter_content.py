"""Regenerate ReefHue's bundled starter presets and LUTs (run from the app folder):

    .venv/bin/python tools/make_starter_content.py

The LUTs are ReefHue's own creations (no third-party licence); they are built
from simple colour math so they can be reproduced exactly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reefhue.pipeline import lut  # noqa: E402
from reefhue.pipeline.colorspace import LUMA  # noqa: E402
from reefhue.pipeline.params import default_edit  # noqa: E402

PRESET_SECTIONS = ("underwater", "white_balance", "light", "color", "details")


def preset(name_key: str, changes: dict) -> dict:
    edit = default_edit()
    for section, values in changes.items():
        edit[section].update(values)
    return {"name_key": name_key, "edit": {s: edit[s] for s in PRESET_SECTIONS}}


PRESETS = {
    "shallow_5m": preset("preset.builtin.shallow_5m", {
        "underwater": {"depth_m": 5.0, "water_type": "coastal"},
        "light": {"contrast": 10}, "color": {"vibrance": 10}}),
    "blue_water_20m": preset("preset.builtin.blue_water_20m", {
        "underwater": {"depth_m": 20.0, "water_type": "blue", "red_comp": 15},
        "light": {"contrast": 15, "shadows": 10}, "color": {"vibrance": 15}, "details": {"dehaze": 15}}),
    "green_water_lake": preset("preset.builtin.green_water_lake", {
        "underwater": {"depth_m": 6.0, "water_type": "green"},
        "light": {"contrast": 10}, "color": {"vibrance": 10}, "details": {"dehaze": 20}}),
    "with_lamp": preset("preset.builtin.with_lamp", {
        "underwater": {"depth_m": 4.0, "water_type": "coastal", "artificial_light": True},
        "light": {"contrast": 5}, "color": {"vibrance": 5}}),
}


def smooth_s(x: np.ndarray, amount: float) -> np.ndarray:
    return x + amount * (x - 0.5) * (1 - np.abs(2 * x - 1)) * 2 * (x * (1 - x)) * 4


def saturate(rgb: np.ndarray, factor: float) -> np.ndarray:
    y = (rgb @ LUMA)[..., None]
    return y + (rgb - y) * factor


def looks(n: int = 33) -> dict[str, np.ndarray]:
    ident = lut.identity_table(n)
    warm = np.clip(saturate(ident * np.array([1.06, 1.0, 0.92], np.float32), 1.12), 0, 1)
    clear = np.clip(saturate(smooth_s(ident * np.array([0.97, 1.0, 1.04], np.float32), 0.25), 1.08), 0, 1)
    fade_base = 0.06 + ident * 0.9
    fade = np.clip(saturate(fade_base, 0.85), 0, 1)
    punchy = np.clip(saturate(smooth_s(ident, 0.5), 1.25), 0, 1)
    return {"ReefHue Warm Reef": warm, "ReefHue Clear Blue": clear, "ReefHue Film Fade": fade,
            "ReefHue Punchy": punchy}


def main() -> None:
    preset_dir = ROOT / "reefhue" / "resources" / "presets"
    preset_dir.mkdir(parents=True, exist_ok=True)
    for key, data in PRESETS.items():
        (preset_dir / f"{key}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    lut_dir = ROOT / "luts"
    lut_dir.mkdir(exist_ok=True)
    for title, table in looks().items():
        lut.write_cube(lut_dir / f"{title}.cube", table.astype(np.float32), title)
    print("wrote", len(PRESETS), "presets and", len(looks()), "LUTs")


if __name__ == "__main__":
    main()
