"""LUT parsing and application vs. known results; LUT library; LUT stages (SPEC §11)."""

from __future__ import annotations

import numpy as np
import pytest

from reefhue.pipeline import lut
from reefhue.pipeline import pipeline as pl
from reefhue.pipeline.params import default_edit

RNG = np.random.default_rng(4)
SAMPLE = RNG.random((40, 50, 3)).astype(np.float32)


def cube_text(table: np.ndarray, extra: str = "") -> str:
    n = table.shape[0]
    rows = [f"{v[0]:.7f} {v[1]:.7f} {v[2]:.7f}" for b in range(n) for g in range(n) for r in range(n)
            for v in [table[b, g, r]]]
    return f"# test\nTITLE \"t\"\n{extra}LUT_3D_SIZE {n}\n" + "\n".join(rows) + "\n"


def test_identity_is_exact():
    l = lut.parse_cube(cube_text(lut.identity_table(17)))
    assert l.size == 17 and l.title == "t"
    assert np.abs(l(SAMPLE) - SAMPLE).max() < 1e-6


def test_linear_maps_are_exact_with_trilinear():
    ident = lut.identity_table(5)
    inverted = lut.parse_cube(cube_text(1.0 - ident))
    assert np.abs(inverted(SAMPLE) - (1 - SAMPLE)).max() < 1e-6
    swapped = lut.parse_cube(cube_text(ident[..., ::-1]))  # R <-> B
    assert np.abs(swapped(SAMPLE) - SAMPLE[..., ::-1]).max() < 1e-6


def test_data_order_red_fastest():
    text = "LUT_3D_SIZE 2\n" + "\n".join(["0 0 0", "1 0 0", "0 1 0", "1 1 0", "0 0 1", "1 0 1", "0 1 1", "1 1 1"])
    l = lut.parse_cube(text)
    assert np.allclose(l(np.array([[[1.0, 0.0, 0.0]]], np.float32)), [1, 0, 0])
    assert np.allclose(l(np.array([[[0.0, 0.0, 1.0]]], np.float32)), [0, 0, 1])


def test_nonlinear_curve_accuracy():
    squared = lut.parse_cube(cube_text(np.square(lut.identity_table(33))))
    # linear interpolation of x² on a 1/32 grid: error ≤ h²/4 ≈ 0.00024
    assert np.abs(squared(SAMPLE) - np.square(SAMPLE)).max() < 3e-4


def test_1d_lut_and_domain():
    text = "LUT_1D_SIZE 3\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 2 2 2\n0 0 0\n0.5 0.5 0.5\n1 1 1\n"
    l = lut.parse_cube(text)
    x = np.array([[[0.0, 1.0, 2.0], [0.5, 1.5, 3.0]]], np.float32)
    assert np.allclose(l(x), [[[0, 0.5, 1.0], [0.25, 0.75, 1.0]]], atol=1e-6)


def test_3d_domain_scaling():
    l = lut.parse_cube(cube_text(lut.identity_table(9), extra="DOMAIN_MIN 0 0 0\nDOMAIN_MAX 2 2 2\n"))
    assert np.allclose(l(np.array([[[1.0, 2.0, 0.5]]], np.float32)), [0.5, 1.0, 0.25], atol=1e-6)


def test_shaper_plus_3d():
    shaper = "\n".join(f"{v:.6f} {v:.6f} {v:.6f}" for v in np.linspace(0, 1, 5))
    text = ("LUT_1D_SIZE 5\nLUT_1D_INPUT_RANGE -0.1 1.5\nLUT_3D_SIZE 2\n" + shaper + "\n"
            + "\n".join(["0 0 0", "1 0 0", "0 1 0", "1 1 0", "0 0 1", "1 0 1", "0 1 1", "1 1 1"]))
    l = lut.parse_cube(text)
    x = np.array([[[-0.1, 0.7, 1.5]]], np.float32)
    assert np.allclose(l(x), (x + 0.1) / 1.6, atol=1e-6)


@pytest.mark.parametrize("text", ["", "TITLE x\n0 0 0\n", "LUT_3D_SIZE 2\n0 0 0\n", "LUT_3D_SIZE 1\n0 0 0\n",
                                  "LUT_3D_SIZE abc\n"])
def test_invalid_files(text):
    with pytest.raises(lut.LutError):
        lut.parse_cube(text)


def test_write_read_round_trip(tmp_path):
    table = np.clip(lut.identity_table(9) * [1.1, 1.0, 0.9], 0, 1).astype(np.float32)
    lut.write_cube(tmp_path / "x.cube", table, "Round trip")
    l = lut.load_cube(tmp_path / "x.cube")
    assert l.title == "Round trip" and np.abs(l.table3d - table).max() < 1e-6


def test_library_import_resolve_check(tmp_path):
    app_dir, user_dir = tmp_path / "app", tmp_path / "user"
    app_dir.mkdir()
    lib = lut.LutLibrary([app_dir, user_dir])
    src = tmp_path / "Look.cube"
    src.write_text(cube_text(lut.identity_table(3)))
    first = lib.import_file(src)
    second = lib.import_file(src)
    assert first == app_dir / "Look.cube" and second == app_dir / "Look_2.cube"
    assert lib.names() == ["Look.cube", "Look_2.cube"]
    assert lib.resolve("../../Look.cube") == first  # names only, never a path outside the folders
    sha = lut.sha256_of(first)
    assert lib.check("Look.cube", sha) == "ok"
    first.write_text(cube_text(1 - lut.identity_table(3)))
    assert lib.check("Look.cube", sha) == "changed"
    assert lib.check("Gone.cube", sha) == "missing"
    assert lib.check(None, None) == "ok"
    bad = tmp_path / "bad.cube"
    bad.write_text("nope")
    with pytest.raises(lut.LutError):
        lib.import_file(bad)
    assert lib.load("Look.cube") is lib.load("Look.cube")  # cached


def test_import_falls_back_to_user_folder(tmp_path, monkeypatch):
    app_dir, user_dir = tmp_path / "app", tmp_path / "user"
    app_dir.mkdir()
    src = tmp_path / "L.cube"
    src.write_text(cube_text(lut.identity_table(2)))
    real_copy = lut.shutil.copyfile

    def copy(a, b):
        if str(b).startswith(str(app_dir)):
            raise PermissionError("read-only app folder")
        return real_copy(a, b)

    monkeypatch.setattr(lut.shutil, "copyfile", copy)
    assert lut.LutLibrary([app_dir, user_dir]).import_file(src) == user_dir / "L.cube"


def test_lut_stage_intensity_position_and_missing(tmp_path, monkeypatch):
    lib = lut.LutLibrary([tmp_path])
    (tmp_path / "inv.cube").write_text(cube_text(1 - lut.identity_table(5)))
    monkeypatch.setattr(lut, "LIBRARY", lib)
    img = SAMPLE * 0.8 + 0.1

    def render(edit):
        return pl.process(img, edit, pl.make_context((50, 40), (50, 40), edit))

    edit = default_edit()
    edit["lut"].update(file="inv.cube", intensity=100, position="end")
    assert np.abs(render(edit) - (1 - img)).max() < 1e-4
    edit["lut"]["intensity"] = 50
    assert np.abs(render(edit) - 0.5).max() < 1e-4  # halfway between image and inverted image
    edit["lut"].update(intensity=100, position="start")
    start = render(edit)
    assert np.abs(start - (1 - img)).max() < 1e-4  # inversion commutes here …
    edit["light"]["exposure_ev"] = 1.0
    edit["lut"]["position"] = "end"
    end_result = render(edit)
    edit["lut"]["position"] = "start"
    assert not np.allclose(render(edit), end_result, atol=1e-3)  # … but not with other edits
    edit["lut"]["file"] = "missing.cube"
    edit["light"]["exposure_ev"] = 0
    assert np.abs(render(edit) - img).max() < 1e-4  # missing LUT: ignored


def test_bundled_luts_load():
    lib = lut.LutLibrary([lut.app_lut_dir()])
    names = lib.names()
    assert {"ReefHue Warm Reef.cube", "ReefHue Clear Blue.cube", "ReefHue Film Fade.cube", "ReefHue Punchy.cube"} <= set(names)
    for name in names:
        l = lib.load(name)
        assert l is not None and l.size == 33
        out = l(SAMPLE)
        assert out.min() >= 0 and out.max() <= 1
