"""Small UI helpers."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, QDateTime, QLocale, QTime, QTimeZone, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDateTimeEdit, QWidget

from reefhue.i18n import current_language, tr


def format_number(value: float, decimals: int = 1) -> str:
    return QLocale(current_language()).toString(float(value), "f", decimals)


def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return tr("units.bytes", value=str(num_bytes))
    for key, factor in (("units.kb", 1024), ("units.mb", 1024**2), ("units.gb", 1024**3)):
        if num_bytes < factor * 1024 or key == "units.gb":
            return tr(key, value=format_number(num_bytes / factor))
    return tr("units.bytes", value=str(num_bytes))  # pragma: no cover


def open_in_file_manager(path: Path) -> bool:
    """Open a folder in Finder / Explorer (read-only action)."""
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def reveal_in_file_manager(path: Path) -> None:
    """Show ``path`` selected in Finder / Explorer (read-only, non-blocking)."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif sys.platform.startswith("win"):
        subprocess.Popen(["explorer", f"/select,{path}"])
    else:
        open_in_file_manager(path.parent)


def plain_menu_text(text: str) -> str:
    """Strip mnemonic ampersands and a trailing ellipsis (for dialog titles)."""
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&").rstrip("…").rstrip(".")


# Wall-clock datetimes are represented as UTC QDateTimes so that Qt never applies
# DST/time-zone adjustments (e.g. 02:30 on a DST changeover day stays 02:30).
def to_qdatetime(value: datetime) -> QDateTime:
    return QDateTime(QDate(value.year, value.month, value.day),
                     QTime(value.hour, value.minute, value.second), QTimeZone.utc())


def from_qdatetime(value: QDateTime) -> datetime:
    d, t = value.date(), value.time()
    return datetime(d.year(), d.month(), d.day(), t.hour(), t.minute(), t.second())


def make_datetime_edit(parent: QWidget | None = None) -> QDateTimeEdit:
    edit = QDateTimeEdit(parent)
    edit.setTimeZone(QTimeZone.utc())
    edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
    edit.setCalendarPopup(True)
    return edit
