"""Settings dialog (SPEC §4): language, theme, cache, preview quality, export defaults, thumbnails."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from reefhue import i18n
from reefhue.core import app_settings as schema
from reefhue.core import paths
from reefhue.i18n import tr
from reefhue.ui.context import AppContext
from reefhue.ui.util import format_size
from reefhue.ui.workers import run_task


def _combo(items: list[tuple[str, str]], current: str) -> QComboBox:
    """Combo with (data, label) items; selects ``current``."""
    combo = QComboBox()
    for data, label in items:
        combo.addItem(label, data)
    index = combo.findData(current)
    combo.setCurrentIndex(max(0, index))
    return combo


class SettingsDialog(QDialog):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        s = ctx.settings
        self.setWindowTitle(tr("settings.title"))
        self.setMinimumWidth(480)

        # ---- general
        languages = [(schema.LANGUAGE_SYSTEM, tr("language.system"))]
        languages += list(i18n.available_languages().items())
        self.language = _combo(languages, s.get("ui/language"))
        self.theme = _combo([(c, tr(f"theme.{c}")) for c in schema.THEME_CHOICES], s.get("ui/theme"))
        general = QGroupBox(tr("settings.group.general"))
        form = QFormLayout(general)
        form.addRow(tr("settings.language"), self.language)
        form.addRow(tr("settings.theme"), self.theme)

        # ---- preview
        self.preview_quality = _combo(
            [(c, tr(f"settings.preview_quality.{c}")) for c in schema.PREVIEW_QUALITY_CHOICES],
            s.get("preview/quality"),
        )
        spec = schema.SETTINGS["thumbs/size"]
        self.thumb_size = QSlider(Qt.Horizontal)
        self.thumb_size.setRange(spec.minimum, spec.maximum)
        self.thumb_size.setSingleStep(8)
        self.thumb_size.setPageStep(32)
        self.thumb_size.setValue(s.get("thumbs/size"))
        self.thumb_size_label = QLabel()
        self.thumb_size_label.setMinimumWidth(56)
        self.thumb_size.valueChanged.connect(self._update_thumb_label)
        self._update_thumb_label(self.thumb_size.value())
        thumb_row = QHBoxLayout()
        thumb_row.addWidget(self.thumb_size, 1)
        thumb_row.addWidget(self.thumb_size_label)
        self.auto_refresh = QCheckBox(tr("settings.auto_refresh"))
        self.auto_refresh.setToolTip(tr("settings.auto_refresh_tip"))
        self.auto_refresh.setChecked(s.get("files/auto_refresh"))
        preview = QGroupBox(tr("settings.group.preview"))
        form = QFormLayout(preview)
        form.addRow(tr("settings.preview_quality"), self.preview_quality)
        form.addRow(tr("settings.thumb_size"), thumb_row)
        form.addRow("", self.auto_refresh)

        # ---- export defaults
        self.export_mode = _combo(
            [(c, tr(f"export.mode.{c}")) for c in schema.EXPORT_MODE_CHOICES], s.get("export/mode")
        )
        self.suffix = QLineEdit(s.get("export/suffix"))
        self.subfolder = QLineEdit(s.get("export/subfolder"))
        for edit in (self.suffix, self.subfolder):
            edit.textChanged.connect(self._validate)
        jpeg = schema.SETTINGS["export/jpeg_quality"]
        self.jpeg_quality = QSpinBox()
        self.jpeg_quality.setRange(jpeg.minimum, jpeg.maximum)
        self.jpeg_quality.setValue(s.get("export/jpeg_quality"))
        self.video_quality = _combo(
            [(c, tr(f"export.quality.{c}")) for c in schema.VIDEO_QUALITY_CHOICES],
            s.get("export/video_quality"),
        )
        self.hw_encoder = QCheckBox(tr("settings.hw_encoder"))
        self.hw_encoder.setToolTip(tr("settings.hw_encoder_tip"))
        self.hw_encoder.setChecked(s.get("export/hw_encoder"))
        status = ctx.ffmpeg_status
        if status is not None and status.available and not status.hw_encoders_available:
            # Addendum A3: VideoToolbox encoders missing → option unavailable
            self.hw_encoder.setEnabled(False)
            self.hw_encoder.setToolTip(tr("settings.hw_encoder_missing"))
        self.keep_telemetry = QCheckBox(tr("settings.keep_telemetry"))
        self.keep_telemetry.setChecked(s.get("export/keep_telemetry"))
        self.name_error = QLabel(tr("settings.invalid_name"))
        self.name_error.setWordWrap(True)
        self.name_error.setStyleSheet(f"color: {ctx.color('danger')};")
        self.name_error.setVisible(False)
        export = QGroupBox(tr("settings.group.export"))
        form = QFormLayout(export)
        form.addRow(tr("settings.export_mode"), self.export_mode)
        form.addRow(tr("settings.export_suffix"), self.suffix)
        form.addRow(tr("settings.export_subfolder"), self.subfolder)
        form.addRow("", self.name_error)
        form.addRow(tr("settings.jpeg_quality"), self.jpeg_quality)
        form.addRow(tr("settings.video_quality"), self.video_quality)
        form.addRow("", self.hw_encoder)
        form.addRow("", self.keep_telemetry)

        # ---- cache
        self.cache_label = QLabel(tr("settings.cache_size", size="…"))
        self.cache_button = QPushButton(tr("settings.cache_clear"))
        self.cache_button.clicked.connect(self._clear_cache)
        cache = QGroupBox(tr("settings.group.cache"))
        cache_row = QHBoxLayout(cache)
        cache_row.addWidget(self.cache_label, 1)
        cache_row.addWidget(self.cache_button)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(tr("common.ok"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr("common.cancel"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        for group in (general, preview, export, cache):
            layout.addWidget(group)
        layout.addWidget(self.buttons)

        self._refresh_cache_size()

    # ---- helpers ---------------------------------------------------------
    def _update_thumb_label(self, value: int) -> None:
        self.thumb_size_label.setText(tr("units.px", value=value))

    def _validate(self, *_args) -> bool:
        ok = True
        for edit in (self.suffix, self.subfolder):
            valid = schema.is_valid_name_part(edit.text())
            edit.setProperty("invalid", not valid)
            edit.style().unpolish(edit)
            edit.style().polish(edit)
            ok = ok and valid
        self.name_error.setVisible(not ok)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(ok)
        return ok

    def _refresh_cache_size(self) -> None:
        run_task(paths.cache_size_bytes, on_done=self._show_cache_size, guard=self)

    def _show_cache_size(self, size: int) -> None:
        self.cache_label.setText(tr("settings.cache_size", size=format_size(size)))

    def _clear_cache(self) -> None:
        self.cache_button.setEnabled(False)
        self.cache_label.setText(tr("settings.cache_clearing"))
        run_task(
            paths.clear_cache, on_done=self._cache_cleared, on_failed=self._cache_failed, guard=self
        )

    def _cache_cleared(self, _removed: int) -> None:
        self.cache_button.setEnabled(True)
        self.cache_label.setText(tr("settings.cache_cleared"))

    def _cache_failed(self, _details: str) -> None:
        self.cache_button.setEnabled(True)
        self._refresh_cache_size()

    # ---- apply -----------------------------------------------------------
    def apply(self) -> None:
        s = self.ctx.settings
        s.set("preview/quality", self.preview_quality.currentData())
        s.set("thumbs/size", self.thumb_size.value())
        s.set("files/auto_refresh", self.auto_refresh.isChecked())
        s.set("export/mode", self.export_mode.currentData())
        s.set("export/suffix", self.suffix.text())
        s.set("export/subfolder", self.subfolder.text())
        s.set("export/jpeg_quality", self.jpeg_quality.value())
        s.set("export/video_quality", self.video_quality.currentData())
        s.set("export/hw_encoder", self.hw_encoder.isChecked())
        s.set("export/keep_telemetry", self.keep_telemetry.isChecked())
        if self.theme.currentData() != s.get("ui/theme"):
            self.ctx.set_theme_choice(self.theme.currentData())
        if self.language.currentData() != s.get("ui/language"):
            self.ctx.set_language_choice(self.language.currentData())
        s.sync()

    def accept(self) -> None:
        if not self._validate():
            return
        self.apply()
        super().accept()
