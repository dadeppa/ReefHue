"""Extension filter popup: Photos / Videos groups with checkboxes + extra extensions (SPEC §5)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from reefhue.core import extensions
from reefhue.i18n import tr


class _Group(QGroupBox):
    changed = Signal()

    def __init__(self, choices: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.choices = choices
        self.boxes: dict[str, QCheckBox] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        for index, ext in enumerate(choices):
            box = QCheckBox(ext)
            box.toggled.connect(self.changed)
            self.boxes[ext] = box
            grid.addWidget(box, index // 4, index % 4)
        self.all_on = QPushButton()
        self.all_off = QPushButton()
        self.all_on.clicked.connect(lambda: self._set_all(True))
        self.all_off.clicked.connect(lambda: self._set_all(False))
        buttons = QHBoxLayout()
        buttons.addWidget(self.all_on)
        buttons.addWidget(self.all_off)
        buttons.addStretch(1)
        self.extra_label = QLabel()
        self.extra = QLineEdit()
        self.extra.editingFinished.connect(self.changed)
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addLayout(buttons)
        layout.addWidget(self.extra_label)
        layout.addWidget(self.extra)

    def _set_all(self, on: bool) -> None:
        for box in self.boxes.values():
            box.blockSignals(True)
            box.setChecked(on)
            box.blockSignals(False)
        self.changed.emit()

    def selected(self) -> list[str]:
        chosen = [ext for ext, box in self.boxes.items() if box.isChecked()]
        return chosen + [e for e in extensions.parse_filter_text(self.extra.text()) if e not in chosen]

    def set_selected(self, exts: list[str], extras: list[str]) -> None:
        for ext, box in self.boxes.items():
            box.blockSignals(True)
            box.setChecked(ext in exts)
            box.blockSignals(False)
        self.extra.setText(extensions.format_filter_text(extras))


class ExtensionFilterPopup(QFrame):
    """A ``Qt.Popup`` frame; emits the full extension list whenever something changes."""

    extensions_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup)
        self.setObjectName("FilterPopup")
        self.setFrameShape(QFrame.StyledPanel)
        self.photos = _Group(extensions.PHOTO_CHOICES)
        self.videos = _Group(extensions.VIDEO_CHOICES)
        self.hint = QLabel(wordWrap=True)
        self.hint.setObjectName("MutedLabel")
        for group in (self.photos, self.videos):
            group.changed.connect(self._emit)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self.photos)
        layout.addWidget(self.videos)
        layout.addWidget(self.hint)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.photos.setTitle(tr("files.filter.photos"))
        self.videos.setTitle(tr("files.filter.videos"))
        for group in (self.photos, self.videos):
            group.all_on.setText(tr("files.filter.all_on"))
            group.all_off.setText(tr("files.filter.all_off"))
            group.extra_label.setText(tr("files.filter.extra"))
            group.extra.setPlaceholderText(tr("files.filter.extra_placeholder"))
        self.hint.setText(tr("files.filter.gopro_hint"))

    def set_extensions(self, exts: list[str]) -> None:
        """Sync from the flat list; unknown extensions are shown as photo extras
        unless they are already listed as video extras."""
        video_extras = extensions.parse_filter_text(self.videos.extra.text())
        unknown = [e for e in exts if e not in extensions.PHOTO_CHOICES and e not in extensions.VIDEO_CHOICES]
        self.videos.set_selected(exts, [e for e in unknown if e in video_extras])
        self.photos.set_selected(exts, [e for e in unknown if e not in video_extras])

    def selected(self) -> list[str]:
        result = self.photos.selected()
        return result + [e for e in self.videos.selected() if e not in result]

    def _emit(self) -> None:
        self.extensions_changed.emit(self.selected())
