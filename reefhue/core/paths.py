"""Well-known folders: app root, per-user data/cache/log folders.

No Qt here. The per-user folders follow platform conventions and can be
overridden with environment variables (used by tests):
``REEFHUE_DATA_DIR`` and ``REEFHUE_CACHE_DIR``.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from reefhue import APP_NAME

log = logging.getLogger(__name__)


def app_root() -> Path:
    """Folder that contains ``start.command``, ``ffmpeg/``, ``luts/`` and the package."""
    return Path(__file__).resolve().parents[2]


def package_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def resources_dir() -> Path:
    return package_dir() / "resources"


def _platform_data_base(platform: str = sys.platform, env: dict | None = None) -> Path:
    env = os.environ if env is None else env
    home = Path.home()
    if platform == "darwin":
        return home / "Library" / "Application Support"
    if platform.startswith("win"):
        return Path(env.get("APPDATA") or home / "AppData" / "Roaming")
    return Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")


def _platform_cache_base(platform: str = sys.platform, env: dict | None = None) -> Path:
    env = os.environ if env is None else env
    home = Path.home()
    if platform == "darwin":
        return home / "Library" / "Caches"
    if platform.startswith("win"):
        return Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
    return Path(env.get("XDG_CACHE_HOME") or home / ".cache")


def data_dir(create: bool = True) -> Path:
    """Per-user app data folder (settings-adjacent data, presets, logs, user LUTs)."""
    override = os.environ.get("REEFHUE_DATA_DIR")
    path = Path(override) if override else _platform_data_base() / APP_NAME
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir(create: bool = True) -> Path:
    """Per-user cache folder (thumbnails, previews). Never inside a project folder."""
    override = os.environ.get("REEFHUE_CACHE_DIR")
    if override:
        path = Path(override)
    elif sys.platform.startswith("win"):
        path = _platform_cache_base() / APP_NAME / "Cache"
    else:
        path = _platform_cache_base() / APP_NAME
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir(create: bool = True) -> Path:
    path = data_dir(create) / "logs"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def ffmpeg_dir() -> Path:
    """The user-supplied FFmpeg folder next to the app (SPEC §3)."""
    return app_root() / "ffmpeg"


def cache_size_bytes(path: Path | None = None) -> int:
    path = cache_dir() if path is None else path
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def clear_cache(path: Path | None = None) -> int:
    """Delete everything *inside* the app cache folder. Returns the number of removed entries.

    Only ever touches the app cache folder itself; refuses obviously wrong
    targets (filesystem root, home folder) as a safety net.
    """
    path = (cache_dir() if path is None else path).resolve()
    if path == Path(path.anchor) or path == Path.home().resolve():
        raise ValueError(f"refusing to clear unsafe cache path: {path}")
    removed = 0
    if not path.exists():
        return 0
    for child in path.iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed += 1
        except OSError as exc:  # keep going; report in log
            log.warning("could not remove cache entry %s: %s", child, exc)
    log.info("cleared cache %s (%d entries)", path, removed)
    return removed
