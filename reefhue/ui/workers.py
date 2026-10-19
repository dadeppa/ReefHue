"""Run blocking callables on the Qt thread pool and get results back on the UI thread."""

from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

import shiboken6
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

log = logging.getLogger(__name__)

_active: set["_Relay"] = set()


class _Relay(QObject):
    """Lives on the UI thread; its slots run there via queued connections."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        on_done: Callable[[Any], None] | None,
        on_failed: Callable[[str], None] | None,
        guard: QObject | None,
    ):
        super().__init__()
        self._on_done = on_done
        self._on_failed = on_failed
        self._guard = guard
        self.done.connect(self._handle_done)
        self.failed.connect(self._handle_failed)

    def _guard_alive(self) -> bool:
        return self._guard is None or shiboken6.isValid(self._guard)

    @Slot(object)
    def _handle_done(self, result: Any) -> None:
        try:
            if self._on_done is not None and self._guard_alive():
                self._on_done(result)
        finally:
            _active.discard(self)

    @Slot(str)
    def _handle_failed(self, details: str) -> None:
        try:
            if self._on_failed is not None and self._guard_alive():
                self._on_failed(details)
        finally:
            _active.discard(self)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], relay: _Relay) -> None:
        super().__init__()
        self._fn = fn
        self._relay = relay

    def run(self) -> None:  # worker thread
        try:
            result = self._fn()
        except Exception:  # noqa: BLE001 – reported to the UI thread
            details = traceback.format_exc()
            log.error("background task failed:\n%s", details)
            self._relay.failed.emit(details)
        else:
            self._relay.done.emit(result)


def run_task(
    fn: Callable[[], Any],
    on_done: Callable[[Any], None] | None = None,
    on_failed: Callable[[str], None] | None = None,
    pool: QThreadPool | None = None,
    guard: QObject | None = None,
) -> None:
    """Run ``fn()`` on a worker thread; call ``on_done(result)`` / ``on_failed(tb)`` on the UI thread.

    If ``guard`` (typically the widget that owns the callbacks) has been destroyed by
    the time the result arrives, the callbacks are skipped. Must be called from the UI thread.
    """
    relay = _Relay(on_done, on_failed, guard)
    _active.add(relay)
    (pool or QThreadPool.globalInstance()).start(_Task(fn, relay))


def pending_count() -> int:
    return len(_active)
