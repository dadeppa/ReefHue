"""Timecodes, frame numbers, video segments and snapshot names (SPEC §6). No Qt."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath


def format_timecode(t: float, fps: float) -> str:
    """``hh:mm:ss:ff`` (non-drop-frame)."""
    fps_int = max(1, int(round(fps)))
    total_frames = int(math.floor(max(0.0, t) * fps + 1e-6))
    frames = total_frames % fps_int
    seconds = total_frames // fps_int
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{frames:02d}"


def frame_number(t: float, fps: float) -> int:
    return int(math.floor(max(0.0, t) * fps + 1e-6))


def snap_to_frame(t: float, fps: float) -> float:
    return frame_number(t, fps) / fps if fps > 0 else t


def clamp_segment(start: float, end: float, duration: float, fps: float, moved: str = "end") -> tuple[float, float]:
    """Keep ``0 <= start < end - 1 frame <= duration``; the moved handle yields (SPEC §7)."""
    frame = 1.0 / fps if fps > 0 else 0.04
    start = min(max(0.0, start), duration)
    end = min(max(0.0, end), duration)
    if end - start <= frame:
        if moved == "start":
            start = max(0.0, end - frame * 1.5)
        else:
            end = min(duration, start + frame * 1.5)
        if end - start <= frame:  # squeezed at a border: move the other handle
            if moved == "start":
                end = min(duration, start + frame * 1.5)
            else:
                start = max(0.0, end - frame * 1.5)
    return round(start, 4), round(end, 4)


def segment_or_none(start: float, end: float, duration: float, fps: float) -> dict | None:
    """``None`` when the segment covers the whole clip (keeps the file "Unedited")."""
    frame = 1.0 / fps if fps > 0 else 0.04
    if start <= frame / 2 and end >= duration - frame / 2:
        return None
    return {"start_s": round(start, 4), "end_s": round(end, 4)}


def snapshot_name(video_name: str, position: float) -> str:
    """``GX010001.MP4`` at 83.4 s → ``GX010001_00-01-23.jpg``."""
    seconds = int(math.floor(max(0.0, position)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{PurePosixPath(video_name).stem}_{h:02d}-{m:02d}-{s:02d}.jpg"


def unique_path(folder: Path, name: str) -> Path:
    """``name`` in ``folder``; ``_2``, ``_3`` … appended if it already exists (never overwrite)."""
    candidate = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 2
    while candidate.exists() or candidate.with_name(candidate.name + ".part").exists():
        candidate = folder / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def capture_at(video_capture: datetime | None, position: float) -> datetime | None:
    return None if video_capture is None else video_capture + timedelta(seconds=round(position, 3))
