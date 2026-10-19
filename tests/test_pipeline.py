"""Pipeline: stage order, neutrality, stage effects, geometry, preview ≈ export, headless use."""

from __future__ import annotations

import json
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image

from reefhue.pipeline import auto_basic, geometry as geo, pipeline as pl
from reefhue.pipeline.analysis import clipping_masks, histogram
from reefhue.pipeline.colorspace import linear_to_srgb, srgb_to_linear
from reefhue.pipeline.params import default_edit
from reefhue.pipeline.stages.underwater import channel_gains, depth_prior
from reefhue.pipeline.stages.white_balance import temperature_tint_for_neutral, wb_gains
from tests.media import gradient


def image(w=96, h=64, seed=0):
    rng = np.random.default_rng(seed)
    base = np.asarray(gradient(w, h, cast=(0.35, 0.8, 1.0)), dtype=np.float32) / 255.0
    return np.clip(base * 0.9 + rng.random((h, w, 3), dtype=np.float32) * 0.1, 0, 1)


def render(img, edit, full=None):
    full = full or (img.shape[1], img.shape[0])
    ctx = pl.make_context(full, (img.shape[1], img.shape[0]), edit)
    return pl.process(img, edit, ctx)


def test_stage_order_matches_spec():
    assert pl.STAGE_NAMES == (
        "input_lut", "to_linear", "geometry", "denoise", "underwater", "white_balance_exposure", "dehaze",
        "to_display", "levels", "tone", "color", "curves", "clarity", "sharpen", "output_lut",
    )
    parts = [s.part for s in pl.STAGES]
    assert parts == sorted(parts, key=lambda p: p != "geometry")  # geometry part first


def test_neutral_edit_is_identity():
    img = image()
    out = render(img, default_edit())
    assert out.dtype == np.float32
    assert np.abs(out - img).max() < 1e-5


def test_srgb_round_trip():
    x = np.linspace(0, 1, 1001, dtype=np.float32)
    assert np.abs(linear_to_srgb(srgb_to_linear(x)) - x).max() < 1e-5


def _mean(img):
    return img.reshape(-1, 3).mean(axis=0)


@pytest.mark.parametrize(
    "path, value, check",
    [
        (("light", "exposure_ev"), 1.0, lambda a, b: _mean(b).sum() > _mean(a).sum()),
        (("light", "exposure_ev"), -1.0, lambda a, b: _mean(b).sum() < _mean(a).sum()),
        (("white_balance", "temperature"), 50, lambda a, b: (b[..., 0] - b[..., 2]).mean() > (a[..., 0] - a[..., 2]).mean()),
        (("white_balance", "tint"), 50, lambda a, b: _mean(b)[1] < _mean(a)[1]),
        (("color", "saturation"), -100, lambda a, b: np.abs(b - b.mean(axis=2, keepdims=True)).max() < 0.02),
        (("color", "vibrance"), 60, lambda a, b: np.std(b, axis=2).mean() > np.std(a, axis=2).mean()),
        (("light", "contrast"), 60, lambda a, b: np.std(b) > np.std(a)),
        (("light", "contrast"), -60, lambda a, b: np.std(b) < np.std(a)),
        (("light", "shadows"), 80, lambda a, b: b[a.mean(axis=2) < 0.3].mean() > a[a.mean(axis=2) < 0.3].mean()),
        (("light", "blacks"), 100, lambda a, b: b.min() > a.min()),
        (("underwater", "red_comp"), 100, lambda a, b: _mean(b)[0] > _mean(a)[0] * 1.2),
        (("underwater", "depth_m"), 10, lambda a, b: _mean(b)[0] / _mean(b)[2] > _mean(a)[0] / _mean(a)[2]),
        (("details", "clarity"), 80, lambda a, b: not np.allclose(a, b)),
        (("details", "sharpen_amount"), 100, lambda a, b: np.abs(np.diff(b, axis=1)).mean() > np.abs(np.diff(a, axis=1)).mean()),
        (("details", "denoise"), 100, lambda a, b: np.abs(np.diff(b, axis=1)).mean() < np.abs(np.diff(a, axis=1)).mean()),
    ],
)
def test_stage_effects(path, value, check):
    img = image()
    edit = default_edit()
    edit[path[0]][path[1]] = value
    assert check(render(img, default_edit()), render(img, edit))


def test_dehaze_removes_synthetic_haze():
    rng = np.random.default_rng(5)
    blocks = rng.random((8, 12, 3), dtype=np.float32)
    clean_lin = np.kron(blocks, np.ones((16, 16, 1), np.float32)) * 0.8 + 0.02  # contrasty scene
    airlight = np.array([0.55, 0.75, 0.8], np.float32)
    hazy_lin = clean_lin * 0.5 + airlight * 0.5  # transmission 0.5
    hazy = linear_to_srgb(hazy_lin)
    edit = default_edit()
    edit["details"]["dehaze"] = 80
    out = render(hazy, edit)
    clean = linear_to_srgb(clean_lin)
    assert out.mean(axis=2).std() > hazy.mean(axis=2).std() * 1.2
    assert np.abs(out - clean).mean() < np.abs(hazy - clean).mean()
    edit["details"]["dehaze"] = -60  # negative adds haze
    assert render(hazy, edit).mean(axis=2).std() < hazy.mean(axis=2).std()


def test_levels_and_curves():
    img = image()
    edit = default_edit()
    edit["levels"]["master"].update(black=0.1, white=0.9)
    out = render(img, edit)
    assert out.min() >= 0 and out.max() > img.max()
    edit = default_edit()
    edit["curves"]["r"]["points"] = [[0, 0], [0.5, 0.8], [1, 1]]
    out = render(img, edit)
    assert out[..., 0].mean() > img[..., 0].mean() and np.allclose(out[..., 1], np.clip(img[..., 1], 0, 1), atol=1e-3)
    edit["curves"]["r"]["enabled"] = False
    assert np.abs(render(img, edit) - img).max() < 1e-5


def test_underwater_prior_and_gains():
    assert depth_prior(0, "blue", False) == (1.0, 1.0, 1.0)
    blue = depth_prior(10, "blue", False)
    assert blue[0] > blue[1] > 1.0 and blue[2] == 1.0
    assert max(depth_prior(40, "blue", False)) <= 8.0  # soft limit
    assert depth_prior(10, "blue", True)[0] < blue[0]  # artificial light corrects less
    uw = default_edit()["underwater"]
    uw["auto_result"]["gains_rgb"] = [2.0, 1.0, 0.5]
    uw["auto_strength"] = 50
    assert np.allclose(channel_gains(uw), [2 ** 0.5, 1.0, 0.5 ** 0.5])


def test_white_balance_inverse():
    for t, m in [(30, 0), (-45, 20), (80, -60)]:
        g = wb_gains(t, m)
        sample = 1.0 / g  # a colour that these gains make neutral
        assert temperature_tint_for_neutral(sample) == (t, m)


def test_eyedropper_and_auto_wb_neutralise_cast():
    img = np.ones((40, 40, 3), np.float32) * np.array([0.1, 0.25, 0.3], np.float32)
    t, m = auto_basic.eyedropper_white_balance(img, 20, 20, 11)
    out = img * wb_gains(t, m)
    assert np.ptp(out[20, 20]) / out[20, 20].mean() < 0.05
    assert auto_basic.auto_white_balance(img) == (t, m)


def test_auto_light_brightens_dark_image():
    img = image() * 0.15
    edit = default_edit()
    ctx = pl.make_context((96, 64), (96, 64), edit)
    geom = pl.process_geometry(img, edit, ctx)
    values = auto_basic.auto_light(geom, edit, ctx)
    assert values["exposure_ev"] > 0.5


def test_geometry_sizes_and_order():
    assert geo.straightened_size(400, 300, 0) == (400, 300)
    w, h = geo.straightened_size(400, 300, 10)
    assert w < 400 and h < 300 and abs(w / h - 4 / 3) < 0.02
    g = default_edit()["geometry"]
    g.update(rotate_90=1, crop={"x": 0.25, "y": 0.0, "w": 0.5, "h": 1.0, "aspect": "free"})
    assert geo.output_size(400, 300, g) == (150, 400)
    img = np.zeros((300, 400, 3), np.float32)
    img[:10, :10] = 1.0  # mark top-left corner
    out = geo.apply_geometry(img, {**g, "crop": {"x": 0, "y": 0, "w": 1, "h": 1}})
    assert out.shape == (400, 300, 3) and out[:10, -10:].mean() == 1.0  # rotated clockwise
    flipped = geo.apply_geometry(img, {**default_edit()["geometry"], "flip_h": True})
    assert flipped[:10, -10:].mean() == 1.0


def test_straighten_has_no_empty_corners():
    img = np.ones((300, 400, 3), np.float32)
    out = geo.apply_straighten(img, 12.0)
    assert out.min() > 0.99


def test_crop_minimum_and_helpers():
    assert geo.crop_pixels({"x": 0.5, "y": 0.5, "w": 0.0, "h": 0.0}, 1000, 800)[2:] == (32, 32)
    c = geo.clamp_crop({"x": 0.9, "y": -0.2, "w": 0.5, "h": 0.001}, 1000, 1000)
    assert c["x"] == 0.5 and c["y"] == 0.0 and c["h"] == 0.032
    fitted = geo.fit_aspect({"x": 0, "y": 0, "w": 1, "h": 1}, 1.0, 1600, 900)
    assert abs(fitted["w"] * 1600 - fitted["h"] * 900) < 1 and fitted["h"] == 1.0
    dragged = geo.drag_crop({"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}, "se", 0.2, 0.0, 1.0, 1000, 1000)
    assert abs(dragged["w"] - dragged["h"]) < 1e-6 and dragged["w"] == 0.7
    moved = geo.drag_crop({"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}, "move", 0.9, 0.0, None, 1000, 1000)
    assert moved["x"] == 0.5
    rot = geo.transform_crop_for_rotation({"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}, 1)
    assert rot == {"x": 0.4, "y": 0.1, "w": 0.4, "h": 0.3}
    assert geo.transform_crop_for_rotation(rot, 3) == pytest.approx({"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4})


def test_preview_matches_export_downscaled():
    """Same pipeline at 1/4 scale ≈ full-res render downscaled (radii scale with the preview)."""
    import cv2

    full = np.asarray(gradient(800, 600), dtype=np.float32) / 255.0
    full = cv2.GaussianBlur(full + np.random.default_rng(1).random(full.shape, dtype=np.float32) * 0.05, (0, 0), 2)
    edit = default_edit()
    edit["light"].update(exposure_ev=0.4, contrast=25, shadows=30)
    edit["details"].update(clarity=30, dehaze=25, sharpen_amount=40)
    edit["underwater"]["depth_m"] = 8
    edit["geometry"].update(straighten_deg=4.0, crop={"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8, "aspect": "free"})
    export = pl.finalize(render(full, edit))
    small = cv2.resize(full, (200, 150), interpolation=cv2.INTER_AREA)
    preview = pl.finalize(render(small, edit, full=(800, 600)))
    export_small = cv2.resize(export, (preview.shape[1], preview.shape[0]), interpolation=cv2.INTER_AREA)
    assert abs(preview.shape[1] * 4 - export.shape[1]) <= 4
    assert np.abs(preview - export_small).mean() < 0.02


def test_region_render_matches_full_render():
    """100 % view: rendering a region (with margin + global stats) equals the same area of a full render."""
    full = image(400, 300, seed=3)
    edit = default_edit()
    edit["details"].update(dehaze=40, clarity=30, sharpen_amount=50)
    edit["light"]["contrast"] = 20
    ctx = pl.make_context((400, 300), (400, 300), edit)
    geom = pl.process_geometry(full, edit, ctx)
    stats = pl.global_stats(geom, edit)
    whole = pl.process_color(geom, edit, pl.make_context((400, 300), (400, 300), edit, stats=stats))
    margin = 40
    x0, y0, x1, y1 = 150, 100, 250, 180
    region = geom[y0 - margin:y1 + margin, x0 - margin:x1 + margin]
    part = pl.process_color(region, edit, pl.make_context((400, 300), (400, 300), edit, stats=stats))
    part = part[margin:-margin, margin:-margin]
    assert np.abs(part - whole[y0:y1, x0:x1]).max() < 0.01


def test_prefix_cache_gives_same_result():
    img = image()
    edit = default_edit()
    edit["details"]["dehaze"] = 30
    edit["light"]["contrast"] = 20
    ctx = pl.make_context((96, 64), (96, 64), edit)
    geom = pl.process_geometry(img, edit, ctx)
    cache: dict = {}
    a = pl.process_color(geom, edit, ctx, cache)
    edit["light"]["contrast"] = 40
    b = pl.process_color(geom, edit, ctx, cache)  # reuses the cached linear part
    assert len(cache) == 1
    assert np.allclose(b, pl.process_color(geom, edit, pl.make_context((96, 64), (96, 64), edit)), atol=1e-6)
    assert not np.allclose(a, b)


def test_histogram_and_clipping():
    img = np.zeros((10, 10, 3), np.uint8)
    img[:5] = 255
    hist = histogram(img)
    assert set(hist) == {"r", "g", "b", "l"} and hist["r"].shape == (256,)
    blown, crushed = clipping_masks(img)
    assert blown.sum() == 50 and crushed.sum() == 50


def test_quantize_with_dither_is_deterministic():
    img = np.full((8, 8, 3), 0.5, np.float32)
    a, b = pl.to_uint8(img, dither=True), pl.to_uint8(img, dither=True)
    assert np.array_equal(a, b) and abs(float(a.mean()) - 127.5) < 1.0


def test_cli_processes_photo_headless(tmp_path):
    src = tmp_path / "in.jpg"
    gradient(64, 48).save(src, quality=95)
    edit = default_edit()
    edit["light"]["exposure_ev"] = 1.0
    (tmp_path / "edit.json").write_text(json.dumps({"edit": edit}))
    out = tmp_path / "out.jpg"
    result = subprocess.run([sys.executable, "-m", "reefhue.pipeline.cli", str(src), str(out),
                             "--edit", str(tmp_path / "edit.json")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with Image.open(out) as a, Image.open(src) as b:
        assert np.asarray(a).mean() > np.asarray(b).mean()
    # the CLI must never overwrite its input
    result = subprocess.run([sys.executable, "-m", "reefhue.pipeline.cli", str(src), str(src)], capture_output=True)
    assert result.returncode == 2


def test_parallel_strips_give_identical_results(monkeypatch):
    """Pointwise stages and LUTs split into strips must match a single, unsplit call."""
    from reefhue.pipeline import lut as lut_library
    from reefhue.pipeline import parallel

    rng = np.random.default_rng(7)
    img = rng.random((1100, 1000, 3)).astype(np.float32)  # large enough for several strips
    edit = default_edit()
    edit["underwater"]["depth_m"] = 6
    edit["light"].update(contrast=25, shadows=30, highlights=-20, exposure_ev=0.3)
    edit["color"].update(vibrance=30, saturation=-10)
    edit["levels"]["r"].update(black=0.05, white=0.9)
    edit["curves"]["master"]["points"] = [[0, 0], [0.3, 0.25], [0.7, 0.8], [1, 1]]
    edit["lut"].update(file="ReefHue Film Fade.cube", intensity=80)
    monkeypatch.setattr(lut_library, "LIBRARY", lut_library.LutLibrary([lut_library.app_lut_dir()]))

    def render():
        return pl.process(img, edit, pl.make_context((1000, 1100), (1000, 1100), edit))

    calls = []
    real_executor = parallel._executor
    monkeypatch.setattr(parallel, "_executor", lambda: calls.append(1) or real_executor())
    split = render()
    assert calls or parallel.WORKERS < 2  # really split (single-core machines excepted)
    monkeypatch.setattr(parallel, "WORKERS", 1)
    whole = render()
    assert np.array_equal(split, whole)
