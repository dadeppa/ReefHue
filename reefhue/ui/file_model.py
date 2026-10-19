"""Table model for the file pane + proxy with filters and multi-column sorting (SPEC §5)."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSize, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QFont

from reefhue.core import status as st
from reefhue.core.project import FileItem, Project
from reefhue.core.timeutil import from_timestamp
from reefhue.i18n import tr
from reefhue.ui.context import AppContext
from reefhue.ui.thumbnail_service import ThumbnailService
from reefhue.ui.util import format_size

# Column ids (stable; also used in remembered header state).
THUMB, NAME, EXT, TYPE, CAPTURE, CREATED, SIZE, DURATION, RESOLUTION, STATUS, SUBFOLDER = range(11)
COLUMN_KEYS = ("thumb", "name", "ext", "type", "capture", "created", "size", "duration", "resolution", "status",
               "subfolder")
DEFAULT_SORT = [(CAPTURE, Qt.AscendingOrder), (NAME, Qt.AscendingOrder)]
MAX_SORT_KEYS = 3

ItemIdRole = Qt.UserRole + 1
SortRole = Qt.UserRole + 2
StatusRole = Qt.UserRole + 3

OVERRIDE_MARKER = " ✎"
STATUS_TOKENS = {
    st.UNEDITED: "text_muted",
    st.EDITED: "accent",
    st.EXPORTED: "status_ok",
    st.CHANGED_AFTER_EXPORT: "status_warn",
    st.MISSING: "danger",
}


def format_datetime(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def display_resolution(media: dict) -> tuple[int, int] | None:
    w, h = media.get("width"), media.get("height")
    if not w or not h:
        return None
    if media.get("rotation_meta") in (90, 270):
        w, h = h, w
    return int(w), int(h)


class FileTableModel(QAbstractTableModel):
    def __init__(self, ctx: AppContext, thumbs: ThumbnailService, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.thumbs = thumbs
        self.project: Project | None = None
        self.rows: list[FileItem] = []
        self._row_of: dict[str, int] = {}
        self._row_of_path: dict[str, int] = {}
        self.icon_size = 64
        self.decorate_names = False  # grid mode shows the thumbnail on the name column
        thumbs.thumbnail_ready.connect(self._on_thumbnail)

    # ---- data loading ---------------------------------------------------------
    def set_project(self, project: Project | None) -> None:
        self.project = project
        self.reload()

    def reload(self) -> None:
        self.beginResetModel()
        self.rows = self.project.visible_items() if self.project else []
        self._row_of = {item.id: row for row, item in enumerate(self.rows)}
        self._row_of_path = {item.rel_path: row for row, item in enumerate(self.rows)}
        self.endResetModel()

    def refresh_ids(self, ids: list[str]) -> None:
        rows = sorted(self._row_of[i] for i in ids if i in self._row_of)
        if rows:
            self.dataChanged.emit(self.index(rows[0], 0), self.index(rows[-1], len(COLUMN_KEYS) - 1))

    def refresh_all(self) -> None:
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(COLUMN_KEYS) - 1))
        self.headerDataChanged.emit(Qt.Horizontal, 0, len(COLUMN_KEYS) - 1)

    def _on_thumbnail(self, rel_path: str) -> None:
        row = self._row_of_path.get(rel_path)
        if row is not None:
            column = NAME if self.decorate_names else THUMB
            idx = self.index(row, column)
            self.dataChanged.emit(idx, idx, [Qt.DecorationRole])

    def item_at(self, row: int) -> FileItem | None:
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def row_of(self, item_id: str) -> int | None:
        return self._row_of.get(item_id)

    # ---- Qt model API -------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(COLUMN_KEYS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal and role == Qt.DisplayRole and 0 <= section < len(COLUMN_KEYS):
            return tr(f"files.col.{COLUMN_KEYS[section]}")
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        item = self.rows[index.row()]
        col = index.column()
        if role == ItemIdRole:
            return item.id
        if role == StatusRole:
            return item.status
        if role == Qt.DisplayRole:
            return self._display(item, col)
        if role == SortRole:
            return self._sort_key(item, col)
        if role == Qt.DecorationRole and (col == THUMB or (col == NAME and self.decorate_names)):
            return self.thumbs.get_icon(item)
        if role == Qt.FontRole and col == CAPTURE and item.capture_overridden:
            font = QFont()
            font.setItalic(True)
            return font
        if role == Qt.ForegroundRole:
            if col == STATUS:
                return QColor(self.ctx.color(STATUS_TOKENS.get(item.status, "text")))
            if not item.exists:
                return QColor(self.ctx.color("text_disabled"))
        if role == Qt.ToolTipRole:
            return self._tooltip(item, col)
        if role == Qt.SizeHintRole and col == THUMB:
            return QSize(self.icon_size + 8, self.icon_size + 4)
        if role == Qt.TextAlignmentRole and col in (SIZE, DURATION):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def _display(self, item: FileItem, col: int):
        media = item.entry["media"]
        if col == NAME:
            return item.name
        if col == EXT:
            return item.ext.upper()
        if col == TYPE:
            return tr(f"files.type.{item.kind}")
        if col == CAPTURE:
            text = format_datetime(item.capture_datetime)
            return text + OVERRIDE_MARKER if text and item.capture_overridden else text
        if col == CREATED:
            return format_datetime(from_timestamp(item.scanned.created)) if item.scanned else ""
        if col == SIZE:
            size = item.scanned.size if item.scanned else item.entry.get("size")
            return format_size(int(size)) if size else ""
        if col == DURATION:
            return format_duration(media.get("duration_s"))
        if col == RESOLUTION:
            res = display_resolution(media)
            return f"{res[0]} × {res[1]}" if res else ""
        if col == STATUS:
            status_key = item.status
            if status_key == st.EXPORTED:
                last = st.last_successful_export(item.entry)
                if last is not None and last.get("mode") == "overwrite":
                    return tr("status.exported_overwritten")
            return tr(f"status.{status_key}")
        if col == SUBFOLDER:
            return item.subfolder
        return None

    def _sort_key(self, item: FileItem, col: int):
        media = item.entry["media"]
        if col in (NAME, THUMB):
            return item.name.casefold()
        if col == EXT:
            return item.ext
        if col == TYPE:
            return item.kind
        if col == CAPTURE:
            return item.capture_datetime
        if col == CREATED:
            return item.scanned.created if item.scanned else None
        if col == SIZE:
            return item.scanned.size if item.scanned else item.entry.get("size")
        if col == DURATION:
            return media.get("duration_s")
        if col == RESOLUTION:
            res = display_resolution(media)
            return res[0] * res[1] if res else None
        if col == STATUS:
            return st.ALL_STATUSES.index(item.status)
        if col == SUBFOLDER:
            return item.subfolder.casefold()
        return None

    def _tooltip(self, item: FileItem, col: int):
        if col == CAPTURE:
            capture = item.entry["capture"]
            source = capture.get("datetime_source")
            lines = [tr(f"files.source.{source}") if source else ""]
            if item.capture_overridden:
                lines.append(tr("files.capture_corrected", original=capture.get("datetime_original") or "–"))
            return "\n".join(line for line in lines if line) or None
        if col == NAME:
            return item.rel_path
        return None


class FileFilterProxy(QSortFilterProxyModel):
    """Type / status / name filters and multi-key sorting (Shift+click = secondary key)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.type_filter: str | None = None    # "photo" | "video" | None
        self.status_filter: str | None = None
        self.search_text = ""
        self.sort_keys: list[tuple[int, Qt.SortOrder]] = list(DEFAULT_SORT)
        self.setDynamicSortFilter(True)
        self.setSortRole(SortRole)

    def set_filters(self, type_filter: str | None, status_filter: str | None, search: str) -> None:
        self.beginFilterChange()
        self.type_filter, self.status_filter, self.search_text = type_filter, status_filter, search.strip().casefold()
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def set_sort_keys(self, keys: list[tuple[int, Qt.SortOrder]]) -> None:
        self.sort_keys = keys[:MAX_SORT_KEYS] or list(DEFAULT_SORT)
        self.invalidate()
        self.sort(0, Qt.AscendingOrder)

    def click_column(self, column: int, shift: bool) -> None:
        keys = list(self.sort_keys)
        existing = next((i for i, (c, _o) in enumerate(keys) if c == column), None)
        if shift:
            if existing is None:
                keys.append((column, Qt.AscendingOrder))
            else:
                c, o = keys[existing]
                keys[existing] = (c, Qt.DescendingOrder if o == Qt.AscendingOrder else Qt.AscendingOrder)
        elif existing == 0:
            c, o = keys[0]
            keys = [(c, Qt.DescendingOrder if o == Qt.AscendingOrder else Qt.AscendingOrder)] + keys[1:]
        else:
            keys = [(column, Qt.AscendingOrder)]
        self.set_sort_keys(keys)

    def filterAcceptsRow(self, source_row, source_parent) -> bool:  # noqa: N802
        item = self.sourceModel().item_at(source_row)
        if item is None:
            return False
        if self.type_filter and item.kind != self.type_filter:
            return False
        if self.status_filter and item.status != self.status_filter:
            return False
        if self.search_text and self.search_text not in item.rel_path.casefold():
            return False
        return True

    def lessThan(self, left, right) -> bool:  # noqa: N802
        model = self.sourceModel()
        a, b = model.item_at(left.row()), model.item_at(right.row())
        if a is None or b is None:
            return False
        for column, order in self.sort_keys + [(NAME, Qt.AscendingOrder)]:
            ka, kb = model._sort_key(a, column), model._sort_key(b, column)
            if ka == kb:
                continue
            if ka is None:  # empty values always last
                return False
            if kb is None:
                return True
            return ka < kb if order == Qt.AscendingOrder else kb < ka
        return a.rel_path < b.rel_path
