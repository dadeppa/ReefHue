"""Keep the computer awake while exporting (SPEC §14). No Qt.

macOS: a ``caffeinate -i -w <pid>`` child process (ends with us even after a crash).
Windows: ``SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)``.
Elsewhere: no-op.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class SleepInhibitor:
    def __init__(self, platform: str = sys.platform) -> None:
        self.platform = platform
        self._proc: subprocess.Popen | None = None
        self.active = False

    def start(self) -> None:
        if self.active:
            return
        try:
            if self.platform == "darwin":
                self._proc = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif self.platform.startswith("win"):
                import ctypes

                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)  # type: ignore[attr-defined]
            self.active = True
        except Exception as exc:  # noqa: BLE001 – never block an export because of this
            log.warning("could not prevent system sleep: %s", exc)

    def stop(self) -> None:
        if not self.active:
            return
        try:
            if self._proc is not None:
                self._proc.terminate()
                self._proc.wait(timeout=5)
                self._proc = None
            elif self.platform.startswith("win"):
                import ctypes

                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log.warning("could not release sleep inhibition: %s", exc)
        self.active = False
