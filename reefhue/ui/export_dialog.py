"""Export dialog (single file or batch) and the name-conflict question (SPEC §14)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from reefhue.core import app_settings
from reefhue.core import export_plan as ep
from reefhue.core.project import FileItem
from reefhue.i18n import tr
from reefhue.ui.context import AppContext


class ExportDialog(QDialog):
    def __init__(self, ctx: AppContext, items: list[FileItem], defaults: ep.ExportOptions,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.items = items
        photos = [i for i in items if i.kind == "photo"]
        videos = [i for i in items if i.kind == "video"]
        status = ctx.ffmpeg_status
        self.video_export_ok = status is not None and status.export_available
        hw_ok = status is not None and status.hw_encoders_available
        self.setWindowTitle(tr("export.title"))
        self.setMinimumWidth(500)

        intro = QLabel(tr("export.intro", count=len(items), photos=len(photos), videos=len(videos)))
        intro.setWordWrap(True)

        # ---- mode
        self.mode_group = QButtonGroup(self)
        self.suffix_radio = QRadioButton(tr("export.mode.suffix"))
        self.subfolder_radio = QRadioButton(tr("export.mode.subfolder"))
        self.overwrite_radio = QRadioButton(tr("export.mode.overwrite"))
        for i, radio in enumerate((self.suffix_radio, self.subfolder_radio, self.overwrite_radio)):
            self.mode_group.addButton(radio, i)
        self.suffix_edit = QLineEdit(defaults.suffix)
        self.subfolder_edit = QLineEdit(defaults.subfolder)
        self.overwrite_note = QLabel(tr("export.overwrite_note"))
        self.overwrite_note.setWordWrap(True)
        self.overwrite_note.setObjectName("Notice")
        {ep.MODE_SUBFOLDER: self.subfolder_radio}.get(defaults.mode, self.suffix_radio).setChecked(True)
        self.name_error = QLabel(tr("settings.invalid_name"))
        self.name_error.setStyleSheet(f"color: {ctx.color('danger')};")
        self.name_error.setWordWrap(True)
        mode_box = QGroupBox(tr("export.group.target"))
        grid = QFormLayout(mode_box)
        grid.addRow(self.suffix_radio, self.suffix_edit)
        grid.addRow(self.subfolder_radio, self.subfolder_edit)
        grid.addRow(self.overwrite_radio)
        grid.addRow(self.overwrite_note)
        grid.addRow(self.name_error)

        # ---- photos
        self.jpeg_quality = QSpinBox()
        self.jpeg_quality.setRange(1, 100)
        self.jpeg_quality.setValue(defaults.jpeg_quality)
        self.heic_combo = QComboBox()
        self.heic_combo.addItem(tr("export.heic.jpeg"), "jpeg")
        self.heic_combo.addItem(tr("export.heic.heic"), "heic")
        self.heic_combo.setCurrentIndex(max(0, self.heic_combo.findData(defaults.heic_as)))
        photo_box = QGroupBox(tr("export.group.photos"))
        form = QFormLayout(photo_box)
        form.addRow(tr("settings.jpeg_quality"), self.jpeg_quality)
        if any(i.ext in ep.HEIC_EXTS for i in photos):
            form.addRow(tr("export.heic"), self.heic_combo)
        photo_box.setVisible(bool(photos))

        # ---- videos
        self.video_quality = QComboBox()
        for q in app_settings.VIDEO_QUALITY_CHOICES:
            self.video_quality.addItem(tr(f"export.quality.{q}"), q)
        self.video_quality.setCurrentIndex(max(0, self.video_quality.findData(defaults.video_quality)))
        self.hw_encoder = QCheckBox(tr("settings.hw_encoder"))
        self.hw_encoder.setChecked(defaults.hw_encoder and hw_ok)
        self.hw_encoder.setEnabled(hw_ok)
        self.hw_encoder.setToolTip(tr("settings.hw_encoder_tip") if hw_ok else tr("settings.hw_encoder_missing"))
        keep_audio = all(i.entry["export_options"].get("keep_audio", True) for i in videos)
        self.remove_audio = QCheckBox(tr("export.remove_audio"))
        self.remove_audio.setChecked(not keep_audio)
        self.keep_telemetry = QCheckBox(tr("settings.keep_telemetry"))
        self.keep_telemetry.setChecked(defaults.keep_telemetry)
        self.video_note = QLabel(tr("export.videos_unavailable"))
        self.video_note.setWordWrap(True)
        self.video_note.setObjectName("Notice")
        self.video_note.setVisible(bool(videos) and not self.video_export_ok)
        video_box = QGroupBox(tr("export.group.videos"))
        form = QFormLayout(video_box)
        form.addRow(tr("settings.video_quality"), self.video_quality)
        form.addRow("", self.hw_encoder)
        form.addRow("", self.remove_audio)
        form.addRow("", self.keep_telemetry)
        form.addRow(self.video_note)
        video_box.setVisible(bool(videos))
        video_box.setEnabled(self.video_export_ok or not videos)
        self.video_note.setEnabled(True)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(tr("export.start"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr("common.cancel"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(mode_box)
        layout.addWidget(photo_box)
        layout.addWidget(video_box)
        layout.addWidget(self.buttons)

        self.mode_group.idToggled.connect(lambda *_: self._validate())
        self.suffix_edit.textChanged.connect(self._validate)
        self.subfolder_edit.textChanged.connect(self._validate)
        self._validate()

    @property
    def mode(self) -> str:
        return {0: ep.MODE_SUFFIX, 1: ep.MODE_SUBFOLDER, 2: ep.MODE_OVERWRITE}[self.mode_group.checkedId()]

    def _validate(self, *_args) -> None:
        mode = self.mode
        self.suffix_edit.setEnabled(mode == ep.MODE_SUFFIX)
        self.subfolder_edit.setEnabled(mode == ep.MODE_SUBFOLDER)
        self.overwrite_note.setVisible(mode == ep.MODE_OVERWRITE)
        valid = True
        if mode == ep.MODE_SUFFIX:
            valid = app_settings.is_valid_name_part(self.suffix_edit.text())
        elif mode == ep.MODE_SUBFOLDER:
            valid = app_settings.is_valid_name_part(self.subfolder_edit.text())
        self.name_error.setVisible(not valid)
        exportable = [i for i in self.items if i.kind == "photo" or self.video_export_ok]
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(valid and bool(exportable))

    def options(self) -> ep.ExportOptions:
        return ep.ExportOptions(
            mode=self.mode,
            suffix=self.suffix_edit.text(),
            subfolder=self.subfolder_edit.text(),
            jpeg_quality=self.jpeg_quality.value(),
            heic_as=self.heic_combo.currentData(),
            video_quality=self.video_quality.currentData(),
            hw_encoder=self.hw_encoder.isChecked() and self.hw_encoder.isEnabled(),
            keep_telemetry=self.keep_telemetry.isChecked(),
        )


class OverwriteConfirmDialog(QDialog):
    """The extra confirmation for "Overwrite original" (SPEC §14)."""

    def __init__(self, count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("export.overwrite_title"))
        text = QLabel(tr("export.overwrite_confirm", count=count))
        text.setWordWrap(True)
        self.buttons = QDialogButtonBox()
        self.confirm = QPushButton(tr("export.overwrite_button"))
        self.buttons.addButton(self.confirm, QDialogButtonBox.DestructiveRole)
        cancel = self.buttons.addButton(tr("common.cancel"), QDialogButtonBox.RejectRole)
        cancel.setDefault(True)  # never the default action
        self.confirm.setAutoDefault(False)
        self.confirm.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(self.buttons)


class ConflictDialog(QDialog):
    """A target exists: skip / replace (old file to trash) / auto-number, optionally for all."""

    def __init__(self, target: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("export.conflict_title"))
        self.action: str = ep.CONFLICT_SKIP
        text = QLabel(tr("export.conflict_text", name=target))
        text.setWordWrap(True)
        self.apply_all = QCheckBox(tr("export.conflict_apply_all"))
        buttons = QHBoxLayout()
        for action, key in ((ep.CONFLICT_SKIP, "export.conflict_skip"), (ep.CONFLICT_REPLACE, "export.conflict_replace"),
                            (ep.CONFLICT_NUMBER, "export.conflict_number")):
            button = QPushButton(tr(key))
            button.setAutoDefault(False)
            button.clicked.connect(lambda _=False, a=action: self._choose(a))
            buttons.addWidget(button)
            if action == ep.CONFLICT_NUMBER:
                button.setDefault(True)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(self.apply_all)
        layout.addLayout(buttons)

    def _choose(self, action: str) -> None:
        self.action = action
        self.accept()
