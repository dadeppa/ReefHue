"""The one processing pipeline shared by preview, export and analysis (SPEC §8). No Qt.

Stages are declared in :data:`STAGES` in their fixed order. They are split in
two parts so callers can cache the expensive, rarely changing first part:

* ``geometry`` – input LUT, linearisation, lens + geometry (+ crop)
* ``color``    – denoise … output LUT

Preview and export call exactly the same functions; only the resolution
(``ctx.scale``) and the region differ.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

import numpy as np

from reefhue.pipeline import geometry as geo
from reefhue.pipeline import parallel
from reefhue.pipeline.colorspace import linear_to_srgb, srgb_to_linear
from reefhue.pipeline.context import RenderContext
from reefhue.pipeline.stages import color, curves, dehaze, denoise, details, levels, lut, tone, underwater
from reefhue.pipeline.stages import white_balance

StageFn = Callable[[np.ndarray, dict, RenderContext], np.ndarray]


@dataclass(frozen=True)
class Stage:
    name: str
    fn: StageFn
    part: str  # "geometry" | "color"
    pointwise: bool = False  # result per pixel depends only on that pixel → run in parallel strips

    def run(self, img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
        if self.pointwise:
            return parallel.map_rows(lambda strip: self.fn(strip, edit, ctx), img)
        return self.fn(img, edit, ctx)


def _to_linear(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    return srgb_to_linear(img)


def _geometry(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    return geo.apply_geometry(img, edit["geometry"], ctx.scale, crop=ctx.apply_crop)


def _to_display(img: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    return linear_to_srgb(img)


STAGES: tuple[Stage, ...] = (
    Stage("input_lut", lut.input_lut, "geometry"),                                        # 1  (only if lut.position == "start")
    Stage("to_linear", _to_linear, "geometry", pointwise=True),                           # 1
    Stage("geometry", _geometry, "geometry"),                                             # 2
    Stage("denoise", denoise.denoise, "color"),                                           # 3
    Stage("underwater", underwater.underwater, "color"),                                  # 4
    Stage("white_balance_exposure", white_balance.white_balance_exposure, "color"),       # 5
    Stage("dehaze", dehaze.dehaze, "color"),                                              # 6
    Stage("to_display", _to_display, "color", pointwise=True),                            # 7
    Stage("levels", levels.levels, "color", pointwise=True),                              # 8
    Stage("tone", tone.tone, "color", pointwise=True),                                    # 9
    Stage("color", color.color, "color", pointwise=True),                                 # 10
    Stage("curves", curves.curves, "color", pointwise=True),                              # 11
    Stage("clarity", details.clarity, "color"),                                           # 12
    Stage("sharpen", details.sharpen, "color"),                                           # 12 (last detail step)
    Stage("output_lut", lut.output_lut, "color"),                                         # 13
)
STAGE_NAMES = tuple(s.name for s in STAGES)


def run_stages(img: np.ndarray, edit: dict, ctx: RenderContext, part: str | None = None,
               until: str | None = None) -> np.ndarray:
    """Run all stages (or one part) in order; ``until`` stops *before* the named stage.

    8-bit input (``uint8``, 0–255) is accepted: it is linearised through an exact
    lookup table; an input LUT (which needs floats) converts it first.
    """
    if img.dtype == np.uint8:
        lut_first = (edit.get("lut") or {}).get("position") == "start" and (edit.get("lut") or {}).get("file")
        out = img.astype(np.float32) / np.float32(255.0) if lut_first else np.ascontiguousarray(img)
    else:
        out = np.ascontiguousarray(img, dtype=np.float32)
    for stage in STAGES:
        if stage.name == until:
            break
        if part is None or stage.part == part:
            out = stage.run(out, edit, ctx)
    return out


def process_geometry(src_srgb: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    """Decoded sRGB float image → linear, geometry applied."""
    return run_stages(src_srgb, edit, ctx, part="geometry")


def process_color(geom_linear: np.ndarray, edit: dict, ctx: RenderContext, cache: dict | None = None) -> np.ndarray:
    """Linear geometry output → display-space float image (not yet clipped).

    ``cache`` (optional, owned by the caller, e.g. the preview engine) keeps the
    result of the expensive linear-light stages; it is reused while only later
    stages (levels, tone, colour, curves, details) change.
    """
    if cache is None:
        return run_stages(geom_linear, edit, ctx, part="color")
    key = (id(geom_linear), linear_part_key(edit), ctx.scale)
    display = cache.get(key)
    if display is None:
        display = run_stages(geom_linear, edit, ctx, part="color", until=LINEAR_PART_END)
        display = _to_display(display, edit, ctx)
        cache.clear()
        cache[key] = display
    out = display
    started = False
    for stage in STAGES:
        if started:
            out = stage.run(out, edit, ctx)
        started = started or stage.name == LINEAR_PART_END
    return out


LINEAR_PART_END = "to_display"


def linear_part_key(edit: dict) -> str:
    """Everything the stages up to and including ``to_display`` depend on."""
    d = edit["details"]
    relevant = {
        "underwater": edit["underwater"],
        "white_balance": edit["white_balance"],
        "exposure": edit["light"].get("exposure_ev"),
        "denoise": d.get("denoise"),
        "dehaze": d.get("dehaze"),
    }
    return json.dumps(relevant, sort_keys=True, default=str)


def process(src_srgb: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    return process_color(process_geometry(src_srgb, edit, ctx), edit, ctx)


def render_original(geom_linear: np.ndarray) -> np.ndarray:
    """The "original" view: same framing as the edit, no colour/tone/detail changes."""
    return linear_to_srgb(geom_linear)


def pre_white_balance(geom_linear: np.ndarray, edit: dict, ctx: RenderContext) -> np.ndarray:
    """Image as it enters the white-balance stage (for the eyedropper and auto WB)."""
    out = geom_linear
    for stage in STAGES:
        if stage.part != "color":
            continue
        if stage.name == "white_balance_exposure":
            break
        out = stage.run(out, edit, ctx)
    return out


# --------------------------------------------------------------------------- context + output
def make_context(full_src_size: tuple[int, int], proc_src_size: tuple[int, int], edit: dict,
                 apply_crop: bool = True, stats: dict | None = None, lut_provider=None) -> RenderContext:
    """Context for processing a source of ``proc_src_size`` that is ``full_src_size`` at full resolution."""
    full_w, full_h = full_src_size
    scale = proc_src_size[0] / full_w if full_w else 1.0
    out_w, out_h = geo.output_size(full_w, full_h, edit["geometry"], apply_crop=True)
    if lut_provider is None:
        from reefhue.pipeline import lut as lut_library  # noqa: PLC0415 – avoids an import cycle

        lut_provider = lut_library.default_provider
    return RenderContext(scale=scale, ref_short=max(1, min(out_w, out_h)), stats=dict(stats or {}),
                         lut_provider=lut_provider, apply_crop=apply_crop)


def global_stats(geom_linear: np.ndarray, edit: dict) -> dict:
    """Whole-frame statistics that region renders must reuse (computed on the preview)."""
    stats: dict = {}
    if float(edit["details"].get("dehaze", 0) or 0):
        pre = pre_white_balance(geom_linear, edit, RenderContext(scale=0.1))  # tiny radii: cheap
        exposure = white_balance.white_balance_exposure(pre, edit, RenderContext())
        stats["airlight"] = dehaze.estimate_airlight(exposure)
    return stats


def finalize(img: np.ndarray) -> np.ndarray:
    """Step 14 (first part): clip to the displayable range."""
    return np.clip(img, 0.0, 1.0).astype(np.float32)


def to_uint8(img: np.ndarray, dither: bool = False, seed: int = 0) -> np.ndarray:
    """Quantise to 8 bit; optional deterministic triangular dither (export)."""
    x = finalize(img) * np.float32(255.0)
    if dither:
        rng = np.random.default_rng(seed)
        x = x + (rng.random(x.shape, dtype=np.float32) - rng.random(x.shape, dtype=np.float32))
    return np.clip(np.rint(x), 0, 255).astype(np.uint8)


def to_uint16(img: np.ndarray, bits: int = 16) -> np.ndarray:
    peak = (1 << bits) - 1
    return np.clip(np.rint(finalize(img) * peak), 0, peak).astype(np.uint16)
