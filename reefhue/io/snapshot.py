"""Write a corrected video frame as JPEG (SPEC §6 "Snapshot"). No Qt. Blocking – use a worker.

Safe write: ``<name>.part`` first, renamed on success; an existing file is never
overwritten (see :func:`reefhue.core.timecode.unique_path`).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import numpy as np
import piexif
from PIL import Image

from reefhue import __version__

JPEG_QUALITY = 95


def exif_bytes(capture: datetime | None, camera: str | None) -> bytes:
    zeroth = {piexif.ImageIFD.Software: f"ReefHue {__version__}".encode("ascii")}
    if camera:
        zeroth[piexif.ImageIFD.Model] = camera.encode("utf-8", "replace")
    exif_ifd = {}
    if capture is not None:
        stamp = capture.strftime("%Y:%m:%d %H:%M:%S").encode("ascii")
        exif_ifd[piexif.ExifIFD.DateTimeOriginal] = stamp
        exif_ifd[piexif.ExifIFD.DateTimeDigitized] = stamp
    return piexif.dump({"0th": zeroth, "Exif": exif_ifd})


def write_jpeg(img_u8: np.ndarray, target: Path, capture: datetime | None = None, camera: str | None = None,
               quality: int = JPEG_QUALITY) -> Path:
    if target.exists():
        raise FileExistsError(target)
    part = target.with_name(target.name + ".part")
    try:
        Image.fromarray(img_u8, "RGB").save(part, "JPEG", quality=quality, subsampling=0,
                                             exif=exif_bytes(capture, camera))
        os.replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return target
