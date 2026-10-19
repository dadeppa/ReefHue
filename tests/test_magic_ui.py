"""Milestone 6 UI: magic button (one undo step, selection, video frames), LUT selection."""

from __future__ import annotations

import pytest

from reefhue.pipeline import lut
from tests.conftest import wait_until
from tests.test_editor import open_photo, photos  # noqa: F401  (fixture)
from tests.test_lut import cube_text


def wait_idle(window):
    panel = window.viewer.edit_panel
    assert wait_until(lambda: panel.magic_button.isEnabled(), timeout=20)


def test_magic_is_one_undo_step_and_editable(window, photos):  # noqa: F811
    item = open_photo(window, photos)
    panel, editor = window.viewer.edit_panel, window.editor
    editor.set_param(("white_balance", "temperature"), 30, live=False)
    editor.commit()
    before = editor.edit
    panel.magic_button.click()
    wait_idle(window)
    edit = item.entry["edit"]
    auto = edit["underwater"]["auto_result"]
    assert auto["analyzed_at"] and auto["source"] == "frame" and auto["depth_estimated"]
    assert edit["white_balance"]["temperature"] == 0
    assert edit["levels"]["r"] != before["levels"]["r"] or auto["gains_rgb"] != [1, 1, 1]
    assert panel.magic_status.text()  # "depth estimated" note
    window._actions["action.undo"].trigger()
    assert editor.edit == before  # the whole magic result is one undo step
    window._actions["action.redo"].trigger()
    editor.set_param(("underwater", "depth_m"), 4.0, live=False)
    editor.commit()
    assert not editor.edit["underwater"]["auto_result"]["depth_estimated"]  # user value from now on


def test_magic_apply_to_selection(window, photos):  # noqa: F811
    open_photo(window, photos)
    ctl = window.controller
    a, b = ctl.project.item_by_path("A.jpg"), ctl.project.item_by_path("B.jpg")
    window.file_pane.select_ids([a.id, b.id], a.id)
    progress = []
    window.editor.magic_progress.connect(lambda d, t: progress.append((d, t)))
    window.viewer.edit_panel.magic_selection_button.click()
    wait_idle(window)
    assert a.entry["edit"]["underwater"]["auto_result"]["analyzed_at"]
    assert b.entry["edit"]["underwater"]["auto_result"]["analyzed_at"]
    assert progress[0] == (0, 2) and progress[-1] == (0, 0)
    assert window.editor.histories[b.id].can_undo


def test_lut_choose_intensity_and_missing(window, photos, monkeypatch):  # noqa: F811
    folder = photos.parent / "luts"
    folder.mkdir()
    (folder / "Invert.cube").write_text(cube_text(1 - lut.identity_table(5)))
    monkeypatch.setattr(lut, "LIBRARY", lut.LutLibrary([folder]))
    item = open_photo(window, photos)
    panel, editor = window.viewer.edit_panel, window.editor
    panel.reload_luts()
    assert not panel.lut_intensity.isEnabled()
    index = panel.lut_combo.findData("Invert.cube")
    assert index > 0 and panel.lut_combo.itemText(index) == "Invert"
    panel.lut_combo.setCurrentIndex(index)
    panel.lut_combo.activated.emit(index)
    assert item.entry["edit"]["lut"]["file"] == "Invert.cube"
    assert item.entry["edit"]["lut"]["sha256"] == lut.sha256_of(folder / "Invert.cube")
    assert panel.lut_intensity.isEnabled() and panel.lut_status.isHidden()
    assert wait_until(lambda: not window.engine.busy, timeout=10)
    (folder / "Invert.cube").write_text(cube_text(lut.identity_table(3)))
    editor.set_param(("lut", "intensity"), 50, live=False)
    editor.commit()
    assert not panel.lut_status.isHidden() and "changed" in panel.lut_status.text()
    (folder / "Invert.cube").unlink()
    editor.set_param(("lut", "intensity"), 60, live=False)
    editor.commit()
    assert "not found" in panel.lut_status.text().lower() or "missing" in panel.lut_status.text().lower()
    assert panel.lut_combo.currentData() == "Invert.cube"  # kept as a "missing" entry
    panel.lut_combo.setCurrentIndex(0)
    panel.lut_combo.activated.emit(0)
    assert item.entry["edit"]["lut"]["file"] is None and panel.lut_status.isHidden()


def test_builtin_presets_apply(window, photos):  # noqa: F811
    item = open_photo(window, photos)
    panel = window.viewer.edit_panel
    names = [panel.preset_list.item(i).text() for i in range(panel.preset_list.count())]
    assert names[:4] == ["Shallow 5 m", "Blue water 20 m", "Green water / lake", "With lamp"]
    panel.preset_list.setCurrentRow(1)
    preset = panel.selected_preset()
    assert preset.builtin and not panel.preset_delete.isEnabled()
    window.editor.apply_partial([item], preset.edit, preset.sections)  # what the Apply dialog does
    edit = item.entry["edit"]
    assert edit["underwater"]["depth_m"] == 20 and edit["underwater"]["water_type"] == "blue"


@pytest.mark.parametrize("frame_only", [False, True])
def test_magic_on_video(frame_only, window, tmp_path):
    from reefhue.io import ffmpeg
    from tests.media import FFMPEG, make_video

    if FFMPEG is None:
        pytest.skip("ffmpeg not on PATH")
    window.apply_ffmpeg_status(ffmpeg.check_ffmpeg(), notify=False)
    folder = tmp_path / "Dive"
    make_video(folder / "GX010001.MP4", seconds=2.0, size="320x180")
    window.open_folder(folder)
    ctl = window.controller
    assert wait_until(lambda: ctl.project is not None and not ctl.busy, timeout=15)
    item = ctl.project.item_by_path("GX010001.MP4")
    assert wait_until(lambda: item.entry["media"].get("codec"), timeout=15)
    window.file_pane.select_ids([item.id], item.id)
    panel = window.viewer.edit_panel
    assert panel.magic_frame_box.isVisibleTo(panel)
    panel.magic_frame_box.setChecked(frame_only)
    panel.magic_button.click()
    wait_idle(window)
    auto = item.entry["edit"]["underwater"]["auto_result"]
    assert auto["source"] == ("frame" if frame_only else "segment") and auto["analyzed_at"]
    window.viewer.preview.video.close()
