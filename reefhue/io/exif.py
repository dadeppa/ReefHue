"""Photo metadata via Pillow (EXIF). HEIC/HEIF via pillow-heif when available. No Qt."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from reefhue.core.timeutil import format_wallclock, parse_wallclock

log = logging.getLogger(__name__)

EXIF_IFD = 0x8769
TAG_DATETIME_ORIGINAL = 36867
TAG_MAKE = 271
TAG_MODEL = 272
TAG_ORIENTATION = 274
ORIENTATION_TO_ROTATION = {3: 180, 6: 90, 8: 270}  # degrees clockwise for display

_heif_state: bool | None = None


def ensure_heif() -> bool:
    """Register the HEIF opener once. False if pillow-heif is unavailable."""
    global _heif_state
    if _heif_state is None:
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
            _heif_state = True
        except Exception as exc:  # noqa: BLE001 – optional dependency
            log.warning("HEIC/HEIF support unavailable: %s", exc)
            _heif_state = False
    return _heif_state


def _bit_depth(mode: str) -> int:
    if mode in ("I;16", "I;16B", "I;16L", "I;16N", "I", "RGB;16", "RGBA;16"):
        return 16
    if mode == "F":
        return 32
    return 8


def camera_name(make: Any, model: Any) -> str | None:
    make_s = str(make or "").strip().strip("\x00")
    model_s = str(model or "").strip().strip("\x00")
    if make_s and model_s.lower().startswith(make_s.lower()):
        return model_s
    joined = f"{make_s} {model_s}".strip()
    return joined or None


def read_photo_metadata(path: Path) -> tuple[dict[str, Any], str | None]:
    """Return ``(media_fields, datetime_original_iso_or_None)``. Raises on unreadable files."""
    if path.suffix.lower() in (".heic", ".heif"):
        ensure_heif()
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            exif_ifd = exif.get_ifd(EXIF_IFD) if exif else {}
            dt = parse_wallclock(exif_ifd.get(TAG_DATETIME_ORIGINAL)) if exif_ifd else None
            orientation = int(exif.get(TAG_ORIENTATION, 1) or 1) if exif else 1
            media = {
                "type": "photo",
                # GoPro (and many cameras) store JPEGs as MPO (multi-picture JPEG); it is a JPEG.
                "codec": {"mpo": "jpeg"}.get((img.format or "").lower(), (img.format or "").lower()) or None,
                "pix_fmt": img.mode,
                "bit_depth": _bit_depth(img.mode),
                "width": img.width,
                "height": img.height,
                "rotation_meta": ORIENTATION_TO_ROTATION.get(orientation, 0),
                "camera": camera_name(exif.get(TAG_MAKE), exif.get(TAG_MODEL)) if exif else None,
            }
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise OSError(f"cannot read image {path.name}: {exc}") from exc
    return media, format_wallclock(dt)
