"""Milestone 7: tooltips, folder error states, about dialog."""

from __future__ import annotations

import os
import shutil
import sys

import pytest
from PySide6.QtWidgets import QAbstractButton, QComboBox, QMessageBox, QSlider

from reefhue import i18n
from reefhue.core import scan
from reefhue.pipeline.params import PARAMS
from reefhue.ui.edit_panel.param_slider import ParamSlider
from tests.conftest import wait_until
from tests.media import make_jpeg

posix_only = pytest.mark.skipif(sys.platform.startswith("win") or os.geteuid() == 0,
                                reason="needs POSIX permissions (and not root)")


# ---------------------------------------------------------------- tooltips
@pytest.mark.parametrize("lang", ["en", "de", "nl"])
def test_every_parameter_has_a_tooltip(lang):
    assert [p.tip_key for p in PARAMS if not i18n.has_key(p.tip_key, lang)] == []


def _has_tip(widget, root) -> bool:
    while widget is not None and widget is not root:
        if widget.toolTip():
            return True
        widget = widget.parentWidget()
    return False


def _inside(widget, class_name: str) -> bool:
    while widget is not None:
        if widget.metaObject().className() == class_name:
            return True
        widget = widget.parentWidget()
    return False


def test_edit_panel_and_file_pane_controls_have_tooltips(window):
    roots = (window.viewer.edit_panel, window.file_pane, window.viewer.preview.canvas_page)
    missing = []
    for root in roots:
        for cls in (QAbstractButton, QComboBox, QSlider):
            for widget in root.findChildren(cls):
                parent = widget.parent().metaObject().className()
                if widget.objectName().startswith("qt_") or parent in ("QTableView", "QTabBar"):
                    continue  # Qt's own helper buttons
                if _inside(widget, "ExtensionFilterPopup"):
                    continue  # check boxes labelled with the extension itself

                if not _has_tip(widget, root):
                    missing.append(f"{type(root).__name__}: {type(widget).__name__} {getattr(widget, 'text', lambda: '')()!r}")
    assert missing == [], "\n".join(missing)


def test_tooltips_follow_the_language(window):
    slider = window.viewer.edit_panel.sliders[("underwater", "depth_m")]
    assert isinstance(slider, ParamSlider)
    english = slider.slider.toolTip()
    assert "Depth" in english or "depth" in english
    window.ctx.set_language_choice("de")
    assert slider.slider.toolTip() != english and "Tiefe" in slider.slider.toolTip()
    assert window.viewer.edit_panel.reset_buttons["light"].toolTip().startswith("Alle")


# ---------------------------------------------------------------- folder access
@posix_only
def test_folder_access_states(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    assert scan.folder_access(ok) == scan.ACCESS_OK
    assert scan.folder_access(tmp_path / "nope") == scan.ACCESS_MISSING
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, 0o555)
    none = tmp_path / "none"
    none.mkdir()
    os.chmod(none, 0o000)
    try:
        assert scan.folder_access(ro) == scan.ACCESS_READ_ONLY
        assert scan.folder_access(none) == scan.ACCESS_NONE
        with pytest.raises(OSError):
            scan.scan_folder(none, False)  # an unreadable root must not look like "all files deleted"
    finally:
        os.chmod(ro, 0o755)
        os.chmod(none, 0o755)


def _wait_open(window):
    ctl = window.controller
    assert wait_until(lambda: not ctl.busy, timeout=10)
    wait_until(lambda: False, 0.1)


@pytest.fixture
def no_modal(monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda _p, _t, text, *a, **k: shown.append(text)))
    return shown


@posix_only
def test_write_protected_folder_opens_read_only(window, tmp_path, no_modal):
    folder = tmp_path / "Locked"
    make_jpeg(folder / "A.jpg")
    os.chmod(folder, 0o555)
    try:
        assert window.open_folder(folder)
        _wait_open(window)
        project = window.controller.project
        assert project is not None and project.read_only
        assert "sidecar.warn.read_only_folder" in window.controller.warnings()
        assert "write-protected" in window.file_pane.notice.text()
        item = project.item_by_path("A.jpg")
        window.file_pane.select_ids([item.id], item.id)
        window.editor.set_param(("light", "exposure_ev"), 1.0, live=False)
        window.editor.commit()
        window.controller.flush()
        assert sorted(p.name for p in folder.iterdir()) == ["A.jpg"]  # nothing written
        assert no_modal == []
    finally:
        os.chmod(folder, 0o755)


@posix_only
def test_unreadable_folder_gives_a_message(window, tmp_path, no_modal):
    folder = tmp_path / "Private"
    make_jpeg(folder / "A.jpg")
    os.chmod(folder, 0o000)
    try:
        window.open_folder(folder)
        assert wait_until(lambda: no_modal, timeout=10)
        assert "not allowed to read" in no_modal[0]
        assert window.controller.project is None and window.ctx.current_folder is None
    finally:
        os.chmod(folder, 0o755)


def test_folder_deleted_while_open_closes_it(window, tmp_path, no_modal):
    folder = tmp_path / "Gone"
    make_jpeg(folder / "A.jpg")
    assert window.open_folder(folder)
    _wait_open(window)
    assert window.controller.project is not None
    shutil.rmtree(folder)
    window.controller.refresh()
    assert wait_until(lambda: window.controller.project is None, timeout=10)
    assert no_modal and "does not exist" in no_modal[0]
    assert window.ctx.current_folder is None
    assert str(folder.resolve()) not in window.ctx.settings.recent_folders()


# ---------------------------------------------------------------- about
def test_about_dialog_shows_and_copies_system_info(window):
    from PySide6.QtWidgets import QApplication, QLabel

    from reefhue import __version__
    from reefhue.core import diagnostics
    from reefhue.ui.about_dialog import AboutDialog

    dialog = AboutDialog(window.ctx, window)
    texts = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert __version__ in texts and "PySide6" in texts and "NumPy" in texts
    assert diagnostics.platform_text() in texts
    dialog.copy_button.click()
    copied = QApplication.clipboard().text()
    assert copied.startswith("ReefHue\n") and f"Version: {__version__}" in copied and "FFmpeg:" in copied
    dialog.deleteLater()


def test_diagnostics_helpers():
    from reefhue.core import diagnostics

    assert diagnostics.ffmpeg_version("ffmpeg version 8.0.1 Copyright (c) 2000-2025") == "8.0.1"
    assert diagnostics.ffmpeg_version(None) is None
    names = dict(diagnostics.library_versions())
    assert names["NumPy"] and names["OpenCV"] != "–"


# ---------------------------------------------------------------- empty states
def test_filters_hiding_everything_show_a_hint_with_reset(window, tmp_path):
    folder = tmp_path / "Dive"
    make_jpeg(folder / "A.jpg")
    assert window.open_folder(folder)
    _wait_open(window)
    pane = window.file_pane
    assert pane.views.currentWidget() is pane.table
    pane.search_edit.setText("zzz")
    assert pane.views.currentWidget() is pane.message
    assert "filter" in pane.message.text()
    pane.message.linkActivated.emit("clear")
    assert pane.search_edit.text() == "" and pane.views.currentWidget() is pane.table
