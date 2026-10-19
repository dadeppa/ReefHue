"""Per-file metadata extraction (media fields, capture date + source, fingerprint). No Qt.

Capture date priority (SPEC §5): EXIF DateTimeOriginal (photos) / container
creation_time via ffprobe (videos) / filesystem date. Blocking – run in workers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reefhue.core import extensions
from reefhue.core.scan import ScannedFile, fingerprint
from reefhue.core.timeutil import format_wallclock, from_timestamp
from reefhue.io import exif, probe

log = logging.getLogger(__name__)

SOURCE_EXIF = "exif"
SOURCE_FFPROBE = "ffprobe"
SOURCE_FILESYSTEM = "filesystem"


@dataclass
class MetadataResult:
    rel_path: str
    size: int
    mtime: float
    media: dict[str, Any] = field(default_factory=dict)
    datetime_original: str | None = None
    datetime_source: str | None = None
    fingerprint: str | None = None
    error: str | None = None


def filesystem_datetime(scanned: ScannedFile) -> str | None:
    """Earliest of modification/creation time: copies from an SD card keep the
    original mtime but get a new creation time."""
    ts = min(t for t in (scanned.mtime, scanned.created) if t) if (scanned.mtime or scanned.created) else None
    return format_wallclock(from_timestamp(ts)) if ts else None


def extract(folder: Path, scanned: ScannedFile, ffprobe: Path | None) -> MetadataResult:
    path = folder / scanned.rel_path
    result = MetadataResult(scanned.rel_path, scanned.size, scanned.mtime)
    kind = extensions.kind_of(extensions.ext_of(scanned.rel_path))
    try:
        result.fingerprint = fingerprint(path, scanned.size)
    except OSError as exc:
        result.error = str(exc)
        log.warning("fingerprint failed for %s: %s", path, exc)
    dt = None
    try:
        if kind == extensions.PHOTO:
            media, dt = exif.read_photo_metadata(path)
            result.media = media
            source = SOURCE_EXIF
        elif kind == extensions.VIDEO and ffprobe is not None:
            media, dt = probe.parse_probe(probe.run_ffprobe(ffprobe, path))
            result.media = media
            source = SOURCE_FFPROBE
        else:
            result.media = {"type": kind if kind != extensions.OTHER else None}
            source = None
    except Exception as exc:  # noqa: BLE001 – unreadable files must never crash the scan
        log.warning("metadata failed for %s: %s", path, exc)
        result.error = str(exc)
        result.media = {"type": kind if kind != extensions.OTHER else None}
        source = None
    if dt:
        result.datetime_original, result.datetime_source = dt, source
    else:
        result.datetime_original = filesystem_datetime(scanned)
        result.datetime_source = SOURCE_FILESYSTEM
    return result
