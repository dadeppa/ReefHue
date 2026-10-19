"""Photo decoding to float RGB (SPEC §8 step 1). No Qt. Blocking – call from workers.

EXIF orientation is applied, so the pipeline always receives an upright image.
8-bit sources are normalised by 255, 16-bit PNG/TIFF by 65535. Colour profiles
are assumed to be sRGB (see docs/DECISIONS.md).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from reefhue.io.exif import ORIENTATION_TO_ROTATION, TAG_ORIENTATION, ensure_heif

_SIXTEEN_BIT_MODES = {"I;16", "I;16B", "I;16L", "I;16N", "I"}


def _resize(img: np.ndarray, max_side: int | None) -> np.ndarray:
    if not max_side:
        return img
    h, w = img.shape[:2]
    if max(h, w) <= max_side:
        return img
    f = max_side / max(h, w)
    return cv2.resize(img, (max(1, round(w * f)), max(1, round(h * f))), interpolation=cv2.INTER_AREA)


def _orient(img: np.ndarray, orientation: int) -> np.ndarray:
    ops = {2: lambda a: a[:, ::-1], 3: lambda a: np.rot90(a, 2), 4: lambda a: a[::-1],
           5: lambda a: np.rot90(a[:, ::-1], 1), 6: lambda a: np.rot90(a, -1),
           7: lambda a: np.rot90(a[:, ::-1], -1), 8: lambda a: np.rot90(a, 1)}
    return np.ascontiguousarray(ops[orientation](img)) if orientation in ops else img


def is_deep(im: Image.Image) -> bool:
    """True for 16-bit sources. Pillow opens 16-bit RGB PNG/TIFF as 8-bit "RGB", so the
    raw mode of the image tiles ("RGB;16B", "RGB;16L", …) is checked as well."""
    if im.mode in _SIXTEEN_BIT_MODES:
        return True
    for tile in getattr(im, "tile", None) or []:
        args = tile[3] if len(tile) > 3 else None
        raw = args if isinstance(args, str) else (args[0] if isinstance(args, tuple) and args else "")
        if isinstance(raw, str) and ";16" in raw:
            return True
    return False


def oriented_size(path: Path) -> tuple[int, int]:
    if path.suffix.lower() in (".heic", ".heif"):
        ensure_heif()
    with Image.open(path) as im:
        w, h = im.size
        orientation = int(im.getexif().get(TAG_ORIENTATION, 1) or 1)
    return (h, w) if ORIENTATION_TO_ROTATION.get(orientation) in (90, 270) else (w, h)


def load_photo(path: Path, max_side: int | None = None) -> tuple[np.ndarray, tuple[int, int]]:
    """Return ``(float32 RGB in [0, 1], full oriented size (w, h))``.

    With ``max_side`` the image is decoded/downscaled for preview (JPEG uses the
    fast DCT-domain reduction first).
    """
    if path.suffix.lower() in (".heic", ".heif"):
        ensure_heif()
    with Image.open(path) as im:
        deep = is_deep(im)  # before getexif(): reading EXIF can clear the tile information
        full_w, full_h = im.size
        orientation = int(im.getexif().get(TAG_ORIENTATION, 1) or 1)
        if orientation in (5, 6, 7, 8):
            full_size = (full_h, full_w)
        else:
            full_size = (full_w, full_h)
        if deep:
            data = cv2.imread(str(path), cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
            if data is not None and data.dtype == np.uint16:
                if data.ndim == 2:
                    data = np.repeat(data[..., None], 3, axis=2)
                rgb = cv2.cvtColor(data[..., :3], cv2.COLOR_BGR2RGB).astype(np.float32) / 65535.0
                return _resize(_orient(rgb, orientation), max_side), full_size
        if max_side and im.format == "JPEG":
            im.draft("RGB", (max_side, max_side))
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P", "PA"):
            im = im.convert("RGBA")
            rgba = np.asarray(im, dtype=np.float32) / 255.0
            alpha = rgba[..., 3:4]
            rgb = rgba[..., :3] * alpha + 0.5 * (1 - alpha)  # transparent areas → mid grey
        else:
            rgb = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
    return _resize(np.ascontiguousarray(rgb, dtype=np.float32), max_side), full_size
