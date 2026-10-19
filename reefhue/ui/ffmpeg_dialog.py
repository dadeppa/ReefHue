"""Non-modal FFmpeg status/help dialog (startup notice and Help ▸ FFmpeg help). SPEC §3 + Addendum A."""

from __future__ import annotations

import html
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from reefhue.core import paths
from reefhue.i18n import tr
from reefhue.io import ffmpeg as ffmpeg_io
from reefhue.io.ffmpeg import FfmpegStatus, ToolStatus
from reefhue.ui.context import AppContext
from reefhue.ui.util import open_in_file_manager
from reefhue.ui.workers import run_task

VERIFY_WINDOWS = 'ffmpeg -hide_banner -encoders | findstr "libx264 libx265"'


def _tool_line(tool: ToolStatus) -> str:
    if tool.ok:
        line = tr("ffmpeg.dialog.tool_ok", tool=tool.name, path=html.escape(str(tool.path)),
                  source=tr(f"ffmpeg.source.{tool.source}"))
        if tool.version:
            line += "<br>&nbsp;&nbsp;" + html.escape(tool.version)
        return line
    if tool.problem == ffmpeg_io.NOT_FOUND:
        return tr("ffmpeg.dialog.tool_missing", tool=tool.name)
    return tr("ffmpeg.dialog.tool_failed", tool=tool.name, path=html.escape(str(tool.path)))


def status_lines(status: FfmpegStatus) -> list[str]:
    """Which binaries are in use (A1), their architecture (A2) and encoders (A3)."""
    lines = [_tool_line(status.ffmpeg), _tool_line(status.ffprobe)]
    archs = status.ffmpeg.archs
    if archs:
        names = " + ".join(sorted(archs))
        key = "ffmpeg.dialog.arch_rosetta" if status.intel_on_apple_silicon else "ffmpeg.dialog.arch_native"
        lines.append(tr(key, archs=names))
    if status.encoders is not None:
        marks = " · ".join(f"{e} {'✓' if e in status.encoders else '✗'}" for e in ffmpeg_io.REQUIRED_ENCODERS)
        lines.append(tr("ffmpeg.dialog.encoders", list=marks))
        lines.append(tr("ffmpeg.dialog.hw_encoders_ok" if status.hw_encoders_available
                        else "ffmpeg.dialog.hw_encoders_missing"))
    return lines


def help_texts(status: FfmpegStatus, platform: str = sys.platform) -> list[tuple[str, str]]:
    """``[(i18n_key, command_or_empty)]`` explaining how to fix problems and warnings."""
    result: list[tuple[str, str]] = []
    tools = (status.ffmpeg, status.ffprobe)
    for tool in tools:
        if tool.problem == ffmpeg_io.NOT_EXECUTABLE and tool.path is not None:
            result.append(("ffmpeg.dialog.help_not_executable", f'chmod +x "{tool.path}"'))
    if status.needs_rosetta_install:
        result.append(("ffmpeg.dialog.help_rosetta_install", ffmpeg_io.ROSETTA_COMMAND))
    elif any(t.problem == ffmpeg_io.FAILED_TO_RUN for t in tools):
        if platform == "darwin":
            result.append(("ffmpeg.dialog.help_quarantine", f'xattr -dr com.apple.quarantine "{status.folder}"'))
        result.append(("ffmpeg.dialog.help_failed", ""))
    if status.rosetta:
        result.append(("ffmpeg.dialog.help_rosetta_notice", ffmpeg_io.BREW_COMMAND))
    if status.missing_encoders:
        result.append(("ffmpeg.dialog.help_missing_encoders", ""))
    return result


def install_texts(platform: str = sys.platform) -> list[tuple[str, str]]:
    """How to get FFmpeg (A5): macOS – Homebrew (recommended), static arm64 build, Intel fallback."""
    if platform == "darwin":
        return [
            ("ffmpeg.dialog.install_mac_brew", ffmpeg_io.BREW_COMMAND),
            ("ffmpeg.dialog.install_mac_static", "{folder}"),
            ("ffmpeg.dialog.install_mac_rosetta", ""),
            ("ffmpeg.dialog.verify", ffmpeg_io.VERIFY_COMMAND),
        ]
    verify = VERIFY_WINDOWS if platform.startswith("win") else ffmpeg_io.VERIFY_COMMAND
    return [("ffmpeg.dialog.install_other", "{folder}"), ("ffmpeg.dialog.verify", verify)]


def _block(key: str, command: str, status: FfmpegStatus | None, folder) -> str:
    kwargs = {"encoders": ", ".join(status.missing_encoders)} if status is not None else {}
    text = tr(key, **kwargs)
    if command:
        command = command.replace("{folder}", str(folder))
        text += f"<br><code>{html.escape(command)}</code>"
    return text


class FfmpegDialog(QDialog):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setModal(False)
        self.setMinimumSize(600, 420)
        self._checking = False

        self.heading = QLabel()
        font = self.heading.font()
        font.setPointSizeF(font.pointSizeF() * 1.3)
        font.setBold(True)
        self.heading.setFont(font)
        self.explanation = QLabel(wordWrap=True)
        self.tools = QLabel(wordWrap=True)
        self.tools.setTextFormat(Qt.RichText)
        self.tools.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.tools.setObjectName("MutedLabel")
        self.help = QLabel(wordWrap=True)
        self.help.setTextFormat(Qt.RichText)
        self.help.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.help.setObjectName("Notice")
        self.install = QLabel(wordWrap=True)
        self.install.setTextFormat(Qt.RichText)
        self.install.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.download = QLabel()
        self.download.setTextFormat(Qt.RichText)
        self.download.setOpenExternalLinks(True)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 8, 0)
        body_layout.setSpacing(10)
        for widget in (self.explanation, self.tools, self.help, self.install, self.download):
            body_layout.addWidget(widget)
        body_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(body)

        self.buttons = QDialogButtonBox()
        self.show_folder_button = QPushButton()
        self.check_button = QPushButton()
        self.close_button = QPushButton()
        self.buttons.addButton(self.show_folder_button, QDialogButtonBox.ActionRole)
        self.buttons.addButton(self.check_button, QDialogButtonBox.ActionRole)
        self.buttons.addButton(self.close_button, QDialogButtonBox.RejectRole)
        for button in (self.show_folder_button, self.close_button):
            button.setAutoDefault(False)
        self.check_button.setDefault(True)
        self.show_folder_button.clicked.connect(self._show_folder)
        self.check_button.clicked.connect(self.check_again)
        self.close_button.clicked.connect(self.close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(self.heading)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.buttons)

        ctx.language_changed.connect(self.retranslate_ui)
        ctx.ffmpeg_status_changed.connect(self._on_status)
        self.retranslate_ui()

    def _on_status(self, _status) -> None:
        self._checking = False
        self.retranslate_ui()

    def check_again(self) -> None:
        if self._checking:
            return
        self._checking = True
        self.retranslate_ui()
        run_task(ffmpeg_io.check_ffmpeg, on_done=self.ctx.set_ffmpeg_status, on_failed=self._check_failed)

    def _check_failed(self, _details: str) -> None:
        self._checking = False
        self.retranslate_ui()

    def _show_folder(self) -> None:
        folder = self.ctx.ffmpeg_status.folder if self.ctx.ffmpeg_status else paths.ffmpeg_dir()
        folder.mkdir(parents=True, exist_ok=True)  # the app's own ffmpeg/ folder, never user media
        open_in_file_manager(folder)

    def retranslate_ui(self, *_args) -> None:
        status = self.ctx.ffmpeg_status
        self.setWindowTitle(tr("ffmpeg.dialog.title"))
        if self._checking or status is None:
            self.heading.setText(tr("ffmpeg.dialog.heading_checking"))
        elif status.available and status.warnings:
            self.heading.setText(tr("ffmpeg.dialog.heading_warnings"))
        else:
            self.heading.setText(tr(f"ffmpeg.dialog.heading_{status.state}"))
        self.explanation.setText(tr("ffmpeg.dialog.explanation"))
        folder = status.folder if status else paths.ffmpeg_dir()
        if status is not None:
            self.tools.setText("<br>".join(status_lines(status)))
            problems = [_block(k, c, status, folder) for k, c in help_texts(status)]
            self.help.setText("<br><br>".join(problems))
            self.help.setVisible(bool(problems))
            show_install = not status.available or bool(status.warnings)
        else:
            self.tools.setText("")
            self.help.setVisible(False)
            show_install = True
        install = [_block(k, c, None, folder) for k, c in install_texts()]
        self.install.setText(f"<b>{html.escape(tr('ffmpeg.dialog.install_title'))}</b><br>" + "<br><br>".join(install))
        self.install.setVisible(show_install)
        self.download.setText(tr("ffmpeg.dialog.download", url=ffmpeg_io.DOWNLOAD_URL))
        self.show_folder_button.setText(tr("ffmpeg.dialog.show_folder"))
        self.check_button.setText(tr("ffmpeg.dialog.check_again"))
        self.check_button.setEnabled(not self._checking)
        self.close_button.setText(tr("common.close"))
