"""Milestone 1 acceptance: window layout, runtime language/theme switch, FFmpeg handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from reefhue import i18n
from reefhue.i18n import tr
from reefhue.io import ffmpeg as ffmpeg_io
from tests.conftest import wait_until


def _status(available: bool, folder: Path) -> ffmpeg_io.FfmpegStatus:
    if available:
        tool = lambda n: ffmpeg_io.ToolStatus(n, folder / n, "folder", f"{n} version x", None)  # noqa: E731
    else:
        tool = lambda n: ffmpeg_io.ToolStatus(n, None, None, None, ffmpeg_io.NOT_FOUND)  # noqa: E731
    return ffmpeg_io.FfmpegStatus(tool("ffmpeg"), tool("ffprobe"), folder, False)


def _menu_titles(win) -> list[str]:
    return [a.text() for a in win.menuBar().actions()]


def test_layout_is_one_third_two_thirds(window):
    window.show()
    wait_until(lambda: window.isVisible())
    left, right = window.splitter.sizes()
    assert left > 0 and right > 0
    assert abs(left / (left + right) - 1 / 3) < 0.05


def test_menus_and_empty_state_in_english(window):
    assert _menu_titles(window) == ["&File", "&Edit", "&View", "&Help"]
    preview = window.viewer.preview
    assert preview.stack.currentIndex() == preview.PAGE_EMPTY
    assert preview.empty_state.open_button.text() == "Open folder…"
    assert window.viewer.edit_panel.isHidden()  # whole right side is the empty state
    assert window.windowTitle() == "ReefHue"


@pytest.mark.parametrize("code", ["de", "nl", "en"])
def test_language_switch_at_runtime(window, code):
    window.ctx.set_language_choice(code)
    assert i18n.current_language() == code
    assert _menu_titles(window) == [tr("menu.file"), tr("menu.edit"), tr("menu.view"), tr("menu.help")]
    assert window.viewer.preview.empty_state.title.text() == tr("empty.title")
    tabs = window.viewer.edit_panel.tabs
    assert [tabs.tabText(i).replace("&&", "&") for i in range(tabs.count())] == [
        tr(f"editpanel.tab.{k}")
        for k in ("underwater", "light", "color", "curves", "details", "geometry", "lut_presets")
    ]
    for key, action in window._actions.items():
        assert action.text() == tr(key)
    assert window.ctx.settings.get("ui/language") == code
    assert window.language_actions[code].isChecked()


def test_german_texts_really_differ(window):
    window.ctx.set_language_choice("de")
    assert _menu_titles(window) == ["&Datei", "&Bearbeiten", "&Ansicht", "&Hilfe"]
    assert window.file_pane.header.text() == "Kein Ordner geöffnet"


def test_system_language_choice(window, monkeypatch):
    monkeypatch.setattr("reefhue.ui.context.os_ui_languages", lambda: ["nl-NL", "en-US"])
    window.ctx.set_language_choice("system")
    assert i18n.current_language() == "nl"
    assert window.ctx.settings.get("ui/language") == "system"


def test_theme_switch_via_menu(window, qapp):
    from reefhue.ui.theme import PALETTES

    window.theme_actions["dark"].trigger()
    assert window.ctx.theme.active == "dark"
    assert PALETTES["dark"]["bg"] in qapp.styleSheet()
    window.theme_actions["light"].trigger()
    assert window.ctx.theme.active == "light"


def test_missing_ffmpeg_is_graceful(window, tmp_path):
    window.show()
    window.apply_ffmpeg_status(_status(False, tmp_path), notify=True)
    banner = window.viewer.preview.banner
    assert banner.isVisible()
    assert banner.text_label.text() == tr("ffmpeg.banner.missing")
    assert window.ctx.video_enabled is False
    dialog = window._ffmpeg_dialog
    assert dialog is not None and dialog.isVisible() and not dialog.isModal()
    assert ffmpeg_io.DOWNLOAD_URL in dialog.download.text()
    # the window remains usable: language switch still works and updates the banner
    window.ctx.set_language_choice("de")
    assert banner.text_label.text() == tr("ffmpeg.banner.missing")
    assert dialog.heading.text() == tr("ffmpeg.dialog.heading_missing")
    dialog.close()


def test_available_ffmpeg_hides_banner(window, tmp_path):
    window.show()
    window.apply_ffmpeg_status(_status(False, tmp_path), notify=False)
    window.apply_ffmpeg_status(_status(True, tmp_path), notify=True)
    assert not window.viewer.preview.banner.isVisible()
    assert window.ctx.video_enabled is True
    assert window._ffmpeg_dialog is None  # no dialog when everything is fine


def test_ffmpeg_help_texts(tmp_path):
    from reefhue.ui.ffmpeg_dialog import help_texts

    blocked = ffmpeg_io.FfmpegStatus(
        ffmpeg_io.ToolStatus("ffmpeg", tmp_path / "ffmpeg", "folder", None, ffmpeg_io.FAILED_TO_RUN),
        ffmpeg_io.ToolStatus("ffprobe", tmp_path / "ffprobe", "folder", None, ffmpeg_io.NOT_EXECUTABLE),
        tmp_path,
        False,
    )
    mac = dict(help_texts(blocked, platform="darwin"))
    assert "com.apple.quarantine" in mac["ffmpeg.dialog.help_quarantine"]
    assert "chmod +x" in mac["ffmpeg.dialog.help_not_executable"]
    win = dict(help_texts(blocked, platform="win32"))
    assert "ffmpeg.dialog.help_quarantine" not in win
    assert "ffmpeg.dialog.help_failed" in win


def test_open_folder_is_read_only_and_updates_recent(window, tmp_path):
    project = tmp_path / "Dive Day 1"
    project.mkdir()
    (project / "GX010001.MP4").write_bytes(b"\x00" * 16)
    before = sorted(p.name for p in project.iterdir())
    assert window.open_folder(project)
    assert window.ctx.current_folder == project.resolve()
    assert "Dive Day 1" in window.windowTitle()
    assert window.ctx.settings.recent_folders()[0] == str(project.resolve())
    assert window.viewer.preview.stack.currentIndex() == window.viewer.preview.PAGE_NO_SELECTION
    assert not window.viewer.edit_panel.isHidden()
    assert sorted(p.name for p in project.iterdir()) == before  # nothing written into the folder


def test_open_missing_folder_warns_and_drops_from_recent(window, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(
        "reefhue.ui.main_window.QMessageBox.warning", lambda *a, **k: warnings.append(a)
    )
    gone = tmp_path / "gone"
    window.ctx.settings.set_recent_folders([str(gone)])
    assert not window.open_folder(gone)
    assert warnings
    assert window.ctx.settings.recent_folders() == []
    assert window.ctx.current_folder is None


def test_dropped_folder_detection(qapp, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl

    from reefhue.ui.main_window import dropped_folder

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path))])
    assert dropped_folder(mime) == tmp_path
    file = tmp_path / "a.jpg"
    file.write_bytes(b"x")
    mime.setUrls([QUrl.fromLocalFile(str(file))])
    assert dropped_folder(mime) is None
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path)), QUrl.fromLocalFile(str(tmp_path))])
    assert dropped_folder(mime) is None


def test_toggle_edit_panel_is_remembered(window, tmp_path):
    window.open_folder(tmp_path)
    action = window._actions["action.toggle_edit_panel"]
    action.trigger()
    assert window.viewer.edit_panel.isHidden()
    assert window.ctx.settings.get("ui/edit_panel_visible") is False
    action.trigger()
    assert not window.viewer.edit_panel.isHidden()


def test_window_state_roundtrip(ctx):
    from reefhue.ui.main_window import MainWindow

    first = MainWindow(ctx)
    first.resize(1300, 820)
    first.show()
    wait_until(lambda: first.isVisible())
    first.splitter.setSizes([500, 800])
    sizes = first.splitter.sizes()
    first.close()
    second = MainWindow(ctx)
    second.show()
    wait_until(lambda: second.isVisible())
    # The offscreen test platform ignores restoreGeometry() for hidden windows, so
    # compare the split ratio (exact sizes are restored on real macOS/Windows).
    restored = second.splitter.sizes()
    assert abs(restored[0] / sum(restored) - sizes[0] / sum(sizes)) < 0.01
    second.close()


def test_settings_dialog_applies_and_validates(window):
    from PySide6.QtWidgets import QDialogButtonBox

    from reefhue.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(window.ctx, window)
    ok = dialog.buttons.button(QDialogButtonBox.Ok)
    dialog.suffix.setText("bad/name")
    assert not ok.isEnabled()
    assert dialog.name_error.isVisibleTo(dialog)
    dialog.suffix.setText("_reef")
    assert ok.isEnabled()
    dialog.jpeg_quality.setValue(88)
    dialog.hw_encoder.setChecked(True)
    dialog.thumb_size.setValue(200)
    dialog.preview_quality.setCurrentIndex(dialog.preview_quality.findData("25"))
    dialog.theme.setCurrentIndex(dialog.theme.findData("dark"))
    dialog.language.setCurrentIndex(dialog.language.findData("de"))
    dialog.accept()
    s = window.ctx.settings
    assert s.get("export/suffix") == "_reef"
    assert s.get("export/jpeg_quality") == 88
    assert s.get("export/hw_encoder") is True
    assert s.get("thumbs/size") == 200
    assert s.get("preview/quality") == "25"
    assert window.ctx.theme.active == "dark"
    assert i18n.current_language() == "de"
    assert _menu_titles(window)[0] == "&Datei"


def test_settings_dialog_clear_cache(window):
    from reefhue.core import paths
    from reefhue.ui.settings_dialog import SettingsDialog

    (paths.cache_dir() / "thumb.jpg").write_bytes(b"x" * 2048)
    dialog = SettingsDialog(window.ctx, window)
    assert wait_until(lambda: "2" in dialog.cache_label.text())
    dialog._clear_cache()
    assert wait_until(lambda: dialog.cache_label.text() == tr("settings.cache_cleared"))
    assert list(paths.cache_dir().iterdir()) == []
    dialog.reject()


def test_about_and_error_dialogs_construct(window, qapp):
    from reefhue.ui.about_dialog import AboutDialog
    from reefhue.ui.error_dialog import ErrorDialog

    AboutDialog(window.ctx, window).deleteLater()
    dialog = ErrorDialog("msg", "Traceback: details", window)
    dialog.copy_details()
    assert qapp.clipboard().text() == "Traceback: details"
    dialog.deleteLater()
