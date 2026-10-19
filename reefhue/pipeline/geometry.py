"""Geometry & lens stage (SPEC §8 step 2) and the crop math used by the UI. No Qt.

Order: distortion → vignette correction → straighten (auto-crop to the largest
valid rectangle, no empty corners) → rotate 90° → flip → crop. Crop
coordinates are normalised (0–1) relative to the image *after*
distortion/straighten/rotate/flip.
"""

from __future__ import annotations

import math
from functools import lru_cache

import cv2
import numpy as np

MIN_CROP_PX = 32
DISTORTION_STRENGTH = 0.3  # distortion ±100 → radial coefficient ±0.3 (radius normalised to half-diagonal)
VIGNETTE_STRENGTH = 1.0    # vignette_corr 100 → corners brightened by 1 stop

ASPECTS: dict[str, float | None] = {
    "free": None,
    "original": None,  # resolved from the uncropped image
    "16:9": 16 / 9,
    "4:3": 4 / 3,
    "3:2": 3 / 2,
    "1:1": 1.0,
    "9:16": 9 / 16,
}


# --------------------------------------------------------------------------- sizes
def straighten_scale(w: float, h: float, degrees: float) -> float:
    """Scale of the largest same-aspect rectangle inside a w×h image rotated by ``degrees``."""
    if not degrees:
        return 1.0
    a = math.radians(abs(degrees))
    c, s = math.cos(a), math.sin(a)
    return min(w / (w * c + h * s), h / (w * s + h * c))


def straightened_size(w: int, h: int, degrees: float) -> tuple[int, int]:
    k = straighten_scale(w, h, degrees)
    return max(1, int(math.floor(w * k + 1e-6))), max(1, int(math.floor(h * k + 1e-6)))


def crop_pixels(crop: dict, w: int, h: int, min_px: float = MIN_CROP_PX) -> tuple[int, int, int, int]:
    """Normalised crop → integer pixel rect ``(x, y, cw, ch)`` inside ``w×h``."""
    x0 = int(round(min(max(float(crop.get("x", 0)), 0.0), 1.0) * w))
    y0 = int(round(min(max(float(crop.get("y", 0)), 0.0), 1.0) * h))
    x1 = int(round(min(max(float(crop.get("x", 0)) + float(crop.get("w", 1)), 0.0), 1.0) * w))
    y1 = int(round(min(max(float(crop.get("y", 0)) + float(crop.get("h", 1)), 0.0), 1.0) * h))
    min_w, min_h = min(w, max(1, int(round(min_px)))), min(h, max(1, int(round(min_px))))
    if x1 - x0 < min_w:
        x1 = min(w, x0 + min_w)
        x0 = x1 - min_w
    if y1 - y0 < min_h:
        y1 = min(h, y0 + min_h)
        y0 = y1 - min_h
    return x0, y0, x1 - x0, y1 - y0


def uncropped_size(src_w: int, src_h: int, geometry: dict) -> tuple[int, int]:
    """Size after distortion/straighten/rotate/flip (the crop's reference frame)."""
    w, h = straightened_size(src_w, src_h, float(geometry.get("straighten_deg", 0) or 0))
    if int(geometry.get("rotate_90", 0)) % 2:
        w, h = h, w
    return w, h


def output_size(src_w: int, src_h: int, geometry: dict, apply_crop: bool = True) -> tuple[int, int]:
    w, h = uncropped_size(src_w, src_h, geometry)
    if not apply_crop:
        return w, h
    _x, _y, cw, ch = crop_pixels(geometry.get("crop") or {}, w, h)
    return cw, ch


def is_identity(geometry: dict) -> bool:
    crop = geometry.get("crop") or {}
    return (
        not geometry.get("distortion")
        and not geometry.get("vignette_corr")
        and not geometry.get("straighten_deg")
        and not int(geometry.get("rotate_90", 0)) % 4
        and not geometry.get("flip_h")
        and not geometry.get("flip_v")
        and (crop.get("x", 0), crop.get("y", 0), crop.get("w", 1), crop.get("h", 1)) == (0, 0, 1, 1)
    )


# --------------------------------------------------------------------------- crop editing (UI helpers)
def clamp_crop(rect: dict, w: int, h: int, min_px: float = MIN_CROP_PX) -> dict:
    """Keep a normalised crop inside the frame with at least ``min_px`` pixels per side."""
    min_w, min_h = min(1.0, min_px / max(w, 1)), min(1.0, min_px / max(h, 1))
    cw = min(1.0, max(min_w, float(rect.get("w", 1))))
    ch = min(1.0, max(min_h, float(rect.get("h", 1))))
    x = min(1.0 - cw, max(0.0, float(rect.get("x", 0))))
    y = min(1.0 - ch, max(0.0, float(rect.get("y", 0))))
    return {"x": round(x, 5), "y": round(y, 5), "w": round(cw, 5), "h": round(ch, 5),
            "aspect": rect.get("aspect", "free")}


def aspect_ratio(name: str, w: int, h: int) -> float | None:
    if name == "original":
        return w / h if h else None
    return ASPECTS.get(name)


def fit_aspect(rect: dict, ratio: float | None, w: int, h: int) -> dict:
    """Largest rectangle with pixel aspect ``ratio`` centred on the current crop, inside the frame."""
    rect = dict(rect)
    if ratio is None:
        return clamp_crop(rect, w, h)
    cx = (rect.get("x", 0) + rect.get("w", 1) / 2) * w
    cy = (rect.get("y", 0) + rect.get("h", 1) / 2) * h
    pw, ph = rect.get("w", 1) * w, rect.get("h", 1) * h
    # keep the current area's larger extent, then shrink to fit the frame
    if pw / ph > ratio:
        pw = ph * ratio
    else:
        ph = pw / ratio
    half_w = min(pw / 2, cx, w - cx)
    half_h = min(ph / 2, cy, h - cy)
    if half_w / ratio < half_h:
        half_h = half_w / ratio
    else:
        half_w = half_h * ratio
    rect.update(x=(cx - half_w) / w, y=(cy - half_h) / h, w=2 * half_w / w, h=2 * half_h / h)
    return clamp_crop(rect, w, h)


def drag_crop(rect: dict, handle: str, dx: float, dy: float, ratio: float | None, w: int, h: int) -> dict:
    """Move (``handle == "move"``) or resize a crop by a normalised delta.

    ``handle`` is one of move, n, s, e, w, ne, nw, se, sw. With an aspect ratio,
    resizing keeps the pixel ratio (the dominant drag direction wins).
    """
    x0, y0 = float(rect["x"]), float(rect["y"])
    x1, y1 = x0 + float(rect["w"]), y0 + float(rect["h"])
    if handle == "move":
        dx = min(1 - x1, max(-x0, dx))
        dy = min(1 - y1, max(-y0, dy))
        return clamp_crop({**rect, "x": x0 + dx, "y": y0 + dy}, w, h)
    min_w, min_h = MIN_CROP_PX / max(w, 1), MIN_CROP_PX / max(h, 1)
    if "w" in handle:
        x0 = min(x1 - min_w, max(0.0, x0 + dx))
    if "e" in handle:
        x1 = max(x0 + min_w, min(1.0, x1 + dx))
    if "n" in handle:
        y0 = min(y1 - min_h, max(0.0, y0 + dy))
    if "s" in handle:
        y1 = max(y0 + min_h, min(1.0, y1 + dy))
    if ratio is not None:
        pw, ph = (x1 - x0) * w, (y1 - y0) * h
        horizontal = handle in ("e", "w") or (len(handle) == 2 and abs(dx * w) >= abs(dy * h))
        if horizontal:
            ph = pw / ratio
        else:
            pw = ph * ratio
        # anchor at the opposite side / corner
        if "n" in handle:
            y0 = y1 - ph / h
        else:
            y1 = y0 + ph / h
        if "w" in handle:
            x0 = x1 - pw / w
        else:
            x1 = x0 + pw / w
        if handle in ("e", "w"):  # centre vertically
            cy = (y0 + y1) / 2
            y0, y1 = cy - ph / h / 2, cy + ph / h / 2
        if handle in ("n", "s"):
            cx = (x0 + x1) / 2
            x0, x1 = cx - pw / w / 2, cx + pw / w / 2
        if x0 < 0 or y0 < 0 or x1 > 1 or y1 > 1:  # would leave the frame: refuse
            return clamp_crop(rect, w, h)
    return clamp_crop({**rect, "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}, w, h)


def transform_crop_for_rotation(crop: dict, quarter_turns_cw: int) -> dict:
    """Rotate a normalised crop with the image so the same content stays selected."""
    c = dict(crop)
    for _ in range(quarter_turns_cw % 4):
        c = {**c, "x": 1 - (c["y"] + c["h"]), "y": c["x"], "w": c["h"], "h": c["w"]}
    return {**c, **{k: round(float(c[k]), 6) for k in ("x", "y", "w", "h")}}


def transform_crop_for_flip(crop: dict, horizontal: bool) -> dict:
    c = dict(crop)
    if horizontal:
        c["x"] = 1 - c["x"] - c["w"]
    else:
        c["y"] = 1 - c["y"] - c["h"]
    return {**c, **{k: round(float(c[k]), 6) for k in ("x", "y", "w", "h")}}


# --------------------------------------------------------------------------- image transforms
@lru_cache(maxsize=8)
def _distortion_maps(w: int, h: int, k: float) -> tuple[np.ndarray, np.ndarray]:
    cx, cy = (w - 1) / 2, (h - 1) / 2
    norm = math.hypot(cx, cy) or 1.0
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32) - cx, np.arange(h, dtype=np.float32) - cy)
    r2 = (xs * xs + ys * ys) / (norm * norm)
    # k > 0 corrects barrel distortion: output radius r samples the source at r(1 - k r²).
    factor = 1 - k * r2
    if k < 0:
        factor /= 1 - k  # pincushion correction would leave empty corners: zoom in
    return (xs * factor + cx).astype(np.float32), (ys * factor + cy).astype(np.float32)


def apply_distortion(img: np.ndarray, amount: float) -> np.ndarray:
    if not amount:
        return img
    h, w = img.shape[:2]
    map_x, map_y = _distortion_maps(w, h, round(amount / 100 * DISTORTION_STRENGTH, 5))
    return cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


@lru_cache(maxsize=8)
def _vignette_gain(w: int, h: int, amount: float) -> np.ndarray:
    cx, cy = (w - 1) / 2, (h - 1) / 2
    norm = math.hypot(cx, cy) or 1.0
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32) - cx, np.arange(h, dtype=np.float32) - cy)
    r2 = (xs * xs + ys * ys) / (norm * norm)
    return (np.exp2(amount / 100 * VIGNETTE_STRENGTH * r2)).astype(np.float32)[..., None]


def apply_vignette_correction(img: np.ndarray, amount: float) -> np.ndarray:
    if not amount:
        return img
    h, w = img.shape[:2]
    return img * _vignette_gain(w, h, float(amount))


def apply_straighten(img: np.ndarray, degrees: float) -> np.ndarray:
    if not degrees:
        return img
    h, w = img.shape[:2]
    out_w, out_h = straightened_size(w, h, degrees)
    m = cv2.getRotationMatrix2D(((w - 1) / 2, (h - 1) / 2), -float(degrees), 1.0)  # positive = clockwise
    m[0, 2] += (out_w - w) / 2
    m[1, 2] += (out_h - h) / 2
    return cv2.warpAffine(img, m, (out_w, out_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def apply_rotate_flip(img: np.ndarray, rotate_90: int, flip_h: bool, flip_v: bool) -> np.ndarray:
    k = int(rotate_90) % 4
    if k:
        img = np.rot90(img, -k)  # clockwise quarter turns
    if flip_h:
        img = img[:, ::-1]
    if flip_v:
        img = img[::-1]
    return np.ascontiguousarray(img)


def apply_crop(img: np.ndarray, crop: dict, min_px: float = MIN_CROP_PX) -> np.ndarray:
    h, w = img.shape[:2]
    x, y, cw, ch = crop_pixels(crop, w, h, min_px)
    if (x, y, cw, ch) == (0, 0, w, h):
        return img
    return img[y : y + ch, x : x + cw]


def apply_geometry(img: np.ndarray, geometry: dict, scale: float = 1.0, crop: bool = True) -> np.ndarray:
    img = apply_distortion(img, float(geometry.get("distortion", 0) or 0))
    img = apply_vignette_correction(img, float(geometry.get("vignette_corr", 0) or 0))
    img = apply_straighten(img, float(geometry.get("straighten_deg", 0) or 0))
    img = apply_rotate_flip(img, int(geometry.get("rotate_90", 0)), bool(geometry.get("flip_h")),
                            bool(geometry.get("flip_v")))
    if crop:
        img = apply_crop(img, geometry.get("crop") or {}, MIN_CROP_PX * scale)
    return img
