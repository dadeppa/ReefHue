"""Section picker for paste settings / apply preset / save preset (SPEC §12). Geometry off by default."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from reefhue.core import presets
from reefhue.i18n import tr


class SectionsDialog(QDialog):
    def __init__(self, title: str, intro: str, available=None, ask_name: bool = False,
                 ok_text: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        available = list(presets.SECTIONS) if available is None else list(available)
        self.boxes: dict[str, QCheckBox] = {}
        grid = QGridLayout()
        for index, section in enumerate(presets.SECTIONS):
            box = QCheckBox(tr(f"section.{section}"))
            box.setChecked(section in presets.DEFAULT_SECTIONS and section in available)
            box.setEnabled(section in available)
            box.toggled.connect(self._validate)
            self.boxes[section] = box
            grid.addWidget(box, index // 2, index % 2)
        all_on, all_off = QPushButton(tr("files.filter.all_on")), QPushButton(tr("files.filter.all_off"))
        all_on.clicked.connect(lambda: self._set_all(True))
        all_off.clicked.connect(lambda: self._set_all(False))
        row = QHBoxLayout()
        row.addWidget(all_on)
        row.addWidget(all_off)
        row.addStretch(1)

        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("presets.name_placeholder"))
        self.name.textChanged.connect(self._validate)
        self.name.setVisible(ask_name)
        self._ask_name = ask_name

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(ok_text or tr("common.ok"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr("common.cancel"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        if intro:
            layout.addWidget(QLabel(intro, wordWrap=True))
        if ask_name:
            layout.addWidget(self.name)
        layout.addLayout(grid)
        layout.addLayout(row)
        layout.addWidget(self.buttons)
        self._validate()

    def _set_all(self, on: bool) -> None:
        for box in self.boxes.values():
            if box.isEnabled():
                box.setChecked(on)

    def _validate(self, *_args) -> None:
        ok = bool(self.selected()) and (not self._ask_name or bool(self.name.text().strip()))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(ok)

    def selected(self) -> list[str]:
        return [s for s, box in self.boxes.items() if box.isChecked() and box.isEnabled()]

    @property
    def preset_name(self) -> str:
        return self.name.text().strip()
