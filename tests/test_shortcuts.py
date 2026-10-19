"""Shortcut audit (SPEC §6 table): every listed key is wired up in its context."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication

from tests.conftest import wait_until
from tests.media import FFMPEG, make_jpeg, make_video

GLOBAL = {
    "action.open_folder": "Ctrl+O",
    "action.undo": "Ctrl+Z",
    "action.redo": "Ctrl+Shift+Z",
    "action.copy_settings": "Ctrl+Shift+C",
    "action.paste_settings": "Ctrl+Shift+V",
    "action.info_overlay": "Ctrl+I",
    "action.refresh": "F5",
}


@pytest.mark.parametrize("key", GLOBAL)
def test_global_shortcuts(window, key):
    sequences = [s.toString(QKeySequence.PortableText) for s in window._actions[key].shortcuts()]
    assert GLOBAL[key] in sequences
    assert window._actions[key].shortcutContext() == Qt.WindowShortcut


def test_global_shortcuts_are_unique(window):
    seen = {}
    for key, action in window._actions.items():
        for seq in action.shortcuts():
            text = seq.toString(QKeySequence.PortableText)
            assert text not in seen, f"{text} used by {seen.get(text)} and {key}"
            seen[text] = key


def _press(widget, key, modifiers=Qt.NoModifier):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, key, modifiers))
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyRelease, key, modifiers))


def test_file_pane_up_down_and_select_all(window, tmp_path):
    folder = tmp_path / "Dive"
    for i, name in enumerate(("A.jpg", "B.jpg", "C.jpg")):
        make_jpeg(folder / name, dt=f"2024:05:01 10:0{i}:00")
    assert window.open_folder(folder)
    ctl = window.controller
    assert wait_until(lambda: ctl.project is not None and not ctl.busy, timeout=10)
    pane = window.file_pane
    first = ctl.project.item_by_path("A.jpg")
    pane.select_ids([first.id], first.id)
    view = pane.table
    view.setFocus()
    _press(view, Qt.Key_Down)
    assert window.editor.item.rel_path == "B.jpg"
    _press(view, Qt.Key_Up)
    assert window.editor.item.rel_path == "A.jpg"
    _press(view, Qt.Key_A, Qt.ControlModifier)
    assert len(pane.selected_items()) == 3


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_video_transport_keys(window, tmp_path):
    from reefhue.io import ffmpeg

    window.apply_ffmpeg_status(ffmpeg.check_ffmpeg(), notify=False)
    folder = tmp_path / "Dive"
    make_video(folder / "GX010001.MP4", seconds=12.0, size="160x90")
    window.open_folder(folder)
    ctl = window.controller
    assert wait_until(lambda: ctl.project is not None and not ctl.busy, timeout=15)
    item = ctl.project.item_by_path("GX010001.MP4")
    assert wait_until(lambda: item.entry["media"].get("codec"), timeout=15)
    window.file_pane.select_ids([item.id], item.id)
    preview = window.viewer.preview
    player = preview.video.player
    transport = preview.canvas_page.transport

    def key(k, mods=Qt.NoModifier):
        preview.keyPressEvent(QKeyEvent(QEvent.KeyPress, k, mods))

    key(Qt.Key_Right)  # +5 s
    assert wait_until(lambda: abs(player.position - 5.0) < 0.05, timeout=10)
    key(Qt.Key_Left, Qt.ShiftModifier)  # -1 s
    assert wait_until(lambda: abs(player.position - 4.0) < 0.05, timeout=10)
    key(Qt.Key_Comma)  # one frame back (25 fps)
    assert wait_until(lambda: abs(player.position - 3.96) < 0.03, timeout=10)
    volume = transport.volume.value()
    key(Qt.Key_Down)
    assert transport.volume.value() < volume
    key(Qt.Key_Up)
    assert transport.volume.value() == volume
    key(Qt.Key_L)
    assert wait_until(lambda: player.playing and player.rate == 1.0, timeout=5)
    key(Qt.Key_L)  # repeat = faster
    assert wait_until(lambda: player.rate == 2.0, timeout=5)
    key(Qt.Key_K)
    assert wait_until(lambda: not player.playing, timeout=5)
    key(Qt.Key_J)
    assert wait_until(lambda: player.playing and player.rate == -1.0, timeout=5)
    key(Qt.Key_K)
    assert wait_until(lambda: not player.playing, timeout=5)
    preview.video.close()
