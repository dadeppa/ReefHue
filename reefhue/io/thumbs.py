"""Thumbnails in the app cache (never in the project folder). No Qt. Blocking – run in workers."""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from pathlib import Path

from PIL import Image, ImageOps

from reefhue.core import paths
from reefhue.io.exif import ensure_heif

log = logging.getLogger(__name__)

THUMB_MAX_PX = 320
THUMB_VERSION = 1
VIDEO_TIMEOUT_S = 60


def thumb_dir() -> Path:
    path = paths.cache_dir() / "thumbs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thumb_path(abs_path: Path, size: int, mtime: float) -> Path:
    key = f"{abs_path.resolve()}|{size}|{mtime:.3f}|{THUMB_MAX_PX}|v{THUMB_VERSION}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return thumb_dir() / digest[:2] / f"{digest}.jpg"


def _atomic_target(dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    return dst.with_name(dst.stem + f".{os.getpid()}.part.jpg")


def make_photo_thumbnail(src: Path, dst: Path, max_px: int = THUMB_MAX_PX) -> Path:
    if src.suffix.lower() in (".heic", ".heif"):
        ensure_heif()
    tmp = _atomic_target(dst)
    with Image.open(src) as img:
        img.draft("RGB", (max_px * 2, max_px * 2))  # fast JPEG downscale on decode
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (128, 128, 128))
            background.paste(img, mask=img.getchannel("A"))
            img = background
        elif img.mode != "RGB":
            img = ImageOps.autocontrast(img.convert("RGB")) if img.mode in ("I", "I;16", "F") else img.convert("RGB")
        img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
        img.save(tmp, "JPEG", quality=85)
    os.replace(tmp, dst)
    return dst


def make_video_thumbnail(
    ffmpeg: Path, src: Path, dst: Path, duration_s: float | None, max_px: int = THUMB_MAX_PX
) -> Path:
    tmp = _atomic_target(dst)
    seek = min(1.0, (duration_s or 0) * 0.1)
    scale = f"scale=w={max_px}:h={max_px}:force_original_aspect_ratio=decrease"
    last_error = ""
    for position in (seek, 0.0) if seek > 0 else (0.0,):
        cmd = [str(ffmpeg), "-v", "error", "-nostdin", "-y", "-ss", f"{position:.3f}", "-i", str(src),
               "-frames:v", "1", "-vf", scale, "-q:v", "4", str(tmp)]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=VIDEO_TIMEOUT_S, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            os.replace(tmp, dst)
            return dst
        last_error = result.stderr.strip()[:300]
    try:
        tmp.unlink()
    except OSError:
        pass
    raise OSError(f"ffmpeg could not create a thumbnail for {src.name}: {last_error}")


def ensure_thumbnail(
    abs_path: Path,
    kind: str,
    size: int,
    mtime: float,
    ffmpeg: Path | None = None,
    duration_s: float | None = None,
) -> Path | None:
    """Return a cached thumbnail path, creating it if needed. ``None`` if not possible."""
    dst = thumb_path(abs_path, size, mtime)
    if dst.exists():
        return dst
    try:
        if kind == "photo":
            return make_photo_thumbnail(abs_path, dst)
        if kind == "video" and ffmpeg is not None:
            return make_video_thumbnail(ffmpeg, abs_path, dst, duration_s)
    except Exception as exc:  # noqa: BLE001 – unreadable files must never crash
        log.warning("thumbnail failed for %s: %s", abs_path, exc)
    return None
