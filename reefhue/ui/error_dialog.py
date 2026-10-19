"""Friendly error dialog with "Copy details", and a global exception hook (SPEC §15)."""

from __future__ import annotations

import logging
import sys
import threading
import traceback

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from reefhue.core import paths
from reefhue.i18n import tr
from reefhue.ui.util import open_in_file_manager, plain_menu_text

log = logging.getLogger(__name__)


class ErrorDialog(QDialog):
    def __init__(self, message: str, details: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("error.title"))
        self.setMinimumWidth(520)
        self._details = details

        text = QLabel(message, wordWrap=True)
        self.details_view = QPlainTextEdit(details)
        self.details_view.setReadOnly(True)
        self.details_view.setVisible(bool(details))
        self.details_view.setMinimumHeight(160)

        buttons = QDialogButtonBox()
        self.copy_button = QPushButton(tr("error.copy_details"))
        self.copy_button.setEnabled(bool(details))
        self.copy_button.clicked.connect(self.copy_details)
        log_button = QPushButton(plain_menu_text(tr("action.open_log_folder")))
        log_button.clicked.connect(lambda: open_in_file_manager(paths.log_dir()))
        close_button = QPushButton(tr("common.close"))
        close_button.clicked.connect(self.accept)
        buttons.addButton(self.copy_button, QDialogButtonBox.ActionRole)
        buttons.addButton(log_button, QDialogButtonBox.ActionRole)
        buttons.addButton(close_button, QDialogButtonBox.AcceptRole)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.addWidget(text)
        layout.addWidget(self.details_view, 1)
        layout.addWidget(buttons)

    def copy_details(self) -> None:
        QGuiApplication.clipboard().setText(self._details)
        self.copy_button.setText(tr("error.copied"))


def show_error(message: str, details: str = "", parent: QWidget | None = None) -> None:
    ErrorDialog(message, details, parent).exec()


_showing = False


def _excepthook(exc_type, exc, tb) -> None:
    global _showing
    details = "".join(traceback.format_exception(exc_type, exc, tb))
    log.error("unhandled exception:\n%s", details)
    if issubclass(exc_type, KeyboardInterrupt):
        QApplication.quit()
        return
    if _showing or QApplication.instance() is None:
        return

    def _show() -> None:
        global _showing
        _showing = True
        try:
            show_error(tr("error.unexpected"), details, QApplication.activeWindow())
        finally:
            _showing = False

    QTimer.singleShot(0, _show)


def _thread_excepthook(args) -> None:
    details = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    log.error("unhandled exception in thread %s:\n%s", args.thread, details)


def install_excepthook() -> None:
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
