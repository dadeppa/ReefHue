"""Optional checks with real camera files (skipped when none are present).

Uses ``GX*.MP4`` / ``GOPR*.JPG`` from the folder in ``REEFHUE_REAL_MEDIA`` or, by
default, from ``samples/`` in the project (a HERO10 photo and clip). The originals are
only read: exports go to a temporary folder that contains symlinks to them.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import piexif
import pytest

from reefhue.core import export_plan as ep
from reefhue.core import scan, sidecar
from reefhue.io import export as ex
from reefhue.io import ffmpeg, metadata
from reefhue.io.video import VideoReader
from tests.media import FFMPEG, FFPROBE

ROOT = Path(os.environ.get("REEFHUE_REAL_MEDIA") or Path(__file__).resolve().parents[1] / "samples")
VIDEOS = sorted(ROOT.glob("GX*.MP4"))
PHOTOS = sorted(ROOT.glob("GOPR*.JPG"))

needs_video = pytest.mark.skipif(not VIDEOS or FFMPEG is None, reason="no real GoPro video / ffmpeg")
needs_photo = pytest.mark.skipif(not PHOTOS, reason="no real GoPro photo")


@pytest.fixture
def linked(tmp_path):
    """A temporary project folder with symlinks to the real files (originals stay untouched)."""
    folder = tmp_path / "Dive"
    folder.mkdir()
    for src in VIDEOS[:1] + PHOTOS[:1]:
        (folder / src.name).symlink_to(src)
    return folder


def _meta(folder: Path, name: str):
    status = ffmpeg.check_ffmpeg()
    scanned = next(f for f in scan.scan_folder(folder, False) if f.rel_path == name)
    return metadata.extract(folder, scanned, status.ffprobe.path if status.available else None)


def _job(folder: Path, name: str, kind: str, meta, segment=None, edit=None, opts=None) -> ep.ExportJob:
    entry = sidecar.default_file_entry(name)
    entry["media"].update(meta.media)
    entry["video"]["segment"] = segment
    if edit:
        edit(entry["edit"])
    opts = opts or ep.ExportOptions()
    return ep.make_job(entry, kind, None, False, opts, ep.target_rel_path(name, kind, opts))


def _streams(path: Path) -> list[tuple[str, str]]:
    out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return [(s["codec_type"], s.get("codec_tag_string", "")) for s in json.loads(out)["streams"]]


@needs_video
def test_real_video_metadata(linked):
    meta = _meta(linked, VIDEOS[0].name)
    assert meta.datetime_source == "ffprobe" and meta.error is None
    assert meta.media["camera"].startswith("GoPro")
    assert meta.media["width"] and meta.media["fps"] and meta.media["duration_s"]


@needs_video
def test_real_video_decode_matches_ffmpeg(linked):
    src = linked / VIDEOS[0].name
    with VideoReader(src) as reader:
        frame = reader.frame_at(min(5.0, reader.duration / 2))
        ours = reader.to_array(frame, None).astype(np.float32)
        index = round(reader.frame_time(frame) * reader.fps)  # pick the same frame by number (a rounded -ss can miss it)
        w, h = reader.width, reader.height
        full_range = frame.format.name.startswith("yuvj")
    scale = f"scale=in_color_matrix=bt709:in_range={'pc' if full_range else 'tv'}:out_range=pc,format=rgb24"
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(src), "-vf", f"select=eq(n\\,{index}),{scale}",
                          "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    ref = np.frombuffer(raw, np.uint8)[: w * h * 3].reshape(h, w, 3).astype(np.float32)
    assert np.abs(ours - ref).mean() < 1.0  # same colours as FFmpeg (incl. full-range GoPro footage)


@needs_video
def test_real_video_exports_keep_telemetry(linked):
    name = VIDEOS[0].name
    meta = _meta(linked, name)
    status = ffmpeg.check_ffmpeg()
    copy_job = _job(linked, name, "video", meta, {"start_s": 1.0, "end_s": 3.0}, opts=ep.ExportOptions(mode="subfolder"))
    result = ex.run_job(copy_job, linked, status.ffmpeg.path, status.ffprobe.path, lambda f: None, ex.Control())
    copied = _streams(result.target)
    assert ("video", "hvc1") in copied or ("video", "avc1") in copied
    if meta.media.get("has_telemetry"):
        assert ("data", "gpmd") in copied and not result.notes
    enc_job = _job(linked, name, "video", meta, {"start_s": 1.0, "end_s": 2.0},
                   edit=lambda e: e["light"].update(contrast=10))
    result = ex.run_job(enc_job, linked, status.ffmpeg.path, status.ffprobe.path, lambda f: None, ex.Control())
    encoded = _streams(result.target)
    assert encoded[0][0] == "video" and ("audio", "mp4a") in encoded
    if meta.media.get("has_telemetry"):
        assert ("data", "gpmd") in encoded and not result.notes
    reread = _meta(result.target.parent, result.target.name)
    assert reread.datetime_original == meta.datetime_original  # creation_time survives (no ";"-joined tags)
    assert all(p.is_symlink() for p in linked.iterdir() if p.name in (VIDEOS[0].name, PHOTOS[0].name if PHOTOS else ""))


@needs_photo
def test_real_photo_export_keeps_exif(linked):
    name = PHOTOS[0].name
    meta = _meta(linked, name)
    assert meta.media["codec"] == "jpeg" and meta.media["camera"]
    result = ex.run_job(_job(linked, name, "photo", meta, edit=lambda e: e["light"].update(exposure_ev=0.2)),
                        linked, None, None, lambda f: None, ex.Control())
    src_exif, out_exif = piexif.load(str(PHOTOS[0])), piexif.load(str(result.target))
    for ifd, tag in (("0th", piexif.ImageIFD.Make), ("0th", piexif.ImageIFD.Model),
                     ("Exif", piexif.ExifIFD.DateTimeOriginal), ("Exif", piexif.ExifIFD.ExposureTime)):
        assert out_exif[ifd].get(tag) == src_exif[ifd].get(tag)
    assert out_exif["0th"][piexif.ImageIFD.Orientation] == 1 and not out_exif.get("thumbnail")
    if src_exif.get("GPS"):
        assert out_exif["GPS"] == src_exif["GPS"]
