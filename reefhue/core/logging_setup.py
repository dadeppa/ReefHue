"""Rotating log file in the app data folder (SPEC §15). No Qt."""

from __future__ import annotations

import logging
import platform
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from reefhue import __version__
from reefhue.core import paths

LOG_FILE_NAME = "reefhue.log"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 5
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(directory: Path | None = None, level: int = logging.INFO) -> Path:
    """Configure root logging (rotating file + stderr). Returns the log file path.

    Safe to call more than once: previously installed ReefHue handlers are replaced.
    """
    directory = paths.log_dir() if directory is None else directory
    directory.mkdir(parents=True, exist_ok=True)
    log_file = directory / LOG_FILE_NAME

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_reefhue", False):
            root.removeHandler(handler)
            handler.close()

    file_handler = RotatingFileHandler(
        log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    file_handler._reefhue = True  # type: ignore[attr-defined]

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(logging.Formatter(_FORMAT))
    stream_handler._reefhue = True  # type: ignore[attr-defined]

    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    root.setLevel(level)

    logging.getLogger(__name__).info(
        "ReefHue %s starting – Python %s on %s (%s)",
        __version__,
        platform.python_version(),
        platform.platform(),
        platform.machine(),
    )
    return log_file
