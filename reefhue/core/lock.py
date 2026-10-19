"""Prevent the same project folder from being opened twice (SPEC §13). No Qt.

The lock lives in the app data folder (not in the user's folder), keyed by the
folder's resolved path. Stale locks from crashed processes are taken over.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import sys
from pathlib import Path

from reefhue.core import paths

log = logging.getLogger(__name__)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class FolderLock:
    def __init__(self, folder: Path, lock_dir: Path | None = None) -> None:
        self.folder = folder.resolve()
        directory = (paths.data_dir() / "locks") if lock_dir is None else lock_dir
        directory.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(str(self.folder).encode("utf-8")).hexdigest()
        self.path = directory / f"{key}.lock"
        self.held = False

    def _payload(self) -> str:
        return json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "folder": str(self.folder)})

    def _holder_alive(self) -> bool:
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if info.get("host") != socket.gethostname():
            return False
        pid = int(info.get("pid") or 0)
        return pid != os.getpid() and pid_alive(pid)

    def acquire(self) -> bool:
        """True if this process now holds the lock."""
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self._holder_alive():
                    log.info("folder %s is locked by another process", self.folder)
                    return False
                try:
                    self.path.unlink()  # stale lock
                except OSError:
                    return False
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(self._payload())
            self.held = True
            return True
        return False

    def release(self) -> None:
        if not self.held:
            return
        try:
            self.path.unlink()
        except OSError as exc:
            log.warning("could not remove lock %s: %s", self.path, exc)
        self.held = False
