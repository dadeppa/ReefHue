"""Video metadata via ``ffprobe`` JSON (SPEC §10). No Qt. Blocking – call from workers."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from reefhue.core.timeutil import format_wallclock, parse_wallclock
from reefhue.io.exif import camera_name

log = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 30
_BITS = re.compile(r"(\d{2})(le|be)$")


def run_ffprobe(ffprobe: Path, path: Path) -> dict:
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT_S,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise OSError(f"ffprobe failed for {path.name}: {result.stderr.strip()[:300]}")
    return json.loads(result.stdout or "{}")


def _fps(stream: dict) -> float | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        text = stream.get(key)
        if text and text not in ("0/0", "0"):
            try:
                value = float(Fraction(text))
            except (ValueError, ZeroDivisionError):
                continue
            if value > 0:
                return round(value, 3)
    return None


def _bit_depth(stream: dict) -> int | None:
    raw = stream.get("bits_per_raw_sample")
    if raw and str(raw).isdigit():
        return int(raw)
    pix_fmt = stream.get("pix_fmt") or ""
    match = _BITS.search(pix_fmt)
    if match:
        return int(match.group(1))
    return 8 if pix_fmt else None


def _rotation(stream: dict) -> int:
    """Display rotation in degrees clockwise (0/90/180/270)."""
    clockwise = None
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                # Display-matrix rotation is counter-clockwise: -90 means "turn 90° clockwise".
                clockwise = -float(side["rotation"])
            except (TypeError, ValueError):
                pass
            break
    if clockwise is None:
        try:
            clockwise = float((stream.get("tags") or {}).get("rotate") or 0)  # legacy tag, clockwise
        except (TypeError, ValueError):
            clockwise = 0.0
    return int(round(clockwise / 90.0)) * 90 % 360


def _is_telemetry(stream: dict) -> bool:
    if stream.get("codec_type") != "data":
        return False
    tag = (stream.get("codec_tag_string") or "").lower()
    handler = ((stream.get("tags") or {}).get("handler_name") or "").lower()
    return tag == "gpmd" or "gopro met" in handler


# GoPro writes no make/model into the container, but the firmware tag identifies the camera.
GOPRO_FIRMWARE_MODELS = {
    "HD5": "HERO5 Black", "HD6": "HERO6 Black", "HD7": "HERO7 Black", "HD8": "HERO8 Black",
    "H19": "HERO8 Black", "H20": "HERO9 Black", "H21": "HERO10 Black", "H22": "HERO11 Black",
    "H23": "HERO12 Black", "H24": "HERO13 Black",
}


def _camera(fmt_tags: dict, video_tags: dict) -> str | None:
    make = _first(fmt_tags.get("com.apple.quicktime.make") or fmt_tags.get("make"))
    model = _first(fmt_tags.get("com.apple.quicktime.model") or fmt_tags.get("model"))
    name = camera_name(make, model)
    if name:
        return name
    handler = str(video_tags.get("handler_name") or "")
    firmware = str(_first(fmt_tags.get("firmware")) or "")
    if "gopro" in handler.lower() or firmware[:3] in GOPRO_FIRMWARE_MODELS:
        model = GOPRO_FIRMWARE_MODELS.get(firmware[:3])
        return f"GoPro {model}" if model else "GoPro"
    return None


def _first(value):
    """ffprobe joins repeated tags with ";" (e.g. "2026-02-11T10:25:20Z;2026-02-11T10:25:20Z")."""
    if isinstance(value, str) and ";" in value:
        return next((part for part in value.split(";") if part.strip()), None)
    return value


def creation_time(probe: dict) -> str | None:
    """Wall-clock capture time. Prefers Apple's local-time tag; never converts time zones."""
    fmt_tags = (probe.get("format") or {}).get("tags") or {}
    candidates = [_first(fmt_tags.get("com.apple.quicktime.creationdate")), _first(fmt_tags.get("creation_time"))]
    for stream in probe.get("streams") or []:
        candidates.append(_first((stream.get("tags") or {}).get("creation_time")))
    for value in candidates:
        dt = parse_wallclock(value)
        if dt is not None and dt.year > 1970:
            return format_wallclock(dt.replace(microsecond=0))
    for value in candidates:  # accept 1970 etc. as a last resort (camera clock reset)
        dt = parse_wallclock(value)
        if dt is not None:
            return format_wallclock(dt.replace(microsecond=0))
    return None


def parse_probe(probe: dict) -> tuple[dict[str, Any], str | None]:
    streams = probe.get("streams") or []
    fmt = probe.get("format") or {}
    fmt_tags = fmt.get("tags") or {}
    video = next(
        (s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")),
        {},
    )
    try:
        duration = float(fmt.get("duration") or video.get("duration") or 0) or None
    except (TypeError, ValueError):
        duration = None
    media = {
        "type": "video",
        "codec": video.get("codec_name"),
        "pix_fmt": video.get("pix_fmt"),
        "bit_depth": _bit_depth(video) if video else None,
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": _fps(video) if video else None,
        "duration_s": round(duration, 3) if duration else None,
        "color_transfer": video.get("color_transfer"),
        "color_primaries": video.get("color_primaries"),
        "rotation_meta": _rotation(video) if video else 0,
        "camera": _camera(fmt_tags, video.get("tags") or {}),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "has_telemetry": any(_is_telemetry(s) for s in streams),
    }
    return media, creation_time(probe)
