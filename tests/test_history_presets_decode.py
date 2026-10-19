"""Undo history, copy/paste sections, user presets, photo decoding."""

from __future__ import annotations

import numpy as np

from reefhue.core import presets
from reefhue.core.history import EditHistory
from reefhue.io.decode import load_photo, oriented_size
from reefhue.pipeline.params import default_edit
from tests.media import make_heic, make_jpeg, make_png


def test_history_coalesces_drags_and_undoes():
    base = default_edit()
    h = EditHistory(base)
    for v in (0.1, 0.2, 0.3):  # one drag
        e = default_edit()
        e["light"]["exposure_ev"] = v
        h.record(e, merge_key="light.exposure_ev")
    h.close()
    e2 = default_edit()
    e2["light"]["exposure_ev"] = 0.3
    e2["light"]["contrast"] = 10
    h.record(e2, merge_key="light.contrast")
    h.close()
    assert len(h) == 3
    assert h.undo()["light"]["contrast"] == 0
    assert h.undo()["light"]["exposure_ev"] == 0
    assert h.undo() is None
    assert h.redo()["light"]["exposure_ev"] == 0.3
    # a new change after undo drops the redo branch
    e3 = default_edit()
    e3["color"]["saturation"] = 5
    h.record(e3)
    assert not h.can_redo


def test_same_key_after_close_is_a_new_step():
    h = EditHistory(default_edit())
    for v in (1, 2):
        e = default_edit()
        e["light"]["contrast"] = v
        h.record(e, merge_key="c")
        h.close()
    assert len(h) == 3


def test_sections_extract_apply_and_geometry_default_off():
    src = default_edit()
    src["light"]["exposure_ev"] = 1.0
    src["geometry"]["rotate_90"] = 1
    src["curves"]["r"]["points"] = [[0, 0], [0.5, 0.6], [1, 1]]
    assert "geometry" not in presets.DEFAULT_SECTIONS
    partial = presets.extract(src, presets.DEFAULT_SECTIONS)
    target = default_edit()
    result = presets.apply(target, partial, presets.DEFAULT_SECTIONS)
    assert result["light"]["exposure_ev"] == 1.0
    assert result["curves"]["r"]["points"] == [[0, 0], [0.5, 0.6], [1, 1]]
    assert result["geometry"]["rotate_90"] == 0
    only_light = presets.apply(target, src, ["light"])
    assert only_light["light"]["exposure_ev"] == 1.0 and only_light["curves"] == default_edit()["curves"]
    assert presets.reset_sections(result, ["light"])["light"] == default_edit()["light"]


def test_user_presets_round_trip(tmp_path):
    edit = default_edit()
    edit["color"]["vibrance"] = 25
    p = presets.save_preset("Blue water / 20 m", edit, ["color", "light"], tmp_path)
    assert p.path.parent == tmp_path and p.path.suffix == ".json"
    loaded = presets.load_presets(tmp_path)
    assert [x.name for x in loaded] == ["Blue water / 20 m"]
    assert sorted(loaded[0].sections) == ["color", "light"]
    assert "geometry" not in loaded[0].edit  # presets contain only the chosen edit sections
    (tmp_path / "broken.json").write_text("{nope")
    assert len(presets.load_presets(tmp_path)) == 1
    presets.delete_preset(loaded[0])
    assert presets.load_presets(tmp_path) == []


def test_decode_applies_orientation_and_downscales(tmp_path):
    make_jpeg(tmp_path / "o.jpg", orientation=6, size=(400, 200))
    img, full = load_photo(tmp_path / "o.jpg")
    assert full == (200, 400) and img.shape == (400, 200, 3)
    assert img.dtype == np.float32 and 0 <= img.min() and img.max() <= 1
    small, full = load_photo(tmp_path / "o.jpg", max_side=100)
    assert max(small.shape[:2]) == 100 and full == (200, 400)
    assert oriented_size(tmp_path / "o.jpg") == (200, 400)


def test_decode_png_heic_and_16bit(tmp_path):
    import cv2

    make_png(tmp_path / "p.png")
    assert load_photo(tmp_path / "p.png")[0].shape == (30, 40, 3)
    make_heic(tmp_path / "h.heic")
    assert load_photo(tmp_path / "h.heic")[0].shape == (48, 64, 3)
    deep = (np.linspace(0, 65535, 20 * 10 * 3).reshape(10, 20, 3)).astype(np.uint16)
    cv2.imwrite(str(tmp_path / "d.png"), deep)
    img, _ = load_photo(tmp_path / "d.png")
    assert img.shape == (10, 20, 3) and abs(float(img.max()) - 1.0) < 1e-4
    assert len(np.unique(img)) > 256  # really 16-bit, not silently reduced to 8-bit
