"""Main window: 1/3 file pane | 2/3 viewer pane, menu bar, startup notices."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import QDockWidget, QFileDialog, QMainWindow, QMenu, QMessageBox, QSplitter, QWidget

from reefhue import i18n
from reefhue.core import app_settings as schema
from reefhue.core import paths
from reefhue.i18n import tr
from reefhue.io.ffmpeg import FfmpegStatus
from reefhue.core.project import FileItem
from reefhue.ui import capture_date_dialog
from reefhue.ui.about_dialog import AboutDialog
from reefhue.ui.capture_date_dialog import CaptureDateDialog
from reefhue.ui.context import AppContext
from reefhue.ui.editor_controller import EditorController, is_editable
from reefhue.ui.export_controller import ExportController
from reefhue.ui.export_queue import QueuePanel
from reefhue.ui.ffmpeg_dialog import FfmpegDialog
from reefhue.ui.file_pane import FilePane
from reefhue.ui.preview_engine import PreviewEngine
from reefhue.ui.project_controller import ProjectController
from reefhue.ui.sections_dialog import SectionsDialog
from reefhue.ui.settings_dialog import SettingsDialog
from reefhue.ui.thumbnail_service import ThumbnailService
from reefhue.ui.time_shift_dialog import TimeShiftDialog
from reefhue.ui.util import open_in_file_manager, plain_menu_text, reveal_in_file_manager
from reefhue.ui.viewer import ViewerPane

log = logging.getLogger(__name__)

GEOMETRY_KEY = "window/geometry"
STATE_KEY = "window/state"
MAIN_SPLITTER_KEY = "window/main_splitter"
VIEWER_SPLITTER_KEY = "window/viewer_splitter"


def dropped_folder(mime) -> Path | None:
    """The single local folder in a drag payload, else ``None``."""
    if not mime.hasUrls():
        return None
    urls = [u for u in mime.urls() if u.isLocalFile()]
    if len(urls) != 1:
        return None
    path = Path(urls[0].toLocalFile())
    return path if path.is_dir() else None


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setAcceptDrops(True)
        self.setMinimumSize(900, 600)
        self._ffmpeg_dialog: FfmpegDialog | None = None

        self.thumbs = ThumbnailService(ctx, self)
        self.controller = ProjectController(ctx, self)
        self.engine = PreviewEngine(self)
        self.editor = EditorController(ctx, self.controller, self.engine, self)
        self.file_pane = FilePane(ctx, self.controller, self.thumbs)
        self.viewer = ViewerPane(ctx, self.thumbs, self.editor, self.engine)
        self.viewer.edit_panel.target_items = self.target_items
        self.exporter = ExportController(ctx, self.controller, self.editor, self)
        self.queue_panel = QueuePanel(ctx, self.exporter.queue)
        self.queue_dock = QDockWidget(self)
        self.queue_dock.setObjectName("ExportQueueDock")
        self.queue_dock.setWidget(self.queue_panel)
        self.queue_dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.queue_dock)
        self.queue_dock.hide()
        self.exporter.queue_started.connect(self._show_queue)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setObjectName("MainSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.file_pane)
        self.splitter.addWidget(self.viewer)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 2)
        self.setCentralWidget(self.splitter)

        self.file_pane.open_folder_requested.connect(self.choose_folder)
        self.viewer.preview.empty_state.open_folder_requested.connect(self.choose_folder)
        self.viewer.preview.banner.help_requested.connect(self.show_ffmpeg_help)
        self.file_pane.current_item_changed.connect(self._on_current_item)
        self.editor.history_changed.connect(self._update_edit_actions)
        self.editor.clipboard_changed.connect(self._update_edit_actions)
        self.editor.item_changed.connect(self._update_edit_actions)
        self.file_pane.change_capture_requested.connect(self.change_capture_date)
        self.file_pane.shift_timeline_requested.connect(self.shift_timeline)
        self.file_pane.reveal_requested.connect(self._reveal)
        self.controller.project_changed.connect(self._on_project_changed)
        self.controller.folder_failed.connect(self._on_folder_failed)
        ctx.ffmpeg_status_changed.connect(self._on_ffmpeg_status)

        self._build_actions()
        self._build_menus()
        self._restore_state()

        ctx.language_changed.connect(self.retranslate_ui)
        ctx.folder_changed.connect(self._update_title)
        self.retranslate_ui()

    # ------------------------------------------------------------------ actions
    def _action(self, key: str, slot=None, shortcut=None, enabled: bool = True, checkable: bool = False):
        action = QAction(self)
        action.setProperty("tr_key", key)
        if shortcut is not None:
            if isinstance(shortcut, (list, tuple)):
                sequences = {}
                for s in shortcut:  # platform defaults may repeat an explicit key (e.g. F5)
                    seq = QKeySequence(s)
                    if not seq.isEmpty():
                        sequences.setdefault(seq.toString(), seq)
                action.setShortcuts(list(sequences.values()))
            else:
                action.setShortcut(QKeySequence(shortcut))
        action.setCheckable(checkable)
        action.setEnabled(enabled)
        if slot is not None:
            action.triggered.connect(slot)
        self._actions[key] = action
        return action

    def _build_actions(self) -> None:
        self._actions: dict[str, QAction] = {}
        a = self._action
        a("action.open_folder", self.choose_folder, QKeySequence.Open)
        refresh = a("action.refresh", self.controller.refresh, ("F5", QKeySequence(QKeySequence.Refresh)), enabled=False)
        self.addAction(refresh)  # global F5, not in a menu (SPEC §4 menu layout)
        # Later milestones: export, editing, overlays (shown disabled so the menu layout is final).
        a("action.export", self.export_selection, enabled=False)
        a("action.export_all_edited", self.export_all_edited, enabled=False)
        settings = a("action.settings", self.show_settings, QKeySequence.Preferences)
        settings.setMenuRole(QAction.PreferencesRole)
        quit_action = a("action.quit", self.close, QKeySequence.Quit)
        quit_action.setMenuRole(QAction.QuitRole)
        a("action.undo", self.editor.undo, QKeySequence.Undo, enabled=False)
        a("action.redo", self.editor.redo, ("Ctrl+Shift+Z", QKeySequence(QKeySequence.Redo)), enabled=False)
        a("action.copy_settings", self.copy_settings, "Ctrl+Shift+C", enabled=False)
        a("action.paste_settings", self.paste_settings, "Ctrl+Shift+V", enabled=False)
        a("action.reset", self.reset_edits, enabled=False)
        toggle = a("action.toggle_edit_panel", self._toggle_edit_panel, checkable=True)
        toggle.setChecked(self.ctx.settings.get("ui/edit_panel_visible"))
        hist = a("action.toggle_histogram", self._toggle_histogram, checkable=True)
        hist.setChecked(self.ctx.settings.get("ui/histogram_visible"))
        curves = a("action.toggle_curves", self._toggle_curves, checkable=True)
        curves.setChecked(self.ctx.settings.get("ui/curves_visible"))
        self.viewer.edit_panel.set_histogram_visible(hist.isChecked())
        self.viewer.edit_panel.set_curves_visible(curves.isChecked())
        self.file_pane.edit_actions = {k: self._actions[k] for k in
                                       ("action.copy_settings", "action.paste_settings", "action.reset",
                                        "action.export")}
        info = a("action.info_overlay", self._toggle_info, "Ctrl+I", checkable=True)
        info.setChecked(self.ctx.settings.get("ui/info_overlay"))
        self.viewer.preview.set_info_visible(info.isChecked())
        about = a("action.about", self.show_about)
        about.setMenuRole(QAction.AboutRole)
        a("action.open_log_folder", lambda: open_in_file_manager(paths.log_dir()))
        a("action.ffmpeg_help", self.show_ffmpeg_help)
        self.viewer.set_edit_panel_wanted(toggle.isChecked())

    def _build_menus(self) -> None:
        bar = self.menuBar()
        act = self._actions
        self.menu_file = bar.addMenu("")
        self.menu_file.addAction(act["action.open_folder"])
        self.menu_recent = self.menu_file.addMenu("")
        self.menu_recent.aboutToShow.connect(self._rebuild_recent_menu)
        self.menu_file.addSeparator()
        self.menu_file.addAction(act["action.export"])
        self.menu_file.addAction(act["action.export_all_edited"])
        self.menu_file.addSeparator()
        self.menu_file.addAction(act["action.settings"])
        self.menu_file.addSeparator()
        self.menu_file.addAction(act["action.quit"])

        self.menu_edit = bar.addMenu("")
        edit_items = ("action.undo", "action.redo", None, "action.copy_settings", "action.paste_settings", None, "action.reset")
        for key in edit_items:
            if key is None:
                self.menu_edit.addSeparator()
            else:
                self.menu_edit.addAction(act[key])

        self.menu_view = bar.addMenu("")
        self.menu_theme = self.menu_view.addMenu("")
        self.theme_group = QActionGroup(self)
        self.theme_actions: dict[str, QAction] = {}
        for choice in schema.THEME_CHOICES:
            action = QAction(self, checkable=True)
            action.setData(choice)
            action.triggered.connect(lambda _=False, c=choice: self.ctx.set_theme_choice(c))
            self.theme_group.addAction(action)
            self.menu_theme.addAction(action)
            self.theme_actions[choice] = action
        self.menu_language = self.menu_view.addMenu("")
        self.language_group = QActionGroup(self)
        self.language_actions: dict[str, QAction] = {}
        for code in [schema.LANGUAGE_SYSTEM, *i18n.available_languages()]:
            action = QAction(self, checkable=True)
            action.setData(code)
            action.triggered.connect(lambda _=False, c=code: self.ctx.set_language_choice(c))
            self.language_group.addAction(action)
            self.menu_language.addAction(action)
            self.language_actions[code] = action
            if code == schema.LANGUAGE_SYSTEM:
                self.menu_language.addSeparator()
        self.menu_view.addSeparator()
        for key in ("action.toggle_edit_panel", "action.toggle_histogram", "action.toggle_curves", "action.info_overlay"):
            self.menu_view.addAction(act[key])

        self.menu_help = bar.addMenu("")
        self.menu_help.addAction(act["action.about"])
        self.menu_help.addAction(act["action.open_log_folder"])
        self.menu_help.addAction(act["action.ffmpeg_help"])

    def _rebuild_recent_menu(self) -> None:
        menu = self.menu_recent
        menu.clear()
        folders = self.ctx.settings.recent_folders()
        if not folders:
            empty = menu.addAction(tr("recent.empty"))
            empty.setEnabled(False)
            return
        for folder in folders:
            action = menu.addAction(folder)
            action.triggered.connect(lambda _=False, f=folder: self.open_folder(Path(f)))
        menu.addSeparator()
        clear = menu.addAction(tr("action.clear_recent"))
        clear.triggered.connect(lambda: self.ctx.settings.set_recent_folders([]))

    # ------------------------------------------------------------------ i18n
    def retranslate_ui(self, *_args) -> None:
        for key, action in self._actions.items():
            action.setText(tr(key))
        self.queue_dock.setWindowTitle(tr("queue.title"))
        self.menu_file.setTitle(tr("menu.file"))
        self.menu_recent.setTitle(tr("menu.open_recent"))
        self.menu_edit.setTitle(tr("menu.edit"))
        self.menu_view.setTitle(tr("menu.view"))
        self.menu_theme.setTitle(tr("menu.theme"))
        self.menu_language.setTitle(tr("menu.language"))
        self.menu_help.setTitle(tr("menu.help"))
        for choice, action in self.theme_actions.items():
            action.setText(tr(f"theme.{choice}"))
        names = i18n.available_languages()
        for code, action in self.language_actions.items():
            action.setText(tr("language.system") if code == schema.LANGUAGE_SYSTEM else names[code])
        self._sync_checked_choices()
        self._rebuild_recent_menu()
        self._update_title()

    def _sync_checked_choices(self) -> None:
        theme = self.ctx.settings.get("ui/theme")
        language = self.ctx.settings.get("ui/language")
        if theme in self.theme_actions:
            self.theme_actions[theme].setChecked(True)
        self.language_actions.get(language, self.language_actions[schema.LANGUAGE_SYSTEM]).setChecked(True)

    def _update_title(self, *_args) -> None:
        folder = self.ctx.current_folder
        if folder is None:
            self.setWindowTitle(tr("app.name"))
        else:
            self.setWindowTitle(tr("window.title_with_folder", folder=folder.name or str(folder)))

    # ------------------------------------------------------------------ folders
    def choose_folder(self) -> None:
        start = str(self.ctx.current_folder or Path.home())
        chosen = QFileDialog.getExistingDirectory(self, plain_menu_text(tr("action.open_folder")), start)
        if chosen:
            self.open_folder(Path(chosen))

    def open_folder(self, folder: Path) -> bool:
        """Make ``folder`` the current project folder. Read-only: nothing is written into it."""
        if not folder.is_dir():
            QMessageBox.warning(self, tr("app.name"), tr("error.folder_missing", path=str(folder)))
            self.ctx.settings.remove_recent_folder(str(folder))
            return False
        folder = folder.resolve()
        log.info("open folder %s", folder)
        self.controller.open(folder)
        self.ctx.set_current_folder(folder)
        return True

    def _on_folder_failed(self, folder: Path, key: str) -> None:
        """The folder could not be opened, or it vanished / became unreadable while open."""
        if self.ctx.current_folder == folder:
            self.ctx.set_current_folder(None)
        if key == "error.folder_missing":
            self.ctx.settings.remove_recent_folder(str(folder))
        QMessageBox.warning(self, tr("app.name"), tr(key, path=str(folder)))

    def _on_project_changed(self, project) -> None:
        for key in ("action.refresh", "action.export", "action.export_all_edited"):
            self._actions[key].setEnabled(project is not None)

    # ------------------------------------------------------------------ export
    def export_selection(self) -> None:
        items = self.file_pane.selected_items() or ([self.editor.item] if self.editor.item else [])
        self.exporter.start(items, self)

    def export_all_edited(self) -> None:
        self.exporter.start(self.exporter.pending_items(), self)

    def _show_queue(self) -> None:
        self.queue_dock.show()
        self.queue_dock.raise_()

    def _on_ffmpeg_status(self, _status) -> None:
        # FFmpeg may have become available: probe videos / make video thumbnails now.
        if self.controller.project is not None:
            self.controller.refresh()
            self.file_pane.model.refresh_all()
            current = self.file_pane.current_item()
            if current is not None and current.kind == "video" and self.editor.item is None:
                self._on_current_item(current)  # videos become editable once FFmpeg is there

    # ------------------------------------------------------------------ capture dates
    def change_capture_date(self, item: FileItem) -> None:
        dialog = CaptureDateDialog(item, self)
        if dialog.exec() == CaptureDateDialog.Accepted:
            if dialog.action == capture_date_dialog.RESET:
                self.controller.reset_capture([item.id])
            elif dialog.action == capture_date_dialog.SET:
                self.controller.set_capture_override(item.id, dialog.value)
        dialog.deleteLater()

    def shift_timeline(self, items: list) -> None:
        current = self.file_pane.current_item()
        dialog = TimeShiftDialog(items, current.id if current else None, self)
        if dialog.exec() == TimeShiftDialog.Accepted:
            groups, base = dialog.result_groups()
            self.controller.apply_time_shift(groups, base)
        dialog.deleteLater()

    def _reveal(self, item: FileItem) -> None:
        if self.controller.project is not None and item is not None:
            reveal_in_file_manager(self.controller.project.folder / item.rel_path)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 (Qt API)
        if dropped_folder(event.mimeData()) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 (Qt API)
        folder = dropped_folder(event.mimeData())
        if folder is not None:
            event.acceptProposedAction()
            self.open_folder(folder)

    # ------------------------------------------------------------------ view
    def _toggle_info(self, checked: bool) -> None:
        self.ctx.settings.set("ui/info_overlay", checked)
        self.viewer.preview.set_info_visible(checked)

    def _toggle_histogram(self, checked: bool) -> None:
        self.ctx.settings.set("ui/histogram_visible", checked)
        self.viewer.edit_panel.set_histogram_visible(checked)

    def _toggle_curves(self, checked: bool) -> None:
        self.ctx.settings.set("ui/curves_visible", checked)
        self.viewer.edit_panel.set_curves_visible(checked)

    # ------------------------------------------------------------------ editing
    def _on_current_item(self, item) -> None:
        self.editor.set_item(item)
        self.viewer.preview.show_item(item)
        nxt = self.file_pane.neighbor_item(1)
        if nxt is not None and nxt.kind == "photo" and is_editable(nxt) and self.controller.project is not None:
            self.engine.preload(self.controller.project.folder / nxt.rel_path,
                                nxt.scanned.mtime if nxt.scanned else 0.0)

    def target_items(self) -> list:
        """Files a paste / preset / reset applies to: the selection, else the current file."""
        items = [i for i in self.file_pane.selected_items() if is_editable(i, self.ctx.video_enabled)]
        if not items and self.editor.item is not None:
            items = [self.editor.item]
        return items

    def _update_edit_actions(self, *_args) -> None:
        has_item = self.editor.item is not None
        self._actions["action.undo"].setEnabled(self.editor.can_undo)
        self._actions["action.redo"].setEnabled(self.editor.can_redo)
        self._actions["action.copy_settings"].setEnabled(has_item)
        self._actions["action.paste_settings"].setEnabled(self.editor.clipboard is not None and has_item)
        self._actions["action.reset"].setEnabled(has_item)

    def copy_settings(self) -> None:
        self.editor.copy_settings()

    def paste_settings(self) -> None:
        if self.editor.clipboard is None:
            return
        targets = self.target_items()
        dialog = SectionsDialog(plain_menu_text(tr("action.paste_settings")),
                                tr("paste.intro", count=len(targets)), ok_text=tr("paste.apply"), parent=self)
        if dialog.exec() == SectionsDialog.Accepted:
            self.editor.apply_partial(targets, self.editor.clipboard, dialog.selected())
        dialog.deleteLater()

    def reset_edits(self) -> None:
        self.editor.reset_items(self.target_items())

    def _toggle_edit_panel(self, checked: bool) -> None:
        self.ctx.settings.set("ui/edit_panel_visible", checked)
        self.viewer.set_edit_panel_wanted(checked)

    # ------------------------------------------------------------------ dialogs
    def show_settings(self) -> None:
        dialog = SettingsDialog(self.ctx, self)
        dialog.exec()
        dialog.deleteLater()
        self._sync_checked_choices()
        self.controller.set_auto_refresh(self.ctx.settings.get("files/auto_refresh"))
        self.file_pane.size_slider.setValue(self.ctx.settings.get("thumbs/size"))
        self.viewer.preview.rerender()  # preview quality may have changed

    def show_about(self) -> None:
        dialog = AboutDialog(self.ctx, self)
        dialog.exec()
        dialog.deleteLater()

    def show_ffmpeg_help(self) -> None:
        if self._ffmpeg_dialog is None:
            self._ffmpeg_dialog = FfmpegDialog(self.ctx, self)
        self._ffmpeg_dialog.show()
        self._ffmpeg_dialog.raise_()
        self._ffmpeg_dialog.activateWindow()

    def apply_ffmpeg_status(self, status: FfmpegStatus, notify: bool = True) -> None:
        """Called with the result of the background FFmpeg check."""
        self.ctx.set_ffmpeg_status(status)
        if notify and (not status.available or status.missing_encoders):
            self.show_ffmpeg_help()  # non-modal: the app stays usable (Rosetta only gets the banner)

    # ------------------------------------------------------------------ persistence
    def _restore_state(self) -> None:
        s = self.ctx.settings
        geometry = s.get_bytes(GEOMETRY_KEY)
        if geometry is None or not self.restoreGeometry(geometry):
            self.resize(1400, 860)
        state = s.get_bytes(STATE_KEY)
        if state is not None:
            self.restoreState(state)
        main = s.get_bytes(MAIN_SPLITTER_KEY)
        if main is None or not self.splitter.restoreState(main):
            width = self.width()
            self.splitter.setSizes([width // 3, width - width // 3])
        viewer = s.get_bytes(VIEWER_SPLITTER_KEY)
        if viewer is None or not self.viewer.splitter.restoreState(viewer):
            height = self.height()
            self.viewer.splitter.setSizes([height * 2 // 3, height // 3])

    def save_state(self) -> None:
        s = self.ctx.settings
        s.set_bytes(GEOMETRY_KEY, self.saveGeometry())
        s.set_bytes(STATE_KEY, self.saveState())
        s.set_bytes(MAIN_SPLITTER_KEY, self.splitter.saveState())
        s.set_bytes(VIEWER_SPLITTER_KEY, self.viewer.splitter.saveState())
        s.sync()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt API)
        queue = self.exporter.queue
        if queue.busy:
            box = QMessageBox(QMessageBox.Warning, tr("app.name"), tr("queue.quit_confirm"), parent=self)
            stop = box.addButton(tr("queue.quit_cancel_exports"), QMessageBox.DestructiveRole)
            box.addButton(tr("common.cancel"), QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not stop:
                event.ignore()
                return
            queue.cancel_all()
            queue.wait(30000)  # running jobs clean up their .part files
        self.file_pane.save_view_state()
        self.editor.set_item(None)
        self.controller.close()
        self.save_state()
        super().closeEvent(event)
