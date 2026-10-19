from __future__ import annotations

import threading

import pytest

from reefhue.ui import theme
from tests.conftest import wait_until


@pytest.mark.parametrize(
    "choice, os_dark, expected",
    [
        ("light", True, "light"),
        ("dark", False, "dark"),
        ("system", True, "dark"),
        ("system", False, "light"),
        ("bogus", True, "dark"),
    ],
)
def test_resolve_theme(choice, os_dark, expected):
    assert theme.resolve_theme(choice, os_dark) == expected


def test_palettes_have_identical_tokens_and_render_qss():
    from reefhue.core import paths

    template = (paths.resources_dir() / "themes" / "base.qss").read_text(encoding="utf-8")
    assert set(theme.PALETTES["light"]) == set(theme.PALETTES["dark"])
    for palette in theme.PALETTES.values():
        qss = theme.render_qss(template, {**palette, "check_image": "/tmp/check.svg"})
        assert "@" not in qss


def test_theme_manager_switches_at_runtime(qapp, ctx):
    from PySide6.QtGui import QPalette

    seen = []
    ctx.theme_changed.connect(seen.append)
    assert ctx.set_theme_choice("dark") == "dark"
    assert qapp.palette().color(QPalette.Window).name() == theme.PALETTES["dark"]["bg"]
    assert theme.PALETTES["dark"]["bg"] in qapp.styleSheet()
    assert ctx.set_theme_choice("light") == "light"
    assert qapp.palette().color(QPalette.Window).name() == theme.PALETTES["light"]["bg"]
    assert seen == ["dark", "light"]
    assert ctx.settings.get("ui/theme") == "light"


def test_run_task_delivers_result_on_ui_thread(qapp):
    from reefhue.ui.workers import run_task

    main_thread = threading.get_ident()
    worker_thread = []
    result = []

    def work():
        worker_thread.append(threading.get_ident())
        return 42

    run_task(work, on_done=lambda r: result.append((r, threading.get_ident())))
    assert wait_until(lambda: result)
    assert result[0] == (42, main_thread)
    assert worker_thread[0] != main_thread


def test_run_task_reports_failure(qapp):
    from reefhue.ui.workers import run_task

    errors = []

    def boom():
        raise RuntimeError("kaputt")

    run_task(boom, on_failed=errors.append)
    assert wait_until(lambda: errors)
    assert "kaputt" in errors[0]


def test_run_task_skips_callback_when_guard_destroyed(qapp):
    import shiboken6
    from PySide6.QtCore import QObject

    from reefhue.ui import workers

    guard = QObject()
    called = []
    gate = threading.Event()

    def work():
        gate.wait(5)
        return 1

    workers.run_task(work, on_done=called.append, guard=guard)
    shiboken6.delete(guard)
    gate.set()
    assert wait_until(lambda: workers.pending_count() == 0)
    assert called == []
