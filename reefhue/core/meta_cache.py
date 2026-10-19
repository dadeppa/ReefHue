"""Metadata cache per project folder in the app cache (speeds up re-opening). No Qt.

Only a cache: losing it costs time, never data. Entries are valid while the
file's size and mtime are unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from reefhue.core import paths

log = logging.getLogger(__name__)

CACHE_VERSION = 1


def cache_file(folder: Path) -> Path:
    key = hashlib.sha1(str(folder.resolve()).encode("utf-8")).hexdigest()
    directory = paths.cache_dir() / "meta"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{key}.json"


def load(folder: Path) -> dict[str, dict[str, Any]]:
    path = cache_file(folder)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return {}
    files = data.get("files")
    return files if isinstance(files, dict) else {}


def save(folder: Path, files: dict[str, dict[str, Any]]) -> None:
    path = cache_file(folder)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps({"version": CACHE_VERSION, "files": files}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("could not write metadata cache %s: %s", path, exc)


def lookup(cache: dict, rel_path: str, size: int, mtime: float) -> dict | None:
    record = cache.get(rel_path)
    if record and record.get("size") == size and abs(float(record.get("mtime", -1)) - mtime) < 1e-3:
        return record
    return None
