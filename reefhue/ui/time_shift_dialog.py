"""Context menu ▸ Shift timeline… (SPEC §5).

Mark one anchor file, enter its correct date/time; all other files of the
selection move by the same delta. GoPro validation warns about order conflicts,
can split the selection into groups (each with its own anchor) and can derive
chapter start times from durations. Old → new is previewed before applying.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from reefhue.core import timeline
from reefhue.core.project import FileItem, ShiftGroup
from reefhue.i18n import tr
from reefhue.ui.file_model import format_datetime
from reefhue.ui.util import from_qdatetime, make_datetime_edit, to_qdatetime

MAX_WARNING_LINES = 5
COL_GROUP, COL_NAME, COL_OLD, COL_NEW = range(4)


class TimeShiftDialog(QDialog):
    def __init__(self, items: list[FileItem], current_id: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("shift.title"))
        self.setMinimumSize(720, 520)
        timed = [i.timed() for i in items if i.capture_datetime is not None]
        timed.sort(key=lambda f: (f.dt, f.name))
        self.files = {f.id: f for f in timed}
        self.order = [f.id for f in timed]
        self.proposals = timeline.propose_groups(timed)
        self.conflicts = timeline.gopro_order_conflicts(timed)
        self.chapter_starts = timeline.derive_chapter_starts(timed)
        self._preferred_anchor = current_id if current_id in self.files else (self.order[0] if self.order else None)
        self.groups: list[list[str]] = []
        self.anchors: list[str] = []
        self.targets: list[datetime] = []

        # ---- widgets
        intro = QLabel(tr("shift.intro"), wordWrap=True)
        self.warning = QLabel(wordWrap=True)
        self.warning.setObjectName("WarningLabel")
        self.split_button = QPushButton()
        self.split_button.clicked.connect(self._toggle_split)
        self.split_button.setAutoDefault(False)
        self.derive_box = QCheckBox(tr("shift.derive_chapters", count=len(self.chapter_starts)))
        self.derive_box.setVisible(bool(self.chapter_starts))
        self.derive_box.toggled.connect(self._refresh_table)

        self.group_combo = QComboBox()
        self.group_combo.currentIndexChanged.connect(self._on_group_changed)
        self.anchor_combo = QComboBox()
        self.anchor_combo.currentIndexChanged.connect(self._on_anchor_changed)
        self.target_edit = make_datetime_edit()
        self.target_edit.dateTimeChanged.connect(self._on_target_changed)
        self.delta_label = QLabel()
        self.delta_label.setObjectName("MutedLabel")

        form = QFormLayout()
        self.group_label = QLabel(tr("shift.group"))
        form.addRow(self.group_label, self.group_combo)
        form.addRow(tr("shift.anchor"), self.anchor_combo)
        form.addRow(tr("shift.anchor_new"), self.target_edit)
        form.addRow("", self.delta_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [tr("shift.col.group"), tr("files.col.name"), tr("shift.col.old"), tr("shift.col.new")]
        )
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._row_as_anchor)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(tr("shift.apply"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr("common.cancel"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        top = QHBoxLayout()
        top.addWidget(self.warning, 1)
        top.addWidget(self.split_button, 0, Qt.AlignTop)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(top)
        layout.addWidget(self.derive_box)
        layout.addLayout(form)
        layout.addWidget(self.table, 1)
        hint = QLabel(tr("shift.hint_double_click"))
        hint.setObjectName("MutedLabel")
        layout.addWidget(hint)
        layout.addWidget(self.buttons)

        self._set_groups([list(self.order)])
        self._update_warning()

    # ---- grouping --------------------------------------------------------------------
    @property
    def can_split(self) -> bool:
        return len(self.proposals) > 1

    def _set_groups(self, groups: list[list[str]]) -> None:
        self.groups = [sorted(g, key=lambda i: (self.files[i].dt, self.files[i].name)) for g in groups if g]
        self.anchors = []
        self.targets = []
        for g in self.groups:
            anchor = self._preferred_anchor if self._preferred_anchor in g else g[0]
            self.anchors.append(anchor)
            self.targets.append(self.files[anchor].dt)
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        for index, g in enumerate(self.groups):
            self.group_combo.addItem(tr("shift.group_item", number=index + 1, count=len(g)))
        self.group_combo.blockSignals(False)
        multiple = len(self.groups) > 1
        self.group_combo.setVisible(multiple)
        self.group_label.setVisible(multiple)
        self.split_button.setVisible(self.can_split)
        self.split_button.setText(tr("shift.merge") if multiple else tr("shift.split"))
        self._on_group_changed(0)

    def _toggle_split(self) -> None:
        if len(self.groups) > 1:
            self._set_groups([list(self.order)])
        else:
            self._set_groups([p.file_ids for p in self.proposals])

    def _update_warning(self) -> None:
        lines = [
            tr("shift.conflict", earlier=c.earlier.name, later=c.later.name)
            for c in self.conflicts[:MAX_WARNING_LINES]
        ]
        if len(self.conflicts) > MAX_WARNING_LINES:
            lines.append(tr("shift.more", count=len(self.conflicts) - MAX_WARNING_LINES))
        if self.can_split:
            lines.append(tr("shift.jump_detected", count=len(self.proposals)))
        self.warning.setText("\n".join(lines))
        self.warning.setVisible(bool(lines))

    # ---- current group editing --------------------------------------------------------------
    @property
    def current_group(self) -> int:
        return max(0, self.group_combo.currentIndex()) if len(self.groups) > 1 else 0

    def _on_group_changed(self, _index: int) -> None:
        g = self.current_group
        if not self.groups:
            return
        self.anchor_combo.blockSignals(True)
        self.anchor_combo.clear()
        for file_id in self.groups[g]:
            self.anchor_combo.addItem(self.files[file_id].name, file_id)
        self.anchor_combo.setCurrentIndex(self.groups[g].index(self.anchors[g]))
        self.anchor_combo.blockSignals(False)
        self.target_edit.blockSignals(True)
        self.target_edit.setDateTime(to_qdatetime(self.targets[g]))
        self.target_edit.blockSignals(False)
        self._refresh_table()

    def _on_anchor_changed(self, index: int) -> None:
        g = self.current_group
        file_id = self.anchor_combo.itemData(index)
        if file_id is None:
            return
        # Keep the group's delta: the new anchor's target follows the same shift.
        delta = self.targets[g] - self._base_time(self.anchors[g])
        self.anchors[g] = file_id
        self.targets[g] = self._base_time(file_id) + delta
        self.target_edit.blockSignals(True)
        self.target_edit.setDateTime(to_qdatetime(self.targets[g]))
        self.target_edit.blockSignals(False)
        self._refresh_table()

    def _on_target_changed(self, value) -> None:
        self.targets[self.current_group] = from_qdatetime(value)
        self._refresh_table()

    def _row_as_anchor(self, row: int, _column: int) -> None:
        file_id = self.table.item(row, COL_NAME).data(Qt.UserRole)
        for g, ids in enumerate(self.groups):
            if file_id in ids:
                if len(self.groups) > 1:
                    self.group_combo.setCurrentIndex(g)
                self.anchor_combo.setCurrentIndex(ids.index(file_id))
                return

    # ---- preview ------------------------------------------------------------------------------
    def base_times(self) -> dict[str, datetime]:
        base = {i: f.dt for i, f in self.files.items()}
        if self.derive_box.isChecked():
            base.update(self.chapter_starts)
        return base

    def _base_time(self, file_id: str) -> datetime:
        return self.base_times()[file_id]

    def preview(self) -> dict[str, datetime]:
        base = self.base_times()
        result: dict[str, datetime] = {}
        for ids, anchor, target in zip(self.groups, self.anchors, self.targets):
            result.update(timeline.shift_plan({i: base[i] for i in ids}, anchor, target))
        return result

    def _refresh_table(self, *_args) -> None:
        new = self.preview()
        g = self.current_group
        delta = self.targets[g] - self._base_time(self.anchors[g]) if self.groups else None
        self.delta_label.setText(tr("shift.delta", delta=_format_delta(delta)) if delta is not None else "")
        rows = [(gi, fid) for gi, ids in enumerate(self.groups) for fid in ids]
        self.table.setRowCount(len(rows))
        bold = QFont()
        bold.setBold(True)
        for row, (gi, fid) in enumerate(rows):
            f = self.files[fid]
            is_anchor = fid == self.anchors[gi]
            name = f.name + (f"  ⚓ {tr('shift.anchor_marker')}" if is_anchor else "")
            cells = [str(gi + 1), name, format_datetime(f.dt), format_datetime(new[fid])]
            for col, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if col == COL_NAME:
                    cell.setData(Qt.UserRole, fid)
                if is_anchor:
                    cell.setFont(bold)
                self.table.setItem(row, col, cell)
        self.table.resizeColumnToContents(COL_GROUP)
        self.table.resizeColumnToContents(COL_OLD)
        self.table.resizeColumnToContents(COL_NEW)

    # ---- result -------------------------------------------------------------------------------
    def result_groups(self) -> tuple[list[ShiftGroup], dict[str, datetime]]:
        base = self.chapter_starts if self.derive_box.isChecked() else {}
        groups = [ShiftGroup(a, t, list(ids)) for ids, a, t in zip(self.groups, self.anchors, self.targets)]
        return groups, dict(base)


def _format_delta(delta) -> str:
    seconds = int(delta.total_seconds())
    sign = "−" if seconds < 0 else "+"
    seconds = abs(seconds)
    days, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    text = f"{h:02d}:{m:02d}:{s:02d}"
    return f"{sign}{tr('units.days', value=days)} {text}" if days else f"{sign}{text}"
