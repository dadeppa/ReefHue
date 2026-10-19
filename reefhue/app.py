"""QApplication setup: logging, theme, i18n, main window, background FFmpeg check."""

from __future__ import annotations

import argparse
import logging
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from reefhue import APP_NAME, APP_ORG, __version__, i18n
from reefhue.core.logging_setup import setup_logging
from reefhue.io import ffmpeg as ffmpeg_io
from reefhue.ui.context import AppContext
from reefhue.ui.error_dialog import install_excepthook
from reefhue.ui.icons import icon
from reefhue.ui.main_window import MainWindow
from reefhue.ui.settings_store import SettingsStore
from reefhue.ui.theme import ThemeManager
from reefhue.ui.workers import run_task

log = logging.getLogger(__name__)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="reefhue", add_help=True)
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    # Developer aid for smoke tests of the launchers; not shown in the UI.
    parser.add_argument("--quit-after-ms", type=int, default=0, help=argparse.SUPPRESS)
    return parser.parse_known_args(argv[1:])[0]


def create_context(app: QApplication, settings: SettingsStore | None = None) -> AppContext:
    settings = settings or SettingsStore()
    theme = ThemeManager(app)
    ctx = AppContext(settings, theme)
    theme.apply(settings.get("ui/theme"))
    i18n.set_language(ctx.resolve_language_choice(settings.get("ui/language")))
    return ctx


def start_ffmpeg_check(window: MainWindow) -> None:
    def failed(_details: str) -> None:
        window.apply_ffmpeg_status(ffmpeg_io.unavailable_status())

    run_task(ffmpeg_io.check_ffmpeg, on_done=window.apply_ffmpeg_status, on_failed=failed)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    args = _parse_args(argv)
    setup_logging()

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    app.setApplicationVersion(__version__)
    install_excepthook()

    ctx = create_context(app)
    app.setWindowIcon(icon("waves", ctx.color("accent")))
    ctx.theme_changed.connect(lambda _name: app.setWindowIcon(icon("waves", ctx.color("accent"))))

    window = MainWindow(ctx)
    window.show()
    start_ffmpeg_check(window)

    if args.quit_after_ms > 0:
        QTimer.singleShot(args.quit_after_ms, window.close)

    code = app.exec()
    log.info("ReefHue exiting with code %s", code)
    return code
