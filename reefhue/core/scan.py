"""Folder scanning and file fingerprints (SPEC §5). Read-only. No Qt."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from reefhue.core import sidecar

log = logging.getLogger(__name__)

FINGERPRINT_ALGO = "head-tail-sha1"
FINGERPRINT_CHUNK = 64 * 1024
# Package-like folders that must never be descended into when scanning recursively.
SKIP_DIR_SUFFIXES = (".photoslibrary", ".app", ".fcpbundle", ".imovielibrary", ".lrdata", ".tvlibrary")
_OWN_FILES = {
    sidecar.SIDECAR_NAME,
    sidecar.SIDECAR_NAME + sidecar.BACKUP_SUFFIX,
    sidecar.SIDECAR_NAME + sidecar.TMP_SUFFIX,
    sidecar.SIDECAR_NAME + sidecar.BACKUP_SUFFIX + sidecar.TMP_SUFFIX,
}


@dataclass(frozen=True)
class ScannedFile:
    rel_path: str  # POSIX-style, relative to the project folder
    size: int
    mtime: float
    created: float

    @property
    def name(self) -> str:
        return self.rel_path.rsplit("/", 1)[-1]

    @property
    def subfolder(self) -> str:
        return self.rel_path.rsplit("/", 1)[0] if "/" in self.rel_path else ""


def _created(st: os.stat_result) -> float:
    birth = getattr(st, "st_birthtime", None)  # macOS
    if birth:
        return float(birth)
    if sys.platform.startswith("win"):
        return float(st.st_ctime)  # creation time on Windows
    return float(st.st_mtime)


def _skip_name(name: str) -> bool:
    return name.startswith(".") or name.startswith("~$") or name in _OWN_FILES


ACCESS_OK, ACCESS_READ_ONLY, ACCESS_NONE, ACCESS_MISSING = "ok", "read_only", "no_access", "missing"


def folder_access(folder: Path) -> str:
    """How ReefHue may use ``folder``: ok, read_only (cannot write), no_access or missing.

    Writing is only checked with ``os.access`` – nothing is created in the folder.
    """
    try:
        if not folder.is_dir():
            return ACCESS_MISSING
        with os.scandir(folder) as it:
            next(it, None)
    except FileNotFoundError:
        return ACCESS_MISSING
    except OSError:
        return ACCESS_NONE
    return ACCESS_OK if os.access(folder, os.W_OK | os.X_OK) else ACCESS_READ_ONLY


def scan_folder(folder: Path, recursive: bool) -> list[ScannedFile]:
    """List regular files (no hidden files, no ReefHue sidecar files). Symlinked folders are not followed.

    An unreadable subfolder is skipped; an unreadable project folder raises ``OSError``
    (otherwise every file would look deleted).
    """
    results: list[ScannedFile] = []
    stack = [(folder, "")]
    while stack:
        directory, prefix = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            if directory == folder:
                raise
            log.warning("cannot scan %s: %s", directory, exc)
            continue
        for entry in entries:
            if _skip_name(entry.name):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if recursive and not entry.name.lower().endswith(SKIP_DIR_SUFFIXES):
                        stack.append((Path(entry.path), f"{prefix}{entry.name}/"))
                    continue
                if not entry.is_file():
                    continue
                st = entry.stat()
            except OSError as exc:
                log.warning("cannot stat %s: %s", entry.path, exc)
                continue
            results.append(ScannedFile(f"{prefix}{entry.name}", st.st_size, st.st_mtime, _created(st)))
    results.sort(key=lambda f: f.rel_path.casefold())
    return results


def fingerprint(path: Path, size: int | None = None) -> str:
    """SHA-1 over size + first and last 64 KiB. Cheap identity for rename matching."""
    size = path.stat().st_size if size is None else size
    digest = hashlib.sha1(str(size).encode("ascii"))
    with open(path, "rb") as fh:
        digest.update(fh.read(FINGERPRINT_CHUNK))
        if size > FINGERPRINT_CHUNK:
            fh.seek(max(FINGERPRINT_CHUNK, size - FINGERPRINT_CHUNK))
            digest.update(fh.read(FINGERPRINT_CHUNK))
    return digest.hexdigest()
