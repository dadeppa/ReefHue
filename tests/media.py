"""Synthetic test media (SPEC §18)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import piexif
from PIL import Image

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def gradient(width: int = 64, height: int = 48, cast=(0.3, 0.8, 1.0)) -> Image.Image:
    """Horizontal gradient with a blue/green underwater-like cast."""
    x = np.linspace(0, 1, width, dtype=np.float32)[None, :, None]
    y = np.linspace(0.3, 1, height, dtype=np.float32)[:, None, None]
    rgb = (x * y * np.array(cast, dtype=np.float32)) * 255
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")


def make_jpeg(
    path: Path,
    dt: str | None = "2024:05:01 10:11:12",
    make: str = "GoPro",
    model: str = "HERO12 Black",
    orientation: int = 1,
    size=(64, 48),
) -> Path:
    zeroth = {piexif.ImageIFD.Make: make.encode(), piexif.ImageIFD.Model: model.encode(),
              piexif.ImageIFD.Orientation: orientation}
    exif_ifd = {piexif.ExifIFD.DateTimeOriginal: dt.encode()} if dt else {}
    exif = piexif.dump({"0th": zeroth, "Exif": exif_ifd})
    path.parent.mkdir(parents=True, exist_ok=True)
    gradient(*size).save(path, "JPEG", exif=exif, quality=90)
    return path


def make_png(path: Path, size=(40, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    gradient(*size).save(path, "PNG")
    return path


def make_heic(path: Path, size=(64, 48)) -> Path:
    import pillow_heif

    pillow_heif.register_heif_opener()
    path.parent.mkdir(parents=True, exist_ok=True)
    gradient(*size).save(path, "HEIF", quality=80)
    return path


def make_video(path: Path, seconds: float = 1.0, creation_time: str | None = "2026-05-01T10:00:00.000000Z",
               size: str = "160x90", audio: bool = True) -> Path:
    if FFMPEG is None:
        raise RuntimeError("ffmpeg not available")
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if creation_time:
        cmd += ["-metadata", f"creation_time={creation_time}"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def set_mtime(path: Path, ts: float) -> None:
    os.utime(path, (ts, ts))
