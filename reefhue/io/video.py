"""Video frame access via PyAV (SPEC §6, §8). No Qt. Blocking – use from worker threads.

Frames are converted by swscale (BT.709 for HD sources) to RGB at the requested
size; the display rotation from the metadata is applied so the pipeline always
receives an upright image, like photos. PyAV is imported lazily: it bundles its
own FFmpeg libraries, which only need to load when a video is opened.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Iterator

import numpy as np

log = logging.getLogger(__name__)


def _av():
    import av  # noqa: PLC0415 – lazy on purpose

    return av


def scaled_size(width: int, height: int, max_side: int | None) -> tuple[int, int]:
    """Even-sized target (swscale prefers even sizes) not larger than the source."""
    if not max_side or max(width, height) <= max_side:
        return width, height
    f = max_side / max(width, height)
    return max(2, int(round(width * f / 2)) * 2), max(2, int(round(height * f / 2)) * 2)


def rotate_array(img: np.ndarray, rotation_cw: int) -> np.ndarray:
    k = (int(rotation_cw) // 90) % 4
    return np.ascontiguousarray(np.rot90(img, -k)) if k else img


class VideoReader:
    """Sequential decoding plus accurate single-frame access for one file."""

    def __init__(self, path: Path, rotation_cw: int = 0) -> None:
        av = _av()
        self.path = path
        self.rotation = int(rotation_cw) % 360
        self.container = av.open(str(path))
        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"
        cc = self.stream.codec_context
        self.width, self.height = int(cc.width), int(cc.height)
        self.time_base = float(self.stream.time_base or 1 / 90000)
        rate = self.stream.average_rate or self.stream.guessed_rate or 30
        self.fps = float(rate) if float(rate) > 0 else 30.0
        start = self.stream.start_time
        self.start_s = float(start * self.stream.time_base) if start is not None else 0.0
        if self.stream.duration:
            self.duration = float(self.stream.duration * self.stream.time_base)
        elif self.container.duration:
            self.duration = self.container.duration / 1_000_000
        else:
            self.duration = 0.0
        self.colorspace = "ITU709" if self.height >= 720 else None
        self._iter: Iterator | None = None

    # ---- sizes ---------------------------------------------------------------------
    @property
    def full_size(self) -> tuple[int, int]:
        """Upright size (after display rotation)."""
        return (self.height, self.width) if self.rotation in (90, 270) else (self.width, self.height)

    @property
    def frame_duration(self) -> float:
        return 1.0 / self.fps

    def frame_number(self, t: float) -> int:
        return int(math.floor(t * self.fps + 1e-6))

    # ---- conversion ------------------------------------------------------------------
    def to_array(self, frame, max_side: int | None = None, deep: bool = False) -> np.ndarray:
        """uint8 (or uint16 with ``deep``) RGB array, upright."""
        side = max_side
        if side and self.rotation in (90, 270):
            side = max_side  # rotation does not change the long side
        w, h = scaled_size(self.width, self.height, side)
        kwargs = {"width": w, "height": h, "format": "rgb48le" if deep else "rgb24"}
        if self.colorspace:
            kwargs["src_colorspace"] = self.colorspace
        try:
            arr = frame.reformat(**kwargs).to_ndarray()
        except (ValueError, TypeError):  # older PyAV: colorspace name not accepted
            kwargs.pop("src_colorspace", None)
            arr = frame.reformat(**kwargs).to_ndarray()
        return rotate_array(arr, self.rotation)

    def frame_time(self, frame) -> float:
        if frame.pts is None:
            return 0.0
        return float(frame.pts * self.time_base) - self.start_s

    # ---- access ----------------------------------------------------------------------
    def seek(self, t: float) -> None:
        """Seek to the keyframe at or before ``t``; :meth:`frames` continues from there."""
        target = int((max(0.0, t) + self.start_s) / self.time_base)
        self.container.seek(target, stream=self.stream, backward=True, any_frame=False)
        self._iter = None

    def frames(self) -> Iterator:
        """Decoded ``av.VideoFrame`` objects from the current position."""
        if self._iter is None:
            self._iter = self.container.decode(self.stream)
        return self._iter

    def next_frame(self):
        try:
            return next(self.frames())
        except StopIteration:
            return None
        except Exception as exc:  # corrupt packet: keep going instead of crashing
            log.warning("decode error in %s: %s", self.path.name, exc)
            return None

    def frame_at(self, t: float):
        """The frame displayed at time ``t`` (accurate seek: keyframe, then decode forward)."""
        t = max(0.0, min(t, max(0.0, self.duration - self.frame_duration / 2)))
        self.seek(t)
        best = None
        half = self.frame_duration / 2
        while True:
            frame = self.next_frame()
            if frame is None:
                break
            best = frame
            if self.frame_time(frame) >= t - half:
                break
        return best

    def read_at(self, t: float, max_side: int | None = None, deep: bool = False) -> tuple[float, np.ndarray] | None:
        """``(frame_time, float32 RGB in [0, 1])`` of the frame at ``t``."""
        frame = self.frame_at(t)
        if frame is None:
            return None
        arr = self.to_array(frame, max_side, deep)
        scale = 65535.0 if deep else 255.0
        return self.frame_time(frame), arr.astype(np.float32) / np.float32(scale)

    def close(self) -> None:
        try:
            self.container.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "VideoReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def read_frame(path: Path, t: float, rotation_cw: int = 0, max_side: int | None = None,
               deep: bool = False) -> tuple[np.ndarray, tuple[int, int], float]:
    """One-shot: ``(float RGB, full upright size, frame time)`` at ``t``."""
    with VideoReader(path, rotation_cw) as reader:
        result = reader.read_at(t, max_side, deep)
        if result is None:
            raise OSError(f"no frame at {t:.3f}s in {path.name}")
        frame_t, img = result
        return img, reader.full_size, frame_t
