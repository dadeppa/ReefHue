"""Magic button (SPEC §9): measurable improvement on synthetic blue/green scenes, rules."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from reefhue.pipeline import auto, water_model
from reefhue.pipeline import pipeline as pl
from reefhue.pipeline.colorspace import linear_to_srgb, srgb_to_linear
from reefhue.pipeline.params import default_edit
from reefhue.pipeline.stages.underwater import depth_prior


def reef_chart(seed: int = 0):
    """Textured scene: open water band, sand, gray and coloured patches."""
    rng = np.random.default_rng(seed)
    h, w = 300, 360
    img = np.full((h, w, 3), (0.62, 0.55, 0.45), np.float32)
    grays = []
    for i, v in enumerate((0.25, 0.45, 0.65, 0.85)):
        y, x = 100, 20 + i * 85
        img[y:y + 60, x:x + 60] = v
        grays.append((slice(y + 5, y + 55), slice(x + 5, x + 55)))
    for i, c in enumerate([(0.8, 0.2, 0.15), (0.9, 0.6, 0.1), (0.85, 0.8, 0.2), (0.5, 0.2, 0.6), (0.2, 0.6, 0.4)]):
        y, x = 180 + (i // 3) * 55, 30 + (i % 3) * 110
        img[y:y + 45, x:x + 90] = c
    texture = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 1.5)
    img = np.clip(img * (1 + 0.12 * texture)[..., None], 0, 1)
    water = np.zeros((h, w), bool)
    water[:80] = True
    return img, grays, water


def submerge(srgb, K, depth, veil, water):
    lin = srgb_to_linear(srgb)
    out = lin * np.exp(-np.array(K, np.float32) * depth) + np.array(veil, np.float32) * (1 - np.exp(-0.1 * depth))
    out[water] = veil
    return linear_to_srgb(np.clip(out, 0, 1))


def gray_cast(img, grays) -> float:
    lin = srgb_to_linear(np.clip(img, 0, 1))
    errs = []
    for sl in grays:
        m = lin[sl].reshape(-1, 3).mean(axis=0)
        errs.append((m.max() - m.min()) / max(float(m.mean()), 1e-4))
    return float(np.mean(errs))


def render(img, edit):
    h, w = img.shape[:2]
    return pl.finalize(pl.process(img, edit, pl.make_context((w, h), (w, h), edit)))


SCENES = {
    "blue water": ((0.45, 0.07, 0.03), 8, (0.01, 0.12, 0.3), "blue"),
    "green water": ((0.6, 0.2, 0.35), 5, (0.05, 0.2, 0.1), "green"),
    "coastal": ((0.5, 0.12, 0.15), 6, (0.02, 0.15, 0.18), "coastal"),
}


@pytest.mark.parametrize("scene", SCENES)
def test_magic_measurably_reduces_the_cast(scene):
    K, depth, veil, water_type = SCENES[scene]
    truth, grays, water = reef_chart()
    before = submerge(truth, K, depth, veil, water)
    edit = default_edit()
    edit["underwater"]["water_type"] = water_type
    result = auto.analyze([before], edit)
    after = render(before, auto.apply_result(edit, result, "frame"))
    cast_before, cast_after = gray_cast(before, grays), gray_cast(after, grays)
    assert cast_after < cast_before * 0.7, (cast_before, cast_after)  # at least 30 % better
    assert result["depth_estimated"] and result["depth_m"] > 0


def test_user_depth_is_kept_and_estimate_is_flagged():
    truth, _grays, water = reef_chart()
    img = submerge(truth, (0.5, 0.12, 0.15), 6, (0.02, 0.15, 0.18), water)
    edit = default_edit()
    edit["underwater"]["depth_m"] = 12.0
    result = auto.analyze([img], edit)
    assert result["depth_m"] == 12.0 and not result["depth_estimated"]
    edit["underwater"]["depth_m"] = 0
    estimated = auto.analyze([img], edit)
    assert estimated["depth_estimated"] and 0 < estimated["depth_m"] < 40
    edit = auto.apply_result(edit, estimated, "frame")  # an estimated depth is re-estimated next time
    assert auto.analyze([img], edit)["depth_estimated"]


def test_artificial_light_gives_the_same_effective_prior():
    truth, _grays, water = reef_chart()
    img = submerge(truth, (0.5, 0.12, 0.15), 6, (0.02, 0.15, 0.18), water)
    edit = default_edit()
    plain = auto.analyze([img], edit)
    edit["underwater"]["artificial_light"] = True
    lamp = auto.analyze([img], edit)
    p1 = depth_prior(plain["depth_m"], "coastal", False)
    p2 = depth_prior(lamp["depth_m"], "coastal", True)
    assert np.allclose(p1, p2, rtol=0.1)


def test_red_gain_is_capped():
    deep_blue = np.zeros((64, 64, 3), np.float32)
    deep_blue[...] = (0.02, 0.35, 0.7)
    deep_blue += np.random.default_rng(1).normal(0, 0.01, deep_blue.shape).astype(np.float32)
    edit = default_edit()
    result = auto.analyze([np.clip(deep_blue, 0, 1)], edit)
    prior = depth_prior(result["depth_m"], "coastal", False)
    assert prior[0] * result["gains_rgb"][0] <= water_model.GAIN_SOFT_LIMIT + 1e-6


def test_apply_result_writes_editable_values():
    edit = default_edit()
    edit["white_balance"].update(temperature=40, tint=-20)
    result = {"depth_m": 7.5, "depth_estimated": True, "gains_rgb": [1.5, 1.0, 0.8],
              "levels": {c: {"black": 0.05, "white": 0.9} for c in "rgb"}}
    new = auto.apply_result(edit, result, "segment")
    uw = new["underwater"]
    assert uw["depth_m"] == 7.5 and uw["auto_result"]["gains_rgb"] == [1.5, 1.0, 0.8]
    assert uw["auto_result"]["source"] == "segment" and uw["auto_result"]["algo_version"] == 1
    assert uw["auto_result"]["depth_estimated"] and uw["auto_result"]["analyzed_at"]
    assert new["levels"]["r"] == {"black": 0.05, "white": 0.9, "gamma": 1}
    assert new["white_balance"] == {"temperature": 0, "tint": 0}  # predictable start values
    assert edit["white_balance"]["temperature"] == 40  # input untouched


def test_previous_auto_result_does_not_feed_back():
    truth, _grays, water = reef_chart()
    img = submerge(truth, (0.5, 0.12, 0.15), 6, (0.02, 0.15, 0.18), water)
    edit = default_edit()
    first = auto.analyze([img], edit)
    again = auto.analyze([img], auto.apply_result(edit, first, "frame"))
    assert again["gains_rgb"] == first["gains_rgb"] and again["levels"] == first["levels"]


def test_weights_and_helpers():
    lin = np.zeros((40, 40, 3), np.float32)
    lin[:, 20:] = np.random.default_rng(0).random((40, 20, 3)).astype(np.float32) * 0.5
    w = auto.texture_weights(lin).reshape(40, 40)
    assert w[:, :10].max() == pytest.approx(auto.TEXTURE_FLOOR) and w[:, 25:].mean() > 0.5
    pixels = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [0.2, 0.3, 0.4]], np.float32)
    assert list(auto.pixel_weights(pixels, False)) == [0.0, 0.0, 1.0]
    times = auto.segment_times(10.0, 24.0, 7)
    assert len(times) == 7 and times[0] == pytest.approx(11.0) and times[-1] == pytest.approx(23.0)
