"""Milestone 2 UI: file pane, filters, sorting, capture dates, sidecar persistence."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from PySide6.QtCore import Qt

from reefhue.core import sidecar
from reefhue.i18n import tr
from reefhue.ui import file_model as fm
from tests.conftest import wait_until
from tests.media import make_jpeg, make_png


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "Dive"
    make_jpeg(root / "IMG_0002.jpg", dt="2024:05:01 10:00:00")
    make_jpeg(root / "IMG_0001.jpg", dt="2024:05:01 11:00:00")
    make_jpeg(root / "GOPR0003.JPG", dt="2016:01:01 00:00:05")
    make_png(root / "notes.png")
    (root / "scan.raw").write_bytes(b"raw")
    make_jpeg(root / "sub" / "deep.jpg", dt="2024:05:02 09:00:00")
    return root


def open_and_wait(window, path):
    assert window.open_folder(path)
    ctl = window.controller
    assert wait_until(lambda: ctl.project is not None and not ctl.busy, timeout=10)
    return ctl.project


def names_in_view(window) -> list[str]:
    proxy = window.file_pane.proxy
    return [proxy.index(r, fm.NAME).data() for r in range(proxy.rowCount())]


def test_open_lists_files_sorted_by_capture_date(window, folder):
    open_and_wait(window, folder)
    assert names_in_view(window) == ["GOPR0003.JPG", "IMG_0002.jpg", "IMG_0001.jpg", "notes.png"]
    assert window.file_pane.footer.text() == tr("files.count", shown=4, total=4, selected=0)
    assert not (folder / sidecar.SIDECAR_NAME).exists()  # opening alone writes nothing


def test_extension_filter_field_and_popup_stay_in_sync(window, folder):
    open_and_wait(window, folder)
    pane = window.file_pane
    pane.ext_edit.setText("jpg, RAW")
    pane._on_ext_entered()
    assert pane.ext_edit.text() == "jpg, raw"
    assert sorted(names_in_view(window)) == ["GOPR0003.JPG", "IMG_0001.jpg", "IMG_0002.jpg", "scan.raw"]
    assert pane.popup.photos.boxes["jpg"].isChecked()
    assert not pane.popup.photos.boxes["png"].isChecked()
    assert pane.popup.photos.extra.text() == "raw"
    pane.popup.photos.boxes["png"].setChecked(True)
    assert "png" in pane.ext_edit.text()
    assert "notes.png" in names_in_view(window)
    assert window.controller.project.extension_filter == ["jpg", "png", "raw"]


def test_type_status_and_search_filters(window, folder):
    open_and_wait(window, folder)
    pane = window.file_pane
    pane.search_edit.setText("img_")
    assert sorted(names_in_view(window)) == ["IMG_0001.jpg", "IMG_0002.jpg"]
    pane.search_edit.setText("")
    pane.status_combo.setCurrentIndex(pane.status_combo.findData("edited"))
    assert names_in_view(window) == []
    pane.status_combo.setCurrentIndex(0)
    pane.type_combo.setCurrentIndex(pane.type_combo.findData("video"))
    assert names_in_view(window) == []


def test_recursive_and_subfolder_column(window, folder):
    open_and_wait(window, folder)
    pane = window.file_pane
    assert pane.table.isColumnHidden(fm.SUBFOLDER)
    pane.recursive_box.setChecked(True)
    assert wait_until(lambda: "deep.jpg" in names_in_view(window) and not window.controller.busy)
    assert not pane.table.isColumnHidden(fm.SUBFOLDER)


def test_multi_column_sort_with_shift(window, folder):
    open_and_wait(window, folder)
    proxy = window.file_pane.proxy
    proxy.click_column(fm.TYPE, shift=False)
    proxy.click_column(fm.NAME, shift=True)
    proxy.click_column(fm.NAME, shift=True)  # secondary key descending
    assert proxy.sort_keys == [(fm.TYPE, Qt.AscendingOrder), (fm.NAME, Qt.DescendingOrder)]
    assert names_in_view(window) == ["notes.png", "IMG_0002.jpg", "IMG_0001.jpg", "GOPR0003.JPG"]
    proxy.click_column(fm.TYPE, shift=False)  # primary toggles
    assert proxy.sort_keys[0] == (fm.TYPE, Qt.DescendingOrder)


def test_capture_override_italic_marker_and_autosave(window, folder):
    project = open_and_wait(window, folder)
    item = project.item_by_path("IMG_0001.jpg")
    window.controller.set_capture_override(item.id, datetime(2024, 5, 1, 9, 0, 0))
    assert names_in_view(window)[1] == "IMG_0001.jpg"  # re-sorted by new date
    model = window.file_pane.model
    index = model.index(model.row_of(item.id), fm.CAPTURE)
    assert model.data(index).endswith(fm.OVERRIDE_MARKER)
    assert model.data(index, Qt.FontRole).italic()
    path = folder / sidecar.SIDECAR_NAME
    assert wait_until(lambda: path.exists(), timeout=5)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["files"][0]["capture"]["datetime_override"] == "2024-05-01T09:00:00"


def test_reopening_restores_settings(window, folder, tmp_path):
    project = open_and_wait(window, folder)
    pane = window.file_pane
    pane.recursive_box.setChecked(True)
    pane.hide_exported_box.setChecked(False)
    pane.ext_edit.setText("jpg")
    pane._on_ext_entered()
    a = project.item_by_path("IMG_0002.jpg")
    b = project.item_by_path("IMG_0001.jpg")
    assert wait_until(lambda: not window.controller.busy)
    from reefhue.core.project import ShiftGroup

    window.controller.apply_time_shift([ShiftGroup(a.id, datetime(2025, 1, 1, 8, 0, 0), [a.id, b.id])])
    other = tmp_path / "Other"
    other.mkdir()
    open_and_wait(window, other)  # switching folders flushes the save
    reopened = open_and_wait(window, folder)
    assert pane.recursive_box.isChecked() and reopened.recursive
    assert not pane.hide_exported_box.isChecked()
    assert pane.ext_edit.text() == "jpg"
    assert reopened.items[b.id].capture_datetime == datetime(2025, 1, 1, 9, 0, 0)
    assert "deep.jpg" in names_in_view(window)


def test_viewer_shows_selected_file_and_cannot_display(window, folder):
    project = open_and_wait(window, folder)
    window.controller.set_extension_filter(["jpg", "raw"])
    preview = window.viewer.preview
    preview.show_item(project.item_by_path("scan.raw"))
    assert preview.stack.currentIndex() == preview.PAGE_FILE
    assert preview.file_view.notice.text() == tr("viewer.cannot_display", ext="RAW")
    preview.show_item(project.item_by_path("IMG_0001.jpg"))
    assert preview.stack.currentIndex() == preview.PAGE_CANVAS  # photos open in the editor canvas
    photo = project.item_by_path("IMG_0001.jpg")
    preview.file_view.set_item(photo)
    assert preview.file_view.notice.isHidden()
    assert "2024-05-01 11:00:00" in preview.file_view.details.text()


def test_thumbnails_are_generated_in_background(window, folder):
    project = open_and_wait(window, folder)
    item = project.item_by_path("IMG_0001.jpg")
    assert window.thumbs.get(item) is None  # first call schedules the job
    assert wait_until(lambda: window.thumbs.get(item) is not None, timeout=10)


def test_grid_mode_shares_selection(window, folder):
    open_and_wait(window, folder)
    pane = window.file_pane
    pane.table.selectRow(0)
    pane.set_view_mode("grid")
    assert pane.views.currentWidget() is pane.grid
    assert len(pane.grid.selectionModel().selectedRows()) == 1
    assert window.ctx.settings.get("files/view_mode") == "grid"


def test_folder_locked_by_another_process_opens_read_only(ctx, folder):
    import os
    import socket

    from reefhue.core.lock import FolderLock
    from reefhue.ui.project_controller import ProjectController

    # Another live ReefHue process (simulated by our parent pid) holds the lock.
    FolderLock(folder).path.write_text(json.dumps({"pid": os.getppid(), "host": socket.gethostname()}))
    controller = ProjectController(ctx)
    controller.open(folder)
    assert wait_until(lambda: controller.project is not None and not controller.busy)
    assert controller.project.read_only
    assert "sidecar.warn.locked" in controller.warnings()
    controller.set_recursive(True)
    controller.flush()
    assert not (folder / sidecar.SIDECAR_NAME).exists()  # read-only: nothing written
    controller.close()


def test_time_shift_dialog_split_and_preview(window, tmp_path):
    root = tmp_path / "GoPro"
    make_jpeg(root / "GOPR0001.JPG", dt="2026:05:01 10:00:00")
    make_jpeg(root / "GOPR0002.JPG", dt="2026:05:01 10:30:00")
    make_jpeg(root / "GOPR0003.JPG", dt="1970:01:01 00:01:00")
    make_jpeg(root / "GOPR0004.JPG", dt="1970:01:01 00:11:00")
    project = open_and_wait(window, root)
    items = [project.item_by_path(f"GOPR000{i}.JPG") for i in range(1, 5)]
    from reefhue.ui.time_shift_dialog import TimeShiftDialog
    from reefhue.ui.util import to_qdatetime

    dialog = TimeShiftDialog(items, items[2].id, window)
    assert dialog.warning.isVisible() or dialog.warning.text()
    assert dialog.can_split and dialog.split_button.text() == tr("shift.split")
    dialog._toggle_split()
    assert len(dialog.groups) == 2
    dialog.group_combo.setCurrentIndex(1)
    assert dialog.anchors[1] == items[2].id
    dialog.target_edit.setDateTime(to_qdatetime(datetime(2026, 5, 1, 11, 0, 0)))
    preview = dialog.preview()
    assert preview[items[2].id] == datetime(2026, 5, 1, 11, 0, 0)
    assert preview[items[3].id] == datetime(2026, 5, 1, 11, 10, 0)
    assert preview[items[0].id] == datetime(2026, 5, 1, 10, 0, 0)  # group 1 unchanged
    groups, base = dialog.result_groups()
    window.controller.apply_time_shift(groups, base)
    assert project.items[items[3].id].capture_datetime == datetime(2026, 5, 1, 11, 10, 0)
    assert len(project.settings["time_groups"]) == 2
    dialog.deleteLater()


def test_capture_date_dialog(window, folder):
    project = open_and_wait(window, folder)
    from reefhue.ui.capture_date_dialog import CaptureDateDialog

    item = project.item_by_path("IMG_0001.jpg")
    dialog = CaptureDateDialog(item, window)
    assert not dialog.reset_button.isEnabled()
    assert dialog.value == datetime(2024, 5, 1, 11, 0, 0)
    assert "EXIF" in dialog.original_label.text()
    dialog.deleteLater()
