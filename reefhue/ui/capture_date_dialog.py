"""Context menu ▸ Change capture date… (one file). Original metadata is never touched."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from reefhue.core.project import FileItem
from reefhue.core.timeutil import parse_wallclock
from reefhue.i18n import tr
from reefhue.ui.file_model import format_datetime
from reefhue.ui.util import from_qdatetime, make_datetime_edit, to_qdatetime

SET = "set"
RESET = "reset"


class CaptureDateDialog(QDialog):
    def __init__(self, item: FileItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self.action: str | None = None
        self.setWindowTitle(tr("capture.title"))
        self.setMinimumWidth(420)
        capture = item.entry["capture"]
        original = parse_wallclock(capture.get("datetime_original"))
        source = capture.get("datetime_source")

        name = QLabel(item.name)
        font = name.font()
        font.setBold(True)
        name.setFont(font)
        self.original_label = QLabel(
            tr("capture.original", value=format_datetime(original) or "–",
               source=tr(f"files.source.{source}") if source else "–")
        )
        self.original_label.setObjectName("MutedLabel")
        self.edit = make_datetime_edit()
        self.edit.setDateTime(to_qdatetime(item.capture_datetime or original or datetime.now().replace(microsecond=0)))

        form = QFormLayout()
        form.addRow(tr("capture.new_value"), self.edit)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(tr("common.ok"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr("common.cancel"))
        self.reset_button = QPushButton(tr("capture.reset"))
        self.reset_button.setEnabled(item.capture_overridden)
        self.buttons.addButton(self.reset_button, QDialogButtonBox.ResetRole)
        self.buttons.accepted.connect(self._accept_set)
        self.buttons.rejected.connect(self.reject)
        self.reset_button.clicked.connect(self._accept_reset)

        layout = QVBoxLayout(self)
        layout.addWidget(name)
        layout.addWidget(self.original_label)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

    @property
    def value(self) -> datetime:
        return from_qdatetime(self.edit.dateTime())

    def _accept_set(self) -> None:
        self.action = SET
        self.accept()

    def _accept_reset(self) -> None:
        self.action = RESET
        self.accept()
