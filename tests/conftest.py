"""Shared fixtures. Qt runs headless; app data/cache go to temp folders."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("REEFHUE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("REEFHUE_CACHE_DIR", str(tmp_path / "appcache"))
    yield


@pytest.fixture(autouse=True)
def reset_language():
    from reefhue import i18n

    i18n.set_language("en")
    yield
    i18n.set_language("en")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def settings_store(tmp_path, qapp):
    from PySide6.QtCore import QSettings

    from reefhue.ui.settings_store import SettingsStore

    qs = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    return SettingsStore(qs)


@pytest.fixture
def ctx(qapp, settings_store):
    from reefhue.app import create_context

    context = create_context(qapp, settings_store)
    yield context
    context.theme.apply("light")


@pytest.fixture
def window(ctx):
    from reefhue.ui.main_window import MainWindow

    win = MainWindow(ctx)
    win.resize(1200, 800)
    yield win
    win.close()
    win.thumbs.wait()
    win.controller.wait_for_metadata()
    _delete_now(win)


def _delete_now(obj) -> None:
    """deleteLater() only runs inside an event loop; tests must free widgets explicitly,
    otherwise every later stylesheet change restyles all leaked windows."""
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    obj.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def wait_until(predicate, timeout: float = 5.0) -> bool:
    """Spin the Qt event loop until ``predicate()`` is true or the timeout passes."""
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return predicate()
