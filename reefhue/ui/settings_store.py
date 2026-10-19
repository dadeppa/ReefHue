"""Typed access to QSettings using the schema in :mod:`reefhue.core.app_settings`."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QByteArray, QSettings

from reefhue import APP_NAME, APP_ORG
from reefhue.core import app_settings as schema

RECENT_KEY = "recent/folders"


class SettingsStore:
    def __init__(self, qsettings: QSettings | None = None) -> None:
        self._s = qsettings if qsettings is not None else QSettings(APP_ORG, APP_NAME)

    @property
    def qsettings(self) -> QSettings:
        return self._s

    # ---- schema values ------------------------------------------------
    def get(self, key: str) -> Any:
        return schema.coerce(key, self._s.value(key))

    def set(self, key: str, value: Any) -> None:
        self._s.setValue(key, schema.coerce(key, value))

    # ---- recent folders -------------------------------------------------
    def recent_folders(self) -> list[str]:
        return schema.coerce_recent(self._s.value(RECENT_KEY))

    def set_recent_folders(self, folders: list[str]) -> None:
        self._s.setValue(RECENT_KEY, list(folders))

    def push_recent_folder(self, folder: str) -> list[str]:
        folders = schema.push_recent(self.recent_folders(), folder)
        self.set_recent_folders(folders)
        return folders

    def remove_recent_folder(self, folder: str) -> list[str]:
        folders = [f for f in self.recent_folders() if f != folder]
        self.set_recent_folders(folders)
        return folders

    # ---- window state (opaque Qt blobs) ---------------------------------
    def get_bytes(self, key: str) -> QByteArray | None:
        value = self._s.value(key)
        return value if isinstance(value, QByteArray) and not value.isEmpty() else None

    def set_bytes(self, key: str, value: QByteArray) -> None:
        self._s.setValue(key, value)

    def sync(self) -> None:
        self._s.sync()
