"""Milestone 3 UI: sliders, live preview, undo/redo, copy/paste, presets, canvas tools."""

from __future__ import annotations

import json

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent

from reefhue.core import presets, sidecar
from reefhue.pipeline.params import PARAMS_BY_KEY, default_edit
from reefhue.ui import file_model as fm
from reefhue.ui.edit_panel.param_slider import ParamSlider
from tests.conftest import wait_until
from tests.media import make_jpeg


@pytest.fixture
def photos(tmp_path):
    root = tmp_path / "Dive"
    make_jpeg(root / "A.jpg", dt="2024:05:01 10:00:00", size=(320, 240))
    make_jpeg(root / "B.jpg", dt="2024:05:01 10:05:00", size=(320, 240))
    return root


def open_photo(window, folder, name="A.jpg"):
    assert window.open_folder(folder)
    ctl = window.controller
    assert wait_until(lambda: ctl.project is not None and not ctl.busy, timeout=10)
    item = ctl.project.item_by_path(name)
    window.file_pane.select_ids([item.id], item.id)
    assert window.editor.item is item
    assert wait_until(lambda: window.engine.last_final is not None and not window.engine.busy, timeout=10)
    return item


def wait_render(window):
    assert wait_until(lambda: not window.engine.busy and window.engine.last_final is not None, timeout=10)
    wait_until(lambda: False, 0.05)


def mean_of(result) -> float:
    img = result.image
    ptr = img.constBits()
    arr = np.frombuffer(ptr, np.uint8, img.sizeInBytes()).reshape(img.height(), img.bytesPerLine())
    return float(arr[:, : img.width() * 3].mean())


# ---------------------------------------------------------------- ParamSlider
def _key(widget, key, modifiers=Qt.NoModifier, release=True):
    from PySide6.QtWidgets import QApplication

    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, key, modifiers))
    if release:
        QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyRelease, key, modifiers))


def test_param_slider_keys_typing_reset(qapp):
    slider = ParamSlider(PARAMS_BY_KEY[("light", "exposure_ev")], ("light", "exposure_ev"))
    changes, commits = [], []
    slider.changed.connect(lambda p, v: changes.append(v))
    slider.committed.connect(commits.append)
    _key(slider.slider, Qt.Key_Plus)
    _key(slider.slider, Qt.Key_Right, Qt.ShiftModifier)
    _key(slider.slider, Qt.Key_Down, Qt.AltModifier)
    assert changes == [0.05, 0.55, 0.545]
    assert len(commits) == 3  # one undo step per key press
    assert slider.field.text() == "0.545"
    slider.field.setText("12")
    slider._on_field()
    assert slider.value == 5.0 and slider.field.text() == "5.00"  # typed value clamped
    slider.field.setText("-0,3")  # comma decimal accepted
    slider._on_field()
    assert slider.value == -0.3
    slider.reset()
    assert slider.value == 0.0 and changes[-1] == 0.0


def test_param_slider_drag_snaps_at_zero(qapp):
    slider = ParamSlider(PARAMS_BY_KEY[("light", "contrast")], ("light", "contrast"))
    values = []
    slider.changed.connect(lambda p, v: values.append(v))
    slider._on_slider_moved(slider._tick_for(2.0))
    assert values == []  # snapped to 0 → value unchanged, nothing emitted
    slider._on_slider_moved(slider._tick_for(37.4))
    assert values[-1] == 37.4


# ---------------------------------------------------------------- editing + preview
def test_photo_preview_reacts_live_and_undo(window, photos):
    item = open_photo(window, photos)
    before = mean_of(window.engine.last_final)
    assert window.viewer.edit_panel.histogram.hist is not None
    editor = window.editor
    for v in (0.3, 0.6, 1.0):  # a drag
        editor.set_param(("light", "exposure_ev"), v, live=True)
    editor.commit()
    wait_render(window)
    assert mean_of(window.engine.last_final) > before + 5
    assert item.entry["edit"]["light"]["exposure_ev"] == 1.0
    assert window._actions["action.undo"].isEnabled()
    window._actions["action.undo"].trigger()  # whole drag undone in one step
    assert item.entry["edit"]["light"]["exposure_ev"] == 0
    assert window.viewer.edit_panel.sliders[("light", "exposure_ev")].value == 0
    window._actions["action.redo"].trigger()
    assert item.entry["edit"]["light"]["exposure_ev"] == 1.0
    assert not window._actions["action.redo"].isEnabled()


def test_edit_updates_status_and_autosaves(window, photos):
    item = open_photo(window, photos)
    window.editor.set_param(("color", "saturation"), 25, live=False)
    window.editor.commit()
    model = window.file_pane.model
    assert model.data(model.index(model.row_of(item.id), fm.STATUS)) == "Edited"
    path = photos / sidecar.SIDECAR_NAME
    assert wait_until(path.exists, timeout=5)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["files"][0]["edit"]["color"]["saturation"] == 25


def test_levels_coupling_through_editor(window, photos):
    item = open_photo(window, photos)
    window.editor.set_param(("levels", "g", "white"), 0.4, live=False)
    window.editor.set_param(("levels", "g", "black"), 0.39, live=False)
    g = item.entry["edit"]["levels"]["g"]
    assert g["white"] - g["black"] >= 0.02 - 1e-9 and g["black"] == 0.38


def test_rotate_keeps_crop_content_and_flip(window, photos):
    item = open_photo(window, photos)
    editor = window.editor
    editor.set_crop({"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}, live=False)
    editor.rotate(1)
    geom = item.entry["edit"]["geometry"]
    assert geom["rotate_90"] == 1
    assert geom["crop"]["x"] == pytest.approx(0.4) and geom["crop"]["w"] == pytest.approx(0.4)
    editor.set_param(("geometry", "flip_h"), True, live=False)
    assert geom is not item.entry["edit"]["geometry"]  # new state object
    assert item.entry["edit"]["geometry"]["crop"]["x"] == pytest.approx(0.2)
    editor.rotate(1)  # with one flip active, R still turns the *view* clockwise
    assert item.entry["edit"]["geometry"]["rotate_90"] == 0


def test_copy_paste_without_geometry_by_default(window, photos):
    a = open_photo(window, photos, "A.jpg")
    editor = window.editor
    editor.set_param(("light", "contrast"), 30, live=False)
    editor.rotate(1)
    assert editor.copy_settings()
    assert window._actions["action.paste_settings"].isEnabled() is True
    b = window.controller.project.item_by_path("B.jpg")
    count = editor.apply_partial([b], editor.clipboard, presets.DEFAULT_SECTIONS)
    assert count == 1
    assert b.entry["edit"]["light"]["contrast"] == 30
    assert b.entry["edit"]["geometry"]["rotate_90"] == 0
    assert editor.histories[b.id].can_undo


def test_presets_apply_to_selection(window, photos, tmp_path):
    open_photo(window, photos, "A.jpg")
    edit = default_edit()
    edit["color"]["vibrance"] = 40
    preset = presets.save_preset("Test", edit, ["color"])
    panel = window.viewer.edit_panel
    panel.reload_presets()
    names = [panel.preset_list.item(i).text() for i in range(panel.preset_list.count())]
    assert names == ["Shallow 5 m", "Blue water 20 m", "Green water / lake", "With lamp", "Test"]  # bundled first
    panel.preset_list.setCurrentRow(0)
    assert not panel.preset_delete.isEnabled()  # bundled presets cannot be deleted
    panel.preset_list.setCurrentRow(4)
    assert panel.preset_delete.isEnabled()
    project = window.controller.project
    a, b = project.item_by_path("A.jpg"), project.item_by_path("B.jpg")
    window.file_pane.select_ids([a.id, b.id], a.id)
    window.editor.apply_partial(window.target_items(), preset.edit, ["color"])
    assert a.entry["edit"]["color"]["vibrance"] == 40 and b.entry["edit"]["color"]["vibrance"] == 40


def test_reset_and_tab_reset(window, photos):
    item = open_photo(window, photos)
    editor = window.editor
    editor.set_param(("light", "contrast"), 30, live=False)
    editor.set_param(("details", "clarity"), 20, live=False)
    window.viewer.edit_panel.reset_buttons["light"].click()
    assert item.entry["edit"]["light"]["contrast"] == 0 and item.entry["edit"]["details"]["clarity"] == 20
    window._actions["action.reset"].trigger()
    assert item.entry["edit"] == default_edit()


def test_auto_buttons_and_eyedropper(window, photos):
    item = open_photo(window, photos)
    editor = window.editor
    editor.auto("white_balance")
    assert wait_until(lambda: item.entry["edit"]["white_balance"]["temperature"] != 0, timeout=5)
    editor.set_values({("white_balance", "temperature"): 0, ("white_balance", "tint"): 0})
    w, h = window.engine.last_final.full_size
    assert editor.pick_white_balance(w / 2, h / 2, 11)
    assert item.entry["edit"]["white_balance"]["temperature"] > 0  # bluish test image → warmer


# ---------------------------------------------------------------- canvas
def test_canvas_zoom_and_region_render(window, photos):
    open_photo(window, photos)
    canvas = window.viewer.preview.canvas
    window.show()
    canvas.resize(200, 150)
    canvas.fit()
    assert canvas.zoom is None
    canvas.actual_size()
    assert canvas.zoom_percent() == 100
    if canvas.needs_region():
        assert wait_until(lambda: canvas.region is not None, timeout=10)
    canvas.fit()
    assert canvas.zoom is None


def test_canvas_crop_drag_updates_edit(window, photos):
    item = open_photo(window, photos)
    panel = window.viewer.edit_panel
    panel.tabs.setCurrentIndex(5)  # Geometry → crop mode
    wait_render(window)
    canvas = window.viewer.preview.canvas
    assert canvas.crop_mode and window.engine.last_final.crop_mode
    rect = canvas._crop_widget_rect()
    start = QPointF(rect.right() - 1, rect.bottom() - 1)
    canvas._drag = {"kind": "crop", "handle": "se", "start": start, "rect": dict(canvas.crop)}
    from PySide6.QtGui import QMouseEvent

    end = QPointF(start.x() - rect.width() / 2, start.y() - rect.height() / 2)
    move = QMouseEvent(QEvent.MouseMove, end, end, Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    canvas.mouseMoveEvent(move)
    release = QMouseEvent(QEvent.MouseButtonRelease, end, end, Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    canvas.mouseReleaseEvent(release)
    crop = item.entry["edit"]["geometry"]["crop"]
    assert crop["w"] == pytest.approx(0.5, abs=0.02) and crop["h"] == pytest.approx(0.5, abs=0.02)


def test_video_is_not_editable_yet(window, tmp_path):
    root = tmp_path / "V"
    root.mkdir()
    (root / "clip.mp4").write_bytes(b"\x00" * 64)
    window.open_folder(root)
    ctl = window.controller
    assert wait_until(lambda: ctl.project is not None and not ctl.busy)
    item = ctl.project.item_by_path("clip.mp4")
    window.file_pane.select_ids([item.id], item.id)
    assert window.editor.item is None
    assert not window.viewer.edit_panel.tabs.isEnabled()
    assert window.viewer.preview.stack.currentIndex() == window.viewer.preview.PAGE_FILE


def test_view_toggles(window, photos):
    open_photo(window, photos)
    window.show()
    window._actions["action.toggle_histogram"].trigger()
    assert window.viewer.edit_panel.histogram_frame.isHidden()
    assert window.ctx.settings.get("ui/histogram_visible") is False
    window._actions["action.toggle_curves"].trigger()
    assert window.viewer.edit_panel.curves_box.isHidden()


def test_edit_panel_retranslates(window, photos):
    open_photo(window, photos)
    from reefhue.i18n import tr

    window.ctx.set_language_choice("de")
    panel = window.viewer.edit_panel
    assert panel.sliders[("light", "exposure_ev")].label.text() == tr("param.light.exposure_ev") == "Belichtung"
    assert panel.combos[("underwater", "water_type")].itemText(0) == "Blauwasser"


def test_viewer_keyboard_shortcuts(window, photos):
    item = open_photo(window, photos)
    window.show()
    canvas = window.viewer.preview.canvas
    canvas.resize(200, 150)
    _key(canvas, Qt.Key_1)
    assert canvas.zoom_percent() == 100
    _key(canvas, Qt.Key_F)
    assert canvas.zoom is None
    _key(canvas, Qt.Key_R)
    assert item.entry["edit"]["geometry"]["rotate_90"] == 1
    _key(canvas, Qt.Key_R, Qt.ShiftModifier)
    assert item.entry["edit"]["geometry"]["rotate_90"] == 0
    _key(canvas, Qt.Key_Backslash, release=False)
    assert canvas.hold_original
    _key(canvas, Qt.Key_Backslash)
    assert not canvas.hold_original
