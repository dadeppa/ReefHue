"""Milestone 5 UI: export controller, queue (pause/cancel), dialog rules, bookkeeping."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from reefhue.core import export_plan as ep
from reefhue.core import sidecar
from reefhue.i18n import tr
from reefhue.io import export as ex
from reefhue.ui import file_model as fm
from tests.conftest import wait_until
from tests.media import FFMPEG, make_jpeg, make_video


@pytest.fixture
def fake_trash(tmp_path, monkeypatch):
    trash = tmp_path / "Trash"
    trash.mkdir()
    monkeypatch.setattr(ex, "send2trash", lambda p: shutil.move(p, trash / Path(p).name))
    return trash


class FakeDialog:
    """Stands in for ExportDialog (no modal exec in tests)."""

    Accepted = 1
    options_override: ep.ExportOptions | None = None
    remove_audio_checked = False

    def __init__(self, ctx, items, defaults, parent=None):
        self.defaults = defaults
        self.video_export_ok = bool(ctx.ffmpeg_status and ctx.ffmpeg_status.export_available)

        class _Box:
            def __init__(self, checked):
                self._c = checked

            def isChecked(self):
                return self._c

        self.remove_audio = _Box(FakeDialog.remove_audio_checked)

    def exec(self):
        return 1

    def options(self):
        return FakeDialog.options_override or self.defaults

    def deleteLater(self):
        pass


def open_folder(window, folder):
    window.open_folder(folder)
    ctl = window.controller
    assert wait_until(lambda: ctl.project is not None and not ctl.busy, timeout=15)
    return ctl.project


def run_export(window, items, opts=None):
    FakeDialog.options_override = opts
    assert window.exporter.start(items, window, dialog_factory=FakeDialog, ask=lambda t: (ep.CONFLICT_NUMBER, True))
    assert window.queue_dock.isVisibleTo(window)
    assert wait_until(lambda: not window.exporter.queue.busy, timeout=60)


def test_export_records_history_and_hides_result(window, tmp_path, fake_trash):
    folder = tmp_path / "Dive"
    make_jpeg(folder / "a.jpg")
    make_jpeg(folder / "b.jpg")
    project = open_folder(window, folder)
    a = project.item_by_path("a.jpg")
    a.entry["edit"]["light"]["exposure_ev"] = 0.5
    run_export(window, [a])
    assert (folder / "a_edited.jpg").exists()
    assert a.status == "exported"
    assert a.entry["export_history"][-1]["result"] == "ok"
    assert "a_edited.jpg" in project.derived_paths
    assert wait_until(lambda: not window.controller.busy, timeout=10)
    names = [window.file_pane.proxy.index(r, fm.NAME).data() for r in range(window.file_pane.proxy.rowCount())]
    assert sorted(names) == ["a.jpg", "b.jpg"]  # "Hide exported files" (default) hides the export
    path = folder / sidecar.SIDECAR_NAME
    assert wait_until(lambda: path.exists() and json.loads(path.read_text())["derived_files"], timeout=5)
    a.entry["edit"]["light"]["exposure_ev"] = 0.7
    assert a.status == "changed_after_export"
    assert window.exporter.pending_items() == [a]  # "Export all edited" includes changed-after-export


def test_overwrite_resets_edit_and_shows_overwritten(window, tmp_path, fake_trash, monkeypatch):
    from reefhue.ui import export_controller

    folder = tmp_path / "Dive"
    make_jpeg(folder / "a.jpg")
    project = open_folder(window, folder)
    a = project.item_by_path("a.jpg")
    window.file_pane.select_ids([a.id], a.id)
    window.editor.set_param(("color", "saturation"), -100, live=False)
    monkeypatch.setattr(export_controller.OverwriteConfirmDialog, "exec", lambda self: 1)
    run_export(window, [a], ep.ExportOptions(mode="overwrite"))
    assert (fake_trash / "a.jpg").exists()
    assert a.entry["edit"]["color"]["saturation"] == 0  # baked into the file → edit reset
    assert a.entry["export_history"][-1]["mode"] == "overwrite"
    assert a.entry["export_history"][-1]["original_trashed"] is True
    model = window.file_pane.model
    assert wait_until(lambda: model.row_of(a.id) is not None, timeout=5)
    assert model.data(model.index(model.row_of(a.id), fm.STATUS)) == tr("status.exported_overwritten")
    assert wait_until(lambda: not window.controller.busy, timeout=10)
    assert not window.editor.can_undo  # history dropped: undo would apply the edit twice


def test_overwrite_cancelled_at_confirmation(window, tmp_path, fake_trash, monkeypatch):
    from reefhue.ui import export_controller

    folder = tmp_path / "Dive"
    make_jpeg(folder / "a.jpg")
    project = open_folder(window, folder)
    a = project.item_by_path("a.jpg")
    monkeypatch.setattr(export_controller.OverwriteConfirmDialog, "exec", lambda self: 0)
    FakeDialog.options_override = ep.ExportOptions(mode="overwrite")
    assert not window.exporter.start([a], window, dialog_factory=FakeDialog)
    assert list(fake_trash.iterdir()) == []


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_pause_and_cancel_running_video(window, tmp_path, fake_trash):
    from reefhue.io import ffmpeg

    window.apply_ffmpeg_status(ffmpeg.check_ffmpeg(), notify=False)
    folder = tmp_path / "Dive"
    make_video(folder / "v.mp4", seconds=6.0, size="1280x720")
    project = open_folder(window, folder)
    v = project.item_by_path("v.mp4")
    assert wait_until(lambda: v.entry["media"].get("codec"), timeout=15)
    v.entry["edit"]["details"]["clarity"] = 40
    FakeDialog.options_override = None
    window.exporter.start([v], window, dialog_factory=FakeDialog, ask=lambda t: (ep.CONFLICT_NUMBER, True))
    queue = window.exporter.queue
    state = next(iter(queue.states.values()))
    assert wait_until(lambda: state.progress > 0.05, timeout=30)
    queue.pause()
    held = state.progress
    wait_until(lambda: False, 0.6)
    assert state.status == "running" and state.progress - held < 0.05  # held at a checkpoint
    queue.resume()
    assert wait_until(lambda: state.progress > held, timeout=30)
    queue.cancel(state.id)
    assert wait_until(lambda: state.status == "cancelled", timeout=30)
    assert not list(folder.glob("*.part*")) and not (folder / "v_edited.mp4").exists()
    assert v.entry["export_history"] == []  # cancelled exports are not recorded as results


def test_failed_export_is_listed_with_log(window, tmp_path, fake_trash, monkeypatch):
    folder = tmp_path / "Dive"
    make_jpeg(folder / "a.jpg")
    project = open_folder(window, folder)
    a = project.item_by_path("a.jpg")
    monkeypatch.setattr(ex, "check_free_space", lambda d, n: (_ for _ in ()).throw(ex.ExportError("export.error.disk_space", "full")))
    run_export(window, [a])
    state = next(iter(window.exporter.queue.states.values()))
    assert state.status == "failed" and state.error_key == "export.error.disk_space"
    assert "a.jpg" in window.queue_panel.error_log() and window.queue_panel.copy_log_button.isEnabled()
    assert a.entry["export_history"][-1]["result"] == "failed"
    assert a.status == "unedited"  # a failed export does not count as exported


def test_read_only_project_cannot_export(window, tmp_path, monkeypatch):
    folder = tmp_path / "Dive"
    make_jpeg(folder / "a.jpg")
    project = open_folder(window, folder)
    project.read_only = True
    shown = []
    monkeypatch.setattr("reefhue.ui.export_controller.QMessageBox.information", lambda *a: shown.append(a))
    assert not window.exporter.start([project.item_by_path("a.jpg")], window, dialog_factory=FakeDialog)
    assert shown


def test_export_dialog_rules(window, tmp_path):
    from PySide6.QtWidgets import QDialogButtonBox

    from reefhue.ui.export_dialog import ExportDialog

    folder = tmp_path / "Dive"
    make_jpeg(folder / "a.jpg")
    (folder / "v.mp4").write_bytes(b"\0" * 16)
    project = open_folder(window, folder)
    items = [project.item_by_path("a.jpg"), project.item_by_path("v.mp4")]
    dialog = ExportDialog(window.ctx, items, ep.ExportOptions.from_defaults({"mode": "overwrite"}), window)
    assert dialog.mode == "suffix"  # overwrite is never preselected
    assert not dialog.video_note.isHidden()  # no FFmpeg status → videos cannot be exported
    ok = dialog.buttons.button(QDialogButtonBox.Ok)
    dialog.suffix_edit.setText("bad/name")
    assert not ok.isEnabled()
    dialog.suffix_edit.setText("_x")
    assert ok.isEnabled()
    dialog.overwrite_radio.setChecked(True)
    assert not dialog.overwrite_note.isHidden()
    assert dialog.options().mode == "overwrite"
    dialog.deleteLater()


def test_remaining_time_format():
    from reefhue.ui.export_queue import format_remaining

    assert format_remaining(None) == tr("queue.remaining_unknown")
    assert format_remaining(42) == tr("queue.remaining_seconds", n=42)
    assert format_remaining(125) == tr("queue.remaining_minutes", m=2, s="05")
    assert format_remaining(3725) == tr("queue.remaining_hours", h=1, m="02")


def test_capture_override_reaches_export(window, tmp_path, fake_trash):
    import piexif

    folder = tmp_path / "Dive"
    make_jpeg(folder / "a.jpg")
    project = open_folder(window, folder)
    a = project.item_by_path("a.jpg")
    window.controller.set_capture_override(a.id, datetime(2021, 3, 4, 5, 6, 7))
    run_export(window, [a])
    data = piexif.load(str(folder / "a_edited.jpg"))
    assert data["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2021:03:04 05:06:07"
