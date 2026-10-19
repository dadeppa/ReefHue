"""Shared application state for the UI layer (settings, theme, language, FFmpeg)."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QLocale, QObject, Signal

from reefhue import i18n
from reefhue.core.app_settings import LANGUAGE_SYSTEM
from reefhue.io.ffmpeg import FfmpegStatus
from reefhue.ui.settings_store import SettingsStore
from reefhue.ui.theme import ThemeManager

log = logging.getLogger(__name__)


def os_ui_languages() -> list[str]:
    return list(QLocale.system().uiLanguages())


class AppContext(QObject):
    language_changed = Signal(str)      # active language code
    theme_changed = Signal(str)         # active palette name
    ffmpeg_status_changed = Signal(object)  # FfmpegStatus
    folder_changed = Signal(object)     # Path | None

    def __init__(self, settings: SettingsStore, theme: ThemeManager, parent: QObject | None = None):
        super().__init__(parent)
        self.settings = settings
        self.theme = theme
        self.ffmpeg_status: FfmpegStatus | None = None  # None = check still running
        self.current_folder: Path | None = None
        theme.theme_applied.connect(self.theme_changed)

    # ---- language -------------------------------------------------------
    def resolve_language_choice(self, choice: str) -> str:
        if choice == LANGUAGE_SYSTEM or choice not in i18n.available_languages():
            return i18n.resolve_language(os_ui_languages())
        return choice

    def set_language_choice(self, choice: str) -> str:
        """Persist the user's choice ('system' or a code) and switch at runtime."""
        if choice != LANGUAGE_SYSTEM and choice not in i18n.available_languages():
            choice = LANGUAGE_SYSTEM
        self.settings.set("ui/language", choice)
        code = i18n.set_language(self.resolve_language_choice(choice))
        self.language_changed.emit(code)
        return code

    # ---- theme ----------------------------------------------------------
    def set_theme_choice(self, choice: str) -> str:
        self.settings.set("ui/theme", choice)
        return self.theme.apply(self.settings.get("ui/theme"))

    def color(self, token: str) -> str:
        return self.theme.color(token)

    # ---- ffmpeg ---------------------------------------------------------
    @property
    def video_enabled(self) -> bool:
        return self.ffmpeg_status is not None and self.ffmpeg_status.available

    def set_ffmpeg_status(self, status: FfmpegStatus) -> None:
        self.ffmpeg_status = status
        self.ffmpeg_status_changed.emit(status)

    # ---- folder ---------------------------------------------------------
    def set_current_folder(self, folder: Path | None) -> None:
        self.current_folder = folder
        if folder is not None:
            self.settings.push_recent_folder(str(folder))
        self.folder_changed.emit(folder)
