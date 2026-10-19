"""Help ▸ About: version, system information (copyable for bug reports), credits."""

from __future__ import annotations

import PySide6
from PySide6.QtCore import Qt, QTimer, qVersion
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from reefhue import __version__
from reefhue.core import diagnostics, paths
from reefhue.i18n import tr
from reefhue.ui.context import AppContext
from reefhue.ui.icons import pixmap


VALUE_WIDTH = 420  # px; longer values are shortened on screen (copied in full)


def ffmpeg_text(status) -> str:
    if status is None or not status.available:
        return tr("about.ffmpeg_missing")
    tool = status.ffmpeg
    parts = [diagnostics.ffmpeg_version(tool.version) or "?"]
    if tool.archs:
        parts.append("/".join(sorted(tool.archs)))
    parts.append(str(tool.path))
    return " · ".join(parts)


def system_rows(ctx: AppContext) -> list[tuple[str, str]]:
    """(label, value) rows; also the text copied for bug reports."""
    libraries = ", ".join(f"{name} {version}" for name, version in diagnostics.library_versions())
    return [
        (tr("about.row.version"), __version__),
        (tr("about.row.system"), diagnostics.platform_text()),
        (tr("about.row.python"), diagnostics.python_text()),
        (tr("about.row.qt"), f"Qt {qVersion()} · PySide6 {PySide6.__version__}"),
        (tr("about.row.libraries"), libraries),
        (tr("about.row.ffmpeg"), ffmpeg_text(ctx.ffmpeg_status)),
        (tr("about.row.data"), str(paths.data_dir(create=False))),
    ]


class AboutDialog(QDialog):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("about.title"))
        self.setMinimumWidth(480)
        self.rows = system_rows(ctx)

        logo = QLabel(alignment=Qt.AlignCenter)
        logo.setPixmap(pixmap("waves", ctx.color("accent"), 48, self.devicePixelRatioF()))
        name = QLabel(tr("app.name"), alignment=Qt.AlignCenter)
        font = name.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        font.setBold(True)
        name.setFont(font)
        tagline = QLabel(tr("about.tagline"), alignment=Qt.AlignCenter, wordWrap=True)
        version = QLabel(tr("about.version", version=__version__), alignment=Qt.AlignCenter)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(12)
        for label, value in self.rows[1:]:
            key = QLabel(label)
            key.setObjectName("MutedLabel")
            text = QLabel()
            text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            text.setText(self._display(value, text))
            form.addRow(key, text)

        credits = QLabel(tr("about.credits"), alignment=Qt.AlignCenter, wordWrap=True)
        credits.setObjectName("MutedLabel")
        credits.setFixedWidth(VALUE_WIDTH + 100)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText(tr("common.close"))
        buttons.rejected.connect(self.reject)
        self.copy_button = QPushButton(tr("about.copy_info"))
        self.copy_button.setToolTip(tr("about.copy_info_tip"))
        self.copy_button.clicked.connect(self.copy_info)
        buttons.addButton(self.copy_button, QDialogButtonBox.ActionRole)

        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SetFixedSize)  # grow with the content, never clip it
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(8)
        for widget in (logo, name, tagline, version):
            layout.addWidget(widget)
        layout.addSpacing(8)
        layout.addLayout(form)
        layout.addSpacing(8)
        layout.addWidget(credits)
        layout.addSpacing(8)
        layout.addWidget(buttons)

    @staticmethod
    def _display(value: str, label: QLabel) -> str:
        """Long values on screen: libraries three per line, paths shortened in the middle."""
        if value.count(", ") >= 3:
            items = value.split(", ")
            return "\n".join(", ".join(items[i:i + 3]) for i in range(0, len(items), 3))
        metrics = label.fontMetrics()
        if metrics.horizontalAdvance(value) > VALUE_WIDTH:
            label.setToolTip(value)
            return metrics.elidedText(value, Qt.ElideMiddle, VALUE_WIDTH)
        return value

    def info_text(self) -> str:
        return "\n".join(f"{label}: {value}" for label, value in self.rows)

    def copy_info(self) -> None:
        QGuiApplication.clipboard().setText(f"{tr('app.name')}\n{self.info_text()}")
        self.copy_button.setText(tr("error.copied"))
        QTimer.singleShot(1500, lambda: self.copy_button.setText(tr("about.copy_info")))
