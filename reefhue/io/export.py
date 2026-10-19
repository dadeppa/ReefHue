"""Execute one export job (SPEC §14). No Qt. Blocking – run in workers.

* Safe writing: ``<name>.part`` first, renamed on success; anything replaced
  (an earlier export, or the original in overwrite mode) goes to the system
  trash via send2trash – nothing is ever deleted hard.
* Photos: full-resolution pipeline, EXIF (incl. GPS) kept, orientation set to
  upright, corrected capture date written, ICC profile kept.
* Videos: lossless stream copy when only the segment is cut, otherwise frames
  from the same pipeline are piped to FFmpeg (same codec family, 10-bit kept);
  audio, metadata and – best effort – GoPro telemetry come from the source.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import piexif
from PIL import Image
from send2trash import send2trash

from reefhue.core import export_plan as ep
from reefhue.io import probe as probe_io
from reefhue.io.decode import is_deep, load_photo
from reefhue.io.exif import ensure_heif
from reefhue.io.video import VideoReader
from reefhue.pipeline import geometry as geo
from reefhue.pipeline import pipeline as pl

log = logging.getLogger(__name__)

Progress = Callable[[float], None]
RENDER_WORKERS = max(2, min(4, (os.cpu_count() or 4) // 2))
SPACE_MARGIN = 50 * 1024 * 1024
FFMPEG_TIMEOUT_S = 3600


class ExportError(Exception):
    """``key`` is an i18n key; ``detail`` goes to the error log."""

    def __init__(self, key: str, detail: str = "") -> None:
        super().__init__(detail or key)
        self.key = key
        self.detail = detail


class ExportCancelled(Exception):
    pass


@dataclass
class ExportResult:
    target: Path
    encoder: dict | None = None
    original_trashed: bool = False
    notes: list[str] = field(default_factory=list)  # i18n keys of non-fatal notices


@dataclass
class Control:
    """Cancel / pause flags shared with the UI."""

    cancel: threading.Event = field(default_factory=threading.Event)
    resume: threading.Event = field(default_factory=lambda: _set_event())

    def checkpoint(self) -> None:
        self.resume.wait()
        if self.cancel.is_set():
            raise ExportCancelled()


def _set_event() -> threading.Event:
    event = threading.Event()
    event.set()
    return event


# =============================================================================== shared helpers
def part_path(target: Path) -> Path:
    return target.with_name(target.name + ".part")


def estimate_bytes(job: ep.ExportJob, src_size: int) -> int:
    if job.kind == "photo":
        return src_size * 3 + SPACE_MARGIN
    duration = float(job.media.get("duration_s") or 1)
    fraction = 1.0
    if job.segment:
        fraction = max(0.01, (job.segment["end_s"] - job.segment["start_s"]) / duration)
    factor = 1.0 if job.video_mode == ep.VIDEO_COPY else 1.5
    return int(src_size * fraction * factor) + SPACE_MARGIN


def check_free_space(directory: Path, needed: int) -> None:
    free = shutil.disk_usage(directory).free
    if free < needed:
        raise ExportError("export.error.disk_space", f"need {needed} bytes, {free} free in {directory}")


def finalize(part: Path, target: Path, src: Path, job: ep.ExportJob) -> bool:
    """Move the finished ``.part`` into place. Returns True if the original went to the trash."""
    trashed = False
    if job.overwrite:
        send2trash(str(src))  # the original goes to the system trash, never deleted hard
        trashed = True
        if target != src and target.exists():
            send2trash(str(target))
    elif target.exists():
        if not job.replace_existing:
            raise ExportError("export.error.exists", str(target))
        send2trash(str(target))  # "replace" moves the earlier file to the trash
    os.replace(part, target)
    return trashed


def run_job(job: ep.ExportJob, folder: Path, ffmpeg: Path | None, ffprobe: Path | None,
            progress: Progress, control: Control) -> ExportResult:
    src = folder / job.src_rel
    target = folder / job.target_rel
    if not src.is_file():
        raise ExportError("export.error.missing_source", str(src))
    target.parent.mkdir(parents=True, exist_ok=True)
    check_free_space(target.parent, estimate_bytes(job, src.stat().st_size))
    part = part_path(target)
    part.unlink(missing_ok=True)
    try:
        if job.kind == "photo":
            notes = export_photo(src, part, job, progress, control)
            encoder = None
        else:
            if ffmpeg is None or ffprobe is None:
                raise ExportError("export.error.no_ffmpeg")
            encoder, notes = export_video(src, part, job, ffmpeg, ffprobe, progress, control)
        control.checkpoint()
        trashed = finalize(part, target, src, job)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    progress(1.0)
    return ExportResult(target, encoder, trashed, notes)


# =============================================================================== photos
def _source_info(src: Path) -> tuple[bytes | None, bytes | None, bool]:
    """``(exif bytes, icc profile, is 16-bit)`` of the source photo."""
    if src.suffix.lower() in (".heic", ".heif"):
        ensure_heif()
    with Image.open(src) as im:
        deep = is_deep(im)  # before getexif(): reading EXIF can load the image and clear its tiles
        exif = im.info.get("exif")
        if exif is None:
            data = im.getexif()
            exif = data.tobytes() if data else None
        return exif, im.info.get("icc_profile"), deep


def build_exif(source_exif: bytes | None, size: tuple[int, int], capture_override=None) -> bytes | None:
    """Keep the camera's EXIF (incl. GPS); orientation upright, no stale thumbnail, new size/date."""
    try:
        data = piexif.load(source_exif) if source_exif else {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}
    except Exception as exc:  # noqa: BLE001 – unreadable EXIF: write a minimal block
        log.warning("cannot parse EXIF: %s", exc)
        data = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}
    data["0th"][piexif.ImageIFD.Orientation] = 1  # pixels are already upright
    data["thumbnail"] = None
    data["1st"] = {}
    data.setdefault("Exif", {})
    data["Exif"][piexif.ExifIFD.PixelXDimension] = int(size[0])
    data["Exif"][piexif.ExifIFD.PixelYDimension] = int(size[1])
    if capture_override is not None:
        stamp = capture_override.strftime("%Y:%m:%d %H:%M:%S").encode("ascii")
        data["Exif"][piexif.ExifIFD.DateTimeOriginal] = stamp
        data["Exif"][piexif.ExifIFD.DateTimeDigitized] = stamp
        data["0th"][piexif.ImageIFD.DateTime] = stamp
    for attempt in range(2):
        try:
            return piexif.dump(data)
        except Exception as exc:  # noqa: BLE001 – usually a malformed maker note
            log.warning("EXIF dump failed (attempt %d): %s", attempt + 1, exc)
            data["Exif"].pop(piexif.ExifIFD.MakerNote, None)
            data.pop("Interop", None)
    return None


def export_photo(src: Path, part: Path, job: ep.ExportJob, progress: Progress, control: Control) -> list[str]:
    notes: list[str] = []
    exif_src, icc, sixteen = _source_info(src)
    img, full = load_photo(src)
    progress(0.25)
    control.checkpoint()
    ctx = pl.make_context(full, full, job.edit)
    out = pl.process(img, job.edit, ctx)
    progress(0.75)
    control.checkpoint()
    fmt = ep.PHOTO_FORMATS.get(Path(job.target_rel).suffix.lower().lstrip("."), "JPEG")
    height, width = out.shape[:2]
    if sixteen and fmt in ("PNG", "TIFF"):
        # 16-bit output via OpenCV (Pillow cannot write 16-bit RGB); no EXIF in this path.
        ok, buf = cv2.imencode(".png" if fmt == "PNG" else ".tiff", cv2.cvtColor(pl.to_uint16(out), cv2.COLOR_RGB2BGR))
        if not ok:
            raise ExportError("export.error.write", "cv2.imencode failed")
        part.write_bytes(buf.tobytes())
        notes.append("export.note.no_metadata_16bit")
        return notes
    image = Image.fromarray(pl.to_uint8(out, dither=True), "RGB")
    exif = build_exif(exif_src, (width, height), job.capture_override)
    if exif_src and exif is None:
        notes.append("export.note.exif_dropped")
    kwargs: dict = {}
    if icc:
        kwargs["icc_profile"] = icc
    if exif:
        kwargs["exif"] = exif
    if fmt == "JPEG":
        kwargs.update(quality=job.opts.jpeg_quality, subsampling=0 if job.opts.jpeg_quality >= 90 else 2,
                      optimize=True)
    elif fmt in ("WEBP", "HEIF"):
        kwargs.update(quality=job.opts.jpeg_quality)
    elif fmt == "TIFF":
        kwargs.update(compression="tiff_lzw")
    if fmt == "HEIF":
        ensure_heif()
    with open(part, "wb") as fh:
        image.save(fh, format=fmt, **kwargs)
    return notes


# =============================================================================== videos
def telemetry_streams(probe: dict) -> list[int]:
    """Indices of GoPro GPMF metadata streams."""
    result = []
    for stream in probe.get("streams") or []:
        tag = (stream.get("codec_tag_string") or "").lower()
        handler = ((stream.get("tags") or {}).get("handler_name") or "").lower()
        if stream.get("codec_type") == "data" and (tag == "gpmd" or "gopro met" in handler):
            result.append(int(stream["index"]))
    return result


def _iso(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def metadata_args(job: ep.ExportJob, probe: dict) -> list[str]:
    """Corrected capture date (SPEC §14): creation_time, plus Apple's local-time tag if present."""
    if job.capture_override is None:
        return []
    args = ["-metadata", f"creation_time={_iso(job.capture_override)}"]
    apple = ((probe.get("format") or {}).get("tags") or {}).get("com.apple.quicktime.creationdate")
    if apple:
        offset = apple[19:] if len(apple) > 19 else ""
        args += ["-metadata", f"com.apple.quicktime.creationdate={job.capture_override:%Y-%m-%dT%H:%M:%S}{offset}"]
    return args


def segment_bounds(job: ep.ExportJob) -> tuple[float, float]:
    duration = float(job.media.get("duration_s") or 0)
    if job.segment:
        return float(job.segment["start_s"]), float(job.segment["end_s"])
    return 0.0, duration


def _container(job: ep.ExportJob) -> str:
    return ep.VIDEO_CONTAINERS.get(Path(job.target_rel).suffix.lower().lstrip("."), "mp4")


# Container housekeeping keys: with use_metadata_tags they would be written a second time
# (as "mdta" keys) and readers then see joined values like "isom;mp41".
_DUPLICATED_KEYS = ("major_brand", "minor_version", "compatible_brands", "encoder")


def _movflags(container: str) -> list[str]:
    if container not in ("mp4", "mov"):
        return []
    args = ["-movflags", "+use_metadata_tags+faststart"]  # keeps custom keys (GoPro firmware, Apple model/GPS)
    for key in _DUPLICATED_KEYS:
        args += ["-metadata", f"{key}="]
    return args


def _run_ffmpeg(cmd: list[str], control: Control | None = None) -> None:
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        while True:
            try:
                proc.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if control is not None and control.cancel.is_set():
                    proc.kill()
                    proc.wait()
                    raise ExportCancelled() from None
        if proc.returncode != 0:
            err.seek(0)
            raise ExportError("export.error.ffmpeg", err.read().decode("utf-8", "replace")[-2000:])


def copy_command(ffmpeg: Path, src: Path, part: Path, job: ep.ExportJob, probe: dict, keep_audio: bool,
                 telemetry: list[int]) -> list[str]:
    start, end = segment_bounds(job)
    cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if job.segment:
        cmd += ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}"]
    cmd += ["-i", str(src), "-map", "0:v:0"]
    if keep_audio:
        cmd += ["-map", "0:a?"]
    for index in telemetry:
        cmd += ["-map", f"0:{index}"]
    cmd += ["-c", "copy", "-map_metadata", "0", *_movflags(_container(job)), *metadata_args(job, probe),
            "-f", _container(job), str(part)]
    return cmd


def encode_command(ffmpeg: Path, src: Path, out: Path, job: ep.ExportJob, probe: dict, size: tuple[int, int],
                   fps: str, deep: bool, keep_audio: bool, encoder: str) -> list[str]:
    start, end = segment_bounds(job)
    width, height = size
    ten_bit = deep and encoder != "h264_videotoolbox"
    hd = height >= 720 or width >= 1280
    matrix = "bt709" if hd else "smpte170m"
    scale_matrix = "bt709" if hd else "bt601"
    cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb48le" if deep else "rgb24", "-s", f"{width}x{height}",
           "-framerate", fps, "-i", "pipe:0",
           "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src),
           "-map", "0:v:0"]
    if keep_audio:
        cmd += ["-map", "1:a?", "-c:a", "copy"]
    cmd += ["-vf", f"scale=out_color_matrix={scale_matrix}:out_range=tv,format={ep.output_pix_fmt(encoder, ten_bit)}"]
    cmd += ep.encoder_args(encoder, job.opts.video_quality, ten_bit)
    cmd += ["-colorspace", matrix, "-color_primaries", matrix if hd else "smpte170m",
            "-color_trc", matrix if hd else "smpte170m", "-color_range", "tv",
            "-map_metadata", "1", *_movflags(_container(job)), *metadata_args(job, probe),
            "-shortest", "-f", _container(job), str(out)]
    return cmd


def mux_telemetry_command(ffmpeg: Path, video: Path, src: Path, part: Path, job: ep.ExportJob,
                          telemetry: list[int]) -> list[str]:
    start, end = segment_bounds(job)
    cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(video),
           "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src), "-map", "0"]
    for index in telemetry:
        cmd += ["-map", f"1:{index}"]
    cmd += ["-c", "copy", "-map_metadata", "0", *_movflags(_container(job)), "-f", _container(job), str(part)]
    return cmd


def _render_frames(src: Path, job: ep.ExportJob, proc: subprocess.Popen, size: tuple[int, int], deep: bool,
                   progress: Progress, control: Control) -> int:
    """Decode the segment, run every frame through the pipeline (in parallel), write in order."""
    start, end = segment_bounds(job)
    rotation = int(job.media.get("rotation_meta") or 0)
    width, height = size
    written = 0
    stats: dict = {}
    with VideoReader(src, rotation) as reader, ThreadPoolExecutor(RENDER_WORKERS) as pool:
        full = reader.full_size
        total = max(1, round((end - start) * reader.fps))
        half = reader.frame_duration / 2

        def render(arr: np.ndarray) -> bytes:
            img = arr.astype(np.float32) / np.float32(65535.0) if arr.dtype == np.uint16 else arr
            ctx = pl.make_context(full, full, job.edit, stats=stats)
            out = pl.process(img, job.edit, ctx)
            for key, value in ctx.stats.items():
                stats.setdefault(key, value)
            data = pl.to_uint16(out) if deep else pl.to_uint8(out)
            return np.ascontiguousarray(data[:height, :width]).tobytes()

        frame = reader.frame_at(start)
        inflight: list = []
        while frame is not None or inflight:
            while frame is not None and len(inflight) < RENDER_WORKERS:
                if reader.frame_time(frame) >= end - half:
                    frame = None
                    break
                inflight.append(pool.submit(render, reader.to_array(frame, None, deep)))
                frame = reader.next_frame()
            if not inflight:
                break
            data = inflight.pop(0).result()
            control.checkpoint()
            try:
                proc.stdin.write(data)
            except BrokenPipeError:
                raise ExportError("export.error.ffmpeg", "encoder closed the pipe") from None
            written += 1
            progress(min(0.95, 0.95 * written / total))
    return written


def export_video(src: Path, part: Path, job: ep.ExportJob, ffmpeg: Path, ffprobe: Path,
                 progress: Progress, control: Control) -> tuple[dict, list[str]]:
    notes: list[str] = []
    probe = probe_io.run_ffprobe(ffprobe, src)
    keep_audio = bool(job.export_options.get("keep_audio", True)) and any(
        s.get("codec_type") == "audio" for s in probe.get("streams") or [])
    telemetry = telemetry_streams(probe) if (job.opts.keep_telemetry and job.export_options.get("keep_telemetry", True)) else []
    start, end = segment_bounds(job)

    if job.video_mode == ep.VIDEO_COPY:
        try:
            _run_ffmpeg(copy_command(ffmpeg, src, part, job, probe, keep_audio, telemetry), control)
        except ExportError:
            if not telemetry:
                raise
            log.warning("telemetry copy failed for %s – retrying without", src.name)
            notes.append("export.note.telemetry_dropped")
            _run_ffmpeg(copy_command(ffmpeg, src, part, job, probe, keep_audio, []), control)
        verify_video(ffprobe, part, end - start, tolerance=3.0)  # stream copy starts at a keyframe
        return {"codec": "copy", "quality": None, "hw": False}, notes

    video = probe_io.parse_probe(probe)[0]
    deep = int(video.get("bit_depth") or 8) > 8
    full_w, full_h = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = int(job.media.get("rotation_meta") or video.get("rotation_meta") or 0)
    if rotation in (90, 270):
        full_w, full_h = full_h, full_w
    out_w, out_h = geo.output_size(full_w, full_h, job.edit["geometry"])
    size = (out_w - out_w % 2, out_h - out_h % 2)  # yuv420 needs even dimensions
    stream = next(s for s in probe["streams"] if s.get("codec_type") == "video")
    fps = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "30/1"
    if fps in ("0/0", "0"):
        fps = stream.get("r_frame_rate") or "30/1"
    encoder = ep.encoder_for(video.get("codec"), job.opts.hw_encoder)
    encoded = part if not telemetry else part.with_name(part.name + ".video")
    cmd = encode_command(ffmpeg, src, encoded, job, probe, size, fps, deep, keep_audio, encoder)
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            frames = _render_frames(src, job, proc, size, deep, progress, control)
            proc.stdin.close()
            proc.wait(timeout=FFMPEG_TIMEOUT_S)
        except BaseException:
            proc.kill()
            proc.wait()
            encoded.unlink(missing_ok=True)
            raise
        if proc.returncode != 0:
            err.seek(0)
            encoded.unlink(missing_ok=True)
            raise ExportError("export.error.ffmpeg", err.read().decode("utf-8", "replace")[-2000:])
    if telemetry:
        try:
            _run_ffmpeg(mux_telemetry_command(ffmpeg, encoded, src, part, job, telemetry), control)
            encoded.unlink(missing_ok=True)
        except ExportError:
            log.warning("telemetry mux failed for %s – exporting without", src.name)
            notes.append("export.note.telemetry_dropped")
            os.replace(encoded, part)
    rate = _fps_value(fps)
    verify_video(ffprobe, part, frames / rate if rate else end - start, tolerance=max(0.5, 3.0 / (rate or 30)))
    return {"codec": encoder.replace("lib", "").replace("_videotoolbox", ""), "quality": job.opts.video_quality,
            "hw": job.opts.hw_encoder}, notes


def _fps_value(fps: str) -> float:
    try:
        num, _, den = fps.partition("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def verify_video(ffprobe: Path, path: Path, expected: float, tolerance: float) -> float:
    """SPEC §14: the result must be readable and its duration plausible."""
    try:
        data = probe_io.run_ffprobe(ffprobe, path)
    except Exception as exc:  # noqa: BLE001
        raise ExportError("export.error.verify", str(exc)) from exc
    duration = float((data.get("format") or {}).get("duration") or 0)
    if not any(s.get("codec_type") == "video" for s in data.get("streams") or []):
        raise ExportError("export.error.verify", "no video stream in result")
    if abs(duration - expected) > tolerance:
        raise ExportError("export.error.verify", f"duration {duration:.2f}s, expected {expected:.2f}s")
    return duration
