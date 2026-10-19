"""Left pane: toolbar, filters, list/grid of the folder contents (SPEC §5)."""

from __future__ import annotations

import html
import json
import logging

from PySide6.QtCore import QItemSelectionModel, QSize, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QSlider,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from reefhue.core import extensions
from reefhue.core import status as st
from reefhue.core.project import FileItem, Project
from reefhue.i18n import tr
from reefhue.ui import file_model as fm
from reefhue.ui.context import AppContext
from reefhue.ui.extension_filter_popup import ExtensionFilterPopup
from reefhue.ui.icons import icon
from reefhue.ui.project_controller import ProjectController
from reefhue.ui.thumbnail_service import ThumbnailService

log = logging.getLogger(__name__)

HEADER_STATE_KEY = "files/header_state"
DEFAULT_HIDDEN_COLUMNS = (fm.EXT, fm.CREATED, fm.SUBFOLDER)
LIST_THUMB_RANGE = (24, 160)


class FilePane(QWidget):
    open_folder_requested = Signal()
    current_item_changed = Signal(object)  # FileItem | None
    change_capture_requested = Signal(object)  # FileItem
    shift_timeline_requested = Signal(list)  # list[FileItem]
    reveal_requested = Signal(object)  # FileItem

    def __init__(self, ctx: AppContext, controller: ProjectController, thumbs: ThumbnailService,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.controller = controller
        self.thumbs = thumbs
        self.setObjectName("FilePane")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._syncing = False
        self.edit_actions: dict = {}  # set by the main window (shared QActions)

        # ---- toolbar
        self.open_button = self._tool_button("folder-open")
        self.open_button.clicked.connect(self.open_folder_requested)
        self.refresh_button = self._tool_button("refresh")
        self.refresh_button.clicked.connect(controller.refresh)
        self.list_button = self._tool_button("list", checkable=True)
        self.grid_button = self._tool_button("grid", checkable=True)
        self.list_button.clicked.connect(lambda: self.set_view_mode("list"))
        self.grid_button.clicked.connect(lambda: self.set_view_mode("grid"))
        self.size_slider = QSlider(Qt.Horizontal)
        spec_min, spec_max = 64, 320
        self.size_slider.setRange(spec_min, spec_max)
        self.size_slider.setSingleStep(8)
        self.size_slider.setPageStep(32)
        self.size_slider.setValue(ctx.settings.get("thumbs/size"))
        self.size_slider.setMaximumWidth(120)
        self.size_slider.valueChanged.connect(self._on_size_changed)
        self.filter_button = self._tool_button("filter", checkable=True)
        self.filter_button.setChecked(ctx.settings.get("files/filters_visible"))
        self.filter_button.toggled.connect(self._on_filters_toggled)
        # Placeholder for "Validate filenames / Batch rename" – always disabled in v1 (SPEC §5).
        self.rename_button = self._tool_button("rename")
        self.rename_button.setEnabled(False)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 8, 8, 2)
        toolbar.setSpacing(2)
        for widget in (self.open_button, self.refresh_button):
            toolbar.addWidget(widget)
        toolbar.addSpacing(8)
        for widget in (self.list_button, self.grid_button, self.size_slider):
            toolbar.addWidget(widget)
        toolbar.addStretch(1)
        toolbar.addWidget(self.filter_button)
        toolbar.addWidget(self.rename_button)

        self.recursive_box = QCheckBox()
        self.recursive_box.toggled.connect(self._on_recursive)
        self.hide_exported_box = QCheckBox()
        self.hide_exported_box.toggled.connect(self._on_hide_exported)
        options = QHBoxLayout()
        options.setContentsMargins(12, 2, 12, 2)
        options.setSpacing(18)
        options.addWidget(self.recursive_box)
        options.addWidget(self.hide_exported_box)
        options.addStretch(1)

        # ---- filters
        self.ext_edit = QLineEdit()
        self.ext_edit.returnPressed.connect(self._on_ext_entered)
        self.ext_popup_button = self._tool_button("sliders")
        self.ext_popup_button.clicked.connect(self._show_popup)
        self.popup = ExtensionFilterPopup(self)
        self.popup.extensions_changed.connect(self._on_popup_changed)
        self.type_combo = QComboBox()
        self.status_combo = QComboBox()
        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        for combo in (self.type_combo, self.status_combo):
            combo.currentIndexChanged.connect(self._apply_view_filters)
        self.search_edit.textChanged.connect(self._apply_view_filters)
        ext_row = QHBoxLayout()
        ext_row.addWidget(self.ext_edit, 1)
        ext_row.addWidget(self.ext_popup_button)
        filter_row = QHBoxLayout()
        filter_row.addWidget(self.type_combo)
        filter_row.addWidget(self.status_combo)
        filter_row.addWidget(self.search_edit, 1)
        self.filter_area = QWidget()
        filter_layout = QVBoxLayout(self.filter_area)
        filter_layout.setContentsMargins(10, 2, 10, 4)
        filter_layout.setSpacing(4)
        filter_layout.addLayout(ext_row)
        filter_layout.addLayout(filter_row)
        self.filter_area.setVisible(self.filter_button.isChecked())

        # ---- header + notices
        self.header = QLabel()
        self.header.setObjectName("PaneHeader")
        self.busy_label = QLabel()
        self.busy_label.setObjectName("MutedLabel")
        header_row = QHBoxLayout()
        header_row.setContentsMargins(12, 4, 12, 2)
        header_row.addWidget(self.header, 1)
        header_row.addWidget(self.busy_label)
        self.notice = QLabel(wordWrap=True)
        self.notice.setObjectName("Notice")
        self.notice.setVisible(False)

        # ---- views
        self.model = fm.FileTableModel(ctx, thumbs, self)
        self.proxy = fm.FileFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(lambda pos: self._context_menu(self.table, pos))
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.sectionClicked.connect(self._on_header_clicked)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_menu)

        self.grid = QListView()
        self.grid.setModel(self.proxy)
        self.grid.setModelColumn(fm.NAME)
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setMovement(QListView.Static)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setWrapping(True)
        self.grid.setUniformItemSizes(False)  # portrait and landscape thumbnails differ
        self.grid.setWordWrap(True)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setSelectionModel(self.table.selectionModel())  # one shared selection
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(lambda pos: self._context_menu(self.grid, pos))

        self.views = QStackedWidget()
        self.views.addWidget(self.table)
        self.views.addWidget(self.grid)
        self.message = QLabel(alignment=Qt.AlignCenter, wordWrap=True)
        self.message.setObjectName("MutedLabel")
        self.message.setContentsMargins(16, 16, 16, 16)
        self.message.linkActivated.connect(lambda _link: self.clear_view_filters())
        self.views.addWidget(self.message)

        self.footer = QLabel()
        self.footer.setObjectName("MutedLabel")
        self.footer.setContentsMargins(12, 2, 12, 6)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("Separator")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(toolbar)
        layout.addLayout(options)
        layout.addWidget(self.filter_area)
        layout.addLayout(header_row)
        layout.addWidget(self.notice)
        layout.addWidget(line)
        layout.addWidget(self.views, 1)
        layout.addWidget(self.footer)

        self.table.selectionModel().currentChanged.connect(self._on_current_changed)
        self.table.selectionModel().selectionChanged.connect(self._update_footer)
        self.proxy.rowsInserted.connect(self._update_footer)
        self.proxy.rowsRemoved.connect(self._update_footer)
        self.proxy.modelReset.connect(self._update_footer)
        self.proxy.layoutChanged.connect(self._update_footer)
        for signal in (self.proxy.rowsInserted, self.proxy.rowsRemoved, self.proxy.modelReset, self.proxy.layoutChanged):
            signal.connect(lambda *_a: self._update_message())

        ctx.language_changed.connect(self.retranslate_ui)
        ctx.theme_changed.connect(self.refresh_icons)
        controller.project_changed.connect(self._on_project_changed)
        controller.items_reset.connect(self._on_items_reset)
        controller.items_updated.connect(self._on_items_updated)
        controller.busy_changed.connect(self._on_busy)
        controller.warnings_changed.connect(self._on_warnings)

        self._restore_view_state()
        self.refresh_icons()
        self.retranslate_ui()
        self._on_project_changed(controller.project)

    # ------------------------------------------------------------------ helpers
    def _tool_button(self, icon_name: str, checkable: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setProperty("icon_name", icon_name)
        button.setIconSize(QSize(18, 18))
        button.setAutoRaise(True)
        button.setCheckable(checkable)
        return button

    def refresh_icons(self, *_args) -> None:
        color = self.ctx.color("text")
        for button in self.findChildren(QToolButton):
            name = button.property("icon_name")
            if name:
                button.setIcon(icon(name, color))
        self.model.refresh_all()

    @property
    def project(self) -> Project | None:
        return self.controller.project

    # ------------------------------------------------------------------ i18n
    def retranslate_ui(self, *_args) -> None:
        self.open_button.setToolTip(tr("filepane.open_folder_tip"))
        self.refresh_button.setToolTip(tr("filepane.refresh_tip"))
        self.rename_button.setToolTip(tr("filepane.rename_tip"))
        self.list_button.setToolTip(tr("files.view_list"))
        self.grid_button.setToolTip(tr("files.view_grid"))
        self.size_slider.setToolTip(tr("files.thumb_size"))
        self.filter_button.setToolTip(tr("files.filters_tip"))
        self.ext_popup_button.setToolTip(tr("files.filter.popup_tip"))
        self.ext_edit.setPlaceholderText(tr("files.filter.ext_placeholder"))
        self.ext_edit.setToolTip(tr("files.filter.ext_tip"))
        self.search_edit.setPlaceholderText(tr("files.search_placeholder"))
        self.recursive_box.setText(tr("files.include_subfolders"))
        self.hide_exported_box.setText(tr("files.hide_exported"))
        self.recursive_box.setToolTip(tr("files.include_subfolders_tip"))
        self.hide_exported_box.setToolTip(tr("files.hide_exported_tip"))
        self.type_combo.setToolTip(tr("files.type_filter_tip"))
        self.status_combo.setToolTip(tr("files.status_filter_tip"))
        self.search_edit.setToolTip(tr("files.search_tip"))
        self._fill_combo(self.type_combo, [(None, tr("files.type.all")), ("photo", tr("files.type.photo")),
                                           ("video", tr("files.type.video"))])
        self._fill_combo(self.status_combo, [(None, tr("status.all"))] + [(s, tr(f"status.{s}")) for s in st.ALL_STATUSES])
        self.popup.retranslate_ui()
        self.model.refresh_all()
        self._update_header()
        self._update_footer()
        self._on_busy(self.controller.busy)
        self._on_warnings(self.controller.warnings())

    @staticmethod
    def _fill_combo(combo: QComboBox, items) -> None:
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for data, label in items:
            combo.addItem(label, data)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    # ------------------------------------------------------------------ project events
    def _on_project_changed(self, project: Project | None) -> None:
        self.thumbs.set_folder(project.folder if project else None)
        self.model.set_project(project)
        self._syncing = True
        enabled = project is not None
        for widget in (self.refresh_button, self.recursive_box, self.hide_exported_box, self.ext_edit,
                       self.ext_popup_button, self.type_combo, self.status_combo, self.search_edit):
            widget.setEnabled(enabled)
        if project is not None:
            self.recursive_box.setChecked(project.recursive)
            self.hide_exported_box.setChecked(project.hide_exported)
            self._set_ext_text(project.extension_filter)
            self.popup.set_extensions(project.extension_filter)
        self._syncing = False
        self._update_subfolder_column()
        self._update_header()
        self._update_footer()
        self._update_message()
        self.current_item_changed.emit(None)

    def _on_items_reset(self) -> None:
        selected = self.selected_ids()
        current = self.current_item()
        self.model.reload()
        self._update_subfolder_column()
        self.select_ids(selected, current.id if current else None)
        self._update_message()
        self._update_footer()

    def _on_items_updated(self, ids: list) -> None:
        self.model.refresh_ids(ids)
        current = self.current_item()
        if current is not None and current.id in ids:
            self.current_item_changed.emit(current)

    def _on_busy(self, busy: bool) -> None:
        self.busy_label.setText(tr("files.busy") if busy else "")
        self._update_message()

    def _on_warnings(self, keys: list) -> None:
        lines = []
        for key in keys:
            if key == "sidecar.error.save":
                lines.append(tr(key, error=self.controller.save_error or ""))
            else:
                lines.append(tr(key))
        self.notice.setText("\n".join(lines))
        self.notice.setVisible(bool(lines))

    def _update_header(self) -> None:
        project = self.project
        if project is None:
            self.header.setText(tr("filepane.no_folder"))
            self.header.setToolTip("")
        else:
            name = project.folder.name or str(project.folder)
            self.header.setText(name + (f"  ({tr('files.read_only')})" if project.read_only else ""))
            self.header.setToolTip(str(project.folder))

    def _update_message(self) -> None:
        project = self.project
        if project is None:
            self.message.setText("")
            self.views.setCurrentWidget(self.message)
        elif self.model.rowCount() == 0 and not self.controller.busy:
            self.message.setText(tr("files.empty"))
            self.views.setCurrentWidget(self.message)
        elif self.proxy.rowCount() == 0 and self.model.rowCount() > 0:
            self.message.setText(f'{html.escape(tr("files.no_filter_match"))}<br><br>'
                                 f'<a href="clear">{html.escape(tr("files.clear_filters"))}</a>')
            self.views.setCurrentWidget(self.message)
        else:
            self.views.setCurrentWidget(self.grid if self.view_mode == "grid" else self.table)

    def _update_footer(self, *_args) -> None:
        if self.project is None:
            self.footer.setText("")
            return
        self.footer.setText(
            tr("files.count", shown=self.proxy.rowCount(), total=self.model.rowCount(),
               selected=len(self.table.selectionModel().selectedRows()))
        )

    def _update_subfolder_column(self) -> None:
        recursive = bool(self.project and self.project.recursive)
        self.table.setColumnHidden(fm.SUBFOLDER, not recursive)

    # ------------------------------------------------------------------ option handlers
    def _on_recursive(self, checked: bool) -> None:
        if not self._syncing:
            self.controller.set_recursive(checked)

    def _on_hide_exported(self, checked: bool) -> None:
        if not self._syncing:
            self.controller.set_hide_exported(checked)

    def _set_ext_text(self, exts: list[str]) -> None:
        self.ext_edit.setText(extensions.format_filter_text(exts))

    def _on_ext_entered(self) -> None:
        exts = extensions.parse_filter_text(self.ext_edit.text())
        self._set_ext_text(exts)
        self.popup.set_extensions(exts)
        self.controller.set_extension_filter(exts)

    def _on_popup_changed(self, exts: list) -> None:
        self._set_ext_text(exts)
        self.controller.set_extension_filter(exts)

    def _show_popup(self) -> None:
        if self.project is not None:
            self.popup.set_extensions(self.project.extension_filter)
        self.popup.adjustSize()
        pos = self.ext_popup_button.mapToGlobal(self.ext_popup_button.rect().bottomRight())
        self.popup.move(pos.x() - self.popup.width(), pos.y() + 4)
        self.popup.show()

    def _apply_view_filters(self, *_args) -> None:
        self.proxy.set_filters(self.type_combo.currentData(), self.status_combo.currentData(), self.search_edit.text())
        self._update_footer()
        self._update_message()

    def clear_view_filters(self) -> None:
        """Reset type, status and name filters (the extension list and "hide exported" stay)."""
        for widget in (self.type_combo, self.status_combo):
            widget.blockSignals(True)
            widget.setCurrentIndex(0)
            widget.blockSignals(False)
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self._apply_view_filters()

    def _on_filters_toggled(self, checked: bool) -> None:
        self.filter_area.setVisible(checked)
        self.ctx.settings.set("files/filters_visible", checked)

    # ------------------------------------------------------------------ view mode / size / sort
    @property
    def view_mode(self) -> str:
        return "grid" if self.grid_button.isChecked() else "list"

    def set_view_mode(self, mode: str) -> None:
        self.list_button.setChecked(mode == "list")
        self.grid_button.setChecked(mode == "grid")
        self.model.decorate_names = mode == "grid"
        self.ctx.settings.set("files/view_mode", mode)
        self._apply_size()
        self._update_message()
        self.model.refresh_all()

    def _on_size_changed(self, value: int) -> None:
        self.ctx.settings.set("thumbs/size", value)
        self._apply_size()

    def _apply_size(self) -> None:
        size = self.size_slider.value()
        lo, hi = LIST_THUMB_RANGE
        list_size = max(lo, min(hi, size // 2))
        self.model.icon_size = list_size
        self.table.setIconSize(QSize(list_size, list_size))
        self.table.verticalHeader().setDefaultSectionSize(max(24, list_size + 6))
        self.table.setColumnWidth(fm.THUMB, list_size + 12)
        self.grid.setIconSize(QSize(size, size))
        self.grid.setGridSize(QSize(size + 24, size + 44))
        self.model.refresh_all()

    def _on_header_clicked(self, column: int) -> None:
        shift = bool(QGuiApplication.keyboardModifiers() & Qt.ShiftModifier)
        self.proxy.click_column(column, shift)
        self._show_sort_indicator()
        self.ctx.settings.set("files/sort", json.dumps([[c, int(o.value)] for c, o in self.proxy.sort_keys]))

    def _show_sort_indicator(self) -> None:
        column, order = self.proxy.sort_keys[0]
        self.table.horizontalHeader().setSortIndicator(column, order)

    def _header_menu(self, pos) -> None:
        menu = QMenu(self)
        for column, key in enumerate(fm.COLUMN_KEYS):
            if column == fm.NAME:
                continue
            if column == fm.SUBFOLDER and not (self.project and self.project.recursive):
                continue
            action = QAction(tr(f"files.col.{key}"), menu, checkable=True)
            action.setChecked(not self.table.isColumnHidden(column))
            action.toggled.connect(lambda on, c=column: self.table.setColumnHidden(c, not on))
            menu.addAction(action)
        menu.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def _restore_view_state(self) -> None:
        state = self.ctx.settings.get_bytes(HEADER_STATE_KEY)
        if state is None or not self.table.horizontalHeader().restoreState(state):
            for column in DEFAULT_HIDDEN_COLUMNS:
                self.table.setColumnHidden(column, True)
            self.table.setColumnWidth(fm.NAME, 170)
            self.table.setColumnWidth(fm.CAPTURE, 150)
        self.table.horizontalHeader().setSectionsClickable(True)
        try:
            keys = [(int(c), Qt.SortOrder(int(o))) for c, o in json.loads(self.ctx.settings.get("files/sort") or "[]")]
            keys = [(c, o) for c, o in keys if 0 <= c < len(fm.COLUMN_KEYS)]
        except (ValueError, TypeError):
            keys = []
        self.proxy.set_sort_keys(keys)
        self._show_sort_indicator()
        mode = self.ctx.settings.get("files/view_mode")
        self.set_view_mode(mode)

    def save_view_state(self) -> None:
        self.ctx.settings.set_bytes(HEADER_STATE_KEY, self.table.horizontalHeader().saveState())

    # ------------------------------------------------------------------ selection
    def _on_current_changed(self, current, _previous) -> None:
        self.current_item_changed.emit(self._item_for(current))

    def _item_for(self, proxy_index) -> FileItem | None:
        if not proxy_index.isValid():
            return None
        return self.model.item_at(self.proxy.mapToSource(proxy_index).row())

    def current_item(self) -> FileItem | None:
        return self._item_for(self.table.selectionModel().currentIndex())

    def selected_items(self) -> list[FileItem]:
        rows = self.table.selectionModel().selectedRows()
        items = [self._item_for(index) for index in rows]
        return [i for i in items if i is not None]

    def neighbor_item(self, offset: int = 1) -> FileItem | None:
        """The file ``offset`` rows below the current one in the visible order (for preloading)."""
        current = self.table.selectionModel().currentIndex()
        if not current.isValid():
            return None
        return self._item_for(self.proxy.index(current.row() + offset, fm.NAME))

    def selected_ids(self) -> list[str]:
        return [i.id for i in self.selected_items()]

    def select_ids(self, ids: list[str], current_id: str | None = None) -> None:
        sel = self.table.selectionModel()
        sel.clearSelection()
        flags = QItemSelectionModel.Select | QItemSelectionModel.Rows
        for item_id in ids:
            row = self.model.row_of(item_id)
            if row is not None:
                index = self.proxy.mapFromSource(self.model.index(row, fm.NAME))
                if index.isValid():
                    sel.select(index, flags)
        if current_id is not None:
            row = self.model.row_of(current_id)
            if row is not None:
                index = self.proxy.mapFromSource(self.model.index(row, fm.NAME))
                if index.isValid():
                    sel.setCurrentIndex(index, QItemSelectionModel.NoUpdate)

    # ------------------------------------------------------------------ context menu
    def _context_menu(self, view, pos) -> None:
        if self.project is None or not view.indexAt(pos).isValid():
            return
        items = self.selected_items()
        current = self.current_item()
        menu = QMenu(self)
        for key in ("action.copy_settings", "action.paste_settings", "action.reset"):
            action = self.edit_actions.get(key)
            if action is not None:
                menu.addAction(action)
        menu.addSeparator()
        if "action.export" in self.edit_actions:
            menu.addAction(self.edit_actions["action.export"])
        menu.addSeparator()
        change = menu.addAction(tr("files.menu.change_date"))
        change.setEnabled(len(items) == 1 and items[0].exists)
        change.triggered.connect(lambda: self.change_capture_requested.emit(items[0]))
        shift = menu.addAction(tr("files.menu.shift_timeline"))
        shift.setEnabled(len([i for i in items if i.exists]) >= 2)
        shift.triggered.connect(lambda: self.shift_timeline_requested.emit([i for i in items if i.exists]))
        menu.addSeparator()
        reveal = menu.addAction(tr("files.menu.reveal"))
        target = current if current in items else (items[0] if items else None)
        reveal.setEnabled(target is not None and target.exists)
        reveal.triggered.connect(lambda: self.reveal_requested.emit(target))
        menu.exec(view.viewport().mapToGlobal(pos))
