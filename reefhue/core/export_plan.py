"""Export planning (SPEC §14): options, target paths, conflicts, copy vs. re-encode. No Qt.

Everything here is pure: the UI builds :class:`ExportJob` snapshots at queue
time (later edits do not change a running export) and ``reefhue.io.export``
executes them.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from typing import Callable

from reefhue.core import extensions, sidecar, status
from reefhue.core.timeutil import format_wallclock
from reefhue.pipeline.params import default_edit

MODE_SUFFIX = "suffix"
MODE_SUBFOLDER = "subfolder"
MODE_OVERWRITE = "overwrite"
MODES = (MODE_SUFFIX, MODE_SUBFOLDER, MODE_OVERWRITE)

CONFLICT_SKIP = "skip"
CONFLICT_REPLACE = "replace"
CONFLICT_NUMBER = "number"

VIDEO_COPY = "copy"         # stream copy (lossless cut / remux)
VIDEO_REENCODE = "reencode"  # frames from the pipeline piped to FFmpeg

HEIC_EXTS = ("heic", "heif")
PHOTO_FORMATS = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "tif": "TIFF", "tiff": "TIFF", "webp": "WEBP",
                 "heic": "HEIF", "heif": "HEIF"}
VIDEO_CONTAINERS = {"mp4": "mp4", "m4v": "mp4", "mov": "mov", "mkv": "matroska", "avi": "avi"}

# (CRF, preset) per quality level. x265 CRF ≈ x264 CRF + 2 for similar quality.
QUALITY = {
    "libx264": {"draft": (28, "veryfast"), "high": (20, "medium"), "very_high": (16, "slow")},
    "libx265": {"draft": (30, "veryfast"), "high": (22, "medium"), "very_high": (18, "slow")},
}
VT_QUALITY = {"draft": 45, "high": 65, "very_high": 80}  # VideoToolbox constant quality (-q:v)


@dataclass
class ExportOptions:
    mode: str = MODE_SUFFIX
    suffix: str = "_edited"
    subfolder: str = "edited"
    jpeg_quality: int = 95
    heic_as: str = "jpeg"        # "jpeg" | "heic"
    video_quality: str = "high"
    hw_encoder: bool = False
    keep_telemetry: bool = True

    @classmethod
    def from_defaults(cls, defaults: dict) -> "ExportOptions":
        mode = defaults.get("mode", MODE_SUFFIX)
        return cls(
            mode=mode if mode in (MODE_SUFFIX, MODE_SUBFOLDER) else MODE_SUFFIX,  # overwrite never preselected
            suffix=str(defaults.get("suffix") or "_edited"),
            subfolder=str(defaults.get("subfolder") or "edited"),
            jpeg_quality=int(defaults.get("jpeg_quality", 95)),
            heic_as=defaults.get("heic_as", "jpeg") if defaults.get("heic_as") in ("jpeg", "heic") else "jpeg",
            video_quality=defaults.get("video_quality", "high"),
            hw_encoder=bool(defaults.get("hw_encoder", False)),
            keep_telemetry=bool(defaults.get("keep_telemetry", True)),
        )

    def to_defaults(self) -> dict:
        """Values remembered in the project's ``export_defaults`` (never "overwrite")."""
        return {
            "mode": self.mode if self.mode != MODE_OVERWRITE else MODE_SUFFIX,
            "suffix": self.suffix, "subfolder": self.subfolder, "jpeg_quality": self.jpeg_quality,
            "heic_as": self.heic_as, "video_quality": self.video_quality, "hw_encoder": self.hw_encoder,
            "keep_telemetry": self.keep_telemetry,
        }


# --------------------------------------------------------------------------- targets
def output_ext(ext: str, kind: str, opts: ExportOptions) -> str:
    """Photos keep their format (HEIC → JPEG by default); videos keep their container."""
    ext = ext.lower()
    if kind == extensions.PHOTO and ext in HEIC_EXTS and opts.heic_as == "jpeg":
        return "jpg"
    return ext


def target_rel_path(rel_path: str, kind: str, opts: ExportOptions) -> str:
    """Relative (POSIX) target path for one file, before conflict handling."""
    path = PurePosixPath(rel_path)
    ext = output_ext(path.suffix.lstrip("."), kind, opts)
    original_ext = path.suffix.lstrip(".")
    if opts.mode == MODE_OVERWRITE:
        return rel_path if ext == original_ext.lower() else str(path.with_suffix("." + ext))
    keep_case = original_ext if original_ext.lower() == ext else ext
    parent = "" if str(path.parent) == "." else f"{path.parent}/"
    if opts.mode == MODE_SUBFOLDER:
        return f"{parent}{opts.subfolder}/{path.stem}.{keep_case}"
    return f"{parent}{path.stem}{opts.suffix}.{keep_case}"


def numbered(rel_path: str, n: int) -> str:
    """``IMG_1_edited.jpg`` → ``IMG_1_edited_2.jpg``."""
    path = PurePosixPath(rel_path)
    name = f"{path.stem}_{n}{path.suffix}"
    return name if str(path.parent) == "." else f"{path.parent}/{name}"


def auto_number(rel_path: str, taken: Callable[[str], bool]) -> str:
    n = 2
    candidate = numbered(rel_path, n)
    while taken(candidate):
        n += 1
        candidate = numbered(rel_path, n)
    return candidate


# --------------------------------------------------------------------------- video decisions
def is_neutral_edit(edit: dict) -> bool:
    return status._normalize_numbers(edit) == status._normalize_numbers(default_edit())  # noqa: SLF001


def video_mode(edit: dict) -> str:
    """Re-encode whenever anything is edited; a pure segment cut (or nothing) is a lossless copy."""
    return VIDEO_COPY if is_neutral_edit(edit) else VIDEO_REENCODE


def encoder_for(source_codec: str | None, hw: bool) -> str:
    """Same codec family as the source (H.264 → H.264, HEVC → HEVC); others → H.264."""
    hevc = (source_codec or "").lower() in ("hevc", "h265")
    if hw:
        return "hevc_videotoolbox" if hevc else "h264_videotoolbox"
    return "libx265" if hevc else "libx264"


def encoder_args(encoder: str, quality: str, ten_bit: bool) -> list[str]:
    if encoder in QUALITY:
        crf, preset = QUALITY[encoder].get(quality, QUALITY[encoder]["high"])
        args = ["-c:v", encoder, "-crf", str(crf), "-preset", preset]
        if encoder == "libx265":
            args += ["-tag:v", "hvc1", "-x265-params", "log-level=error"]  # hvc1: plays in Apple apps
            if ten_bit:
                args += ["-profile:v", "main10"]
        elif ten_bit:
            args += ["-profile:v", "high10"]
        return args
    args = ["-c:v", encoder, "-q:v", str(VT_QUALITY.get(quality, VT_QUALITY["high"]))]
    if encoder == "hevc_videotoolbox":
        args += ["-tag:v", "hvc1"]
        if ten_bit:
            args += ["-profile:v", "main10"]
    return args


def output_pix_fmt(encoder: str, ten_bit: bool) -> str:
    if not ten_bit or encoder == "h264_videotoolbox":  # VideoToolbox H.264 is 8-bit only
        return "yuv420p"
    return "p010le" if encoder == "hevc_videotoolbox" else "yuv420p10le"


# --------------------------------------------------------------------------- jobs
@dataclass
class ExportJob:
    """A frozen snapshot of everything needed to export one file."""

    item_id: str
    name: str
    kind: str                 # "photo" | "video"
    src_rel: str
    target_rel: str
    opts: ExportOptions
    edit: dict
    segment: dict | None
    export_options: dict
    media: dict
    capture_override: datetime | None   # write this capture date into the output
    capture: datetime | None            # effective capture date
    settings_hash: str
    replace_existing: bool = False       # conflict answered with "replace"
    id: str = field(default_factory=sidecar.new_id)

    @property
    def overwrite(self) -> bool:
        return self.opts.mode == MODE_OVERWRITE

    @property
    def video_mode(self) -> str:
        return video_mode(self.edit)

    @property
    def cost(self) -> float:
        """Rough relative work units for progress/ETA (a 12 MP photo ≈ 1)."""
        media = self.media
        if self.kind == "video":
            seg = self.segment
            length = (seg["end_s"] - seg["start_s"]) if seg else float(media.get("duration_s") or 1)
            frames = length * float(media.get("fps") or 30)
            pixels = float(media.get("width") or 1920) * float(media.get("height") or 1080)
            per_frame = 0.02 if self.video_mode == VIDEO_COPY else pixels / 12e6
            return max(1.0, frames * per_frame)
        return max(0.2, float(media.get("width") or 4000) * float(media.get("height") or 3000) / 12e6)


def make_job(entry: dict, kind: str, capture: datetime | None, overridden: bool, opts: ExportOptions,
             target_rel: str) -> ExportJob:
    return ExportJob(
        item_id=entry["id"],
        name=PurePosixPath(entry["path"]).name,
        kind=kind,
        src_rel=entry["path"],
        target_rel=target_rel,
        opts=copy.deepcopy(opts),
        edit=copy.deepcopy(entry["edit"]),
        segment=copy.deepcopy((entry.get("video") or {}).get("segment")),
        export_options=copy.deepcopy(entry.get("export_options") or {}),
        media=copy.deepcopy(entry.get("media") or {}),
        capture_override=capture if overridden else None,
        capture=capture,
        settings_hash=status.settings_hash(entry),
    )


def history_record(job: ExportJob, result: str, encoder: dict | None = None, original_trashed: bool = False,
                   timestamp: datetime | None = None, notes: list[str] | None = None) -> dict:
    """An ``export_history`` entry (SPEC §13)."""
    from reefhue.pipeline.params import PIPELINE_VERSION

    record = {
        "id": job.id,
        "timestamp": format_wallclock((timestamp or datetime.now()).replace(microsecond=0)),
        "mode": job.opts.mode,
        "target_path": job.target_rel,
        "settings_hash": job.settings_hash,
        "pipeline_version": PIPELINE_VERSION,
        "result": result,
        "original_trashed": original_trashed,
        "encoder": encoder,
    }
    if notes:
        record["notes"] = list(notes)
    return record
