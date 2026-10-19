"""Metadata extraction (EXIF / ffprobe), thumbnails in the app cache, folder lock."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from reefhue.core import lock, meta_cache, paths, scan
from reefhue.io import exif, metadata, probe, thumbs
from tests.media import FFMPEG, FFPROBE, make_heic, make_jpeg, make_png, make_video

needs_ffmpeg = pytest.mark.skipif(FFMPEG is None or FFPROBE is None, reason="ffmpeg/ffprobe not on PATH")


def _scanned(folder: Path, rel: str) -> scan.ScannedFile:
    return next(f for f in scan.scan_folder(folder, True) if f.rel_path == rel)


def test_photo_metadata_jpeg(tmp_path):
    make_jpeg(tmp_path / "a.jpg", make="Canon", model="Canon EOS R5", orientation=8, size=(80, 60))
    media, dt = exif.read_photo_metadata(tmp_path / "a.jpg")
    assert dt == "2024-05-01T10:11:12"
    assert media["camera"] == "Canon EOS R5"  # model already contains the make
    assert (media["width"], media["height"]) == (80, 60)
    assert media["rotation_meta"] == 270
    assert media["bit_depth"] == 8


def test_photo_without_exif_falls_back_to_filesystem(tmp_path):
    make_png(tmp_path / "p.png")
    result = metadata.extract(tmp_path, _scanned(tmp_path, "p.png"), None)
    assert result.datetime_source == "filesystem"
    assert result.media["type"] == "photo"
    assert result.error is None


def test_heic_is_readable(tmp_path):
    make_heic(tmp_path / "h.heic")
    media, _dt = exif.read_photo_metadata(tmp_path / "h.heic")
    assert (media["width"], media["height"]) == (64, 48)


def test_unreadable_photo_does_not_crash(tmp_path):
    (tmp_path / "broken.jpg").write_bytes(b"not a jpeg at all")
    result = metadata.extract(tmp_path, _scanned(tmp_path, "broken.jpg"), None)
    assert result.error
    assert result.datetime_source == "filesystem"


def test_video_without_ffprobe_uses_filesystem(tmp_path):
    (tmp_path / "v.mp4").write_bytes(b"\x00" * 100)
    result = metadata.extract(tmp_path, _scanned(tmp_path, "v.mp4"), None)
    assert result.media == {"type": "video"}
    assert result.datetime_source == "filesystem"


def test_parse_probe_gopro_like():
    data = {
        "format": {"duration": "511.5", "tags": {"creation_time": "2026-05-01T10:00:00.000000Z", "firmware": "H23"}},
        "streams": [
            {"codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le", "width": 3840, "height": 2160,
             "avg_frame_rate": "60000/1001", "color_transfer": "bt709", "color_primaries": "bt709",
             "tags": {"handler_name": "GoPro H.265"}, "side_data_list": [{"rotation": -90}]},
            {"codec_type": "audio", "codec_name": "aac"},
            {"codec_type": "data", "codec_tag_string": "tmcd"},
            {"codec_type": "data", "codec_tag_string": "gpmd", "tags": {"handler_name": "GoPro MET"}},
        ],
    }
    media, dt = probe.parse_probe(data)
    assert dt == "2026-05-01T10:00:00"  # Z dropped, no conversion
    assert media["codec"] == "hevc" and media["bit_depth"] == 10
    assert media["fps"] == 59.94
    assert media["duration_s"] == 511.5
    assert media["rotation_meta"] == 90
    assert media["camera"] == "GoPro HERO12 Black"  # from the firmware tag (H23)
    assert media["has_audio"] and media["has_telemetry"]


def test_parse_probe_prefers_apple_local_time():
    data = {"format": {"tags": {"creation_time": "2026-05-01T08:00:00Z",
                                "com.apple.quicktime.creationdate": "2026-05-01T10:00:00+0200",
                                "com.apple.quicktime.make": "Apple",
                                "com.apple.quicktime.model": "iPhone 15"}},
            "streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p",
                         "tags": {"rotate": "90"}}]}
    media, dt = probe.parse_probe(data)
    assert dt == "2026-05-01T10:00:00"
    assert media["camera"] == "Apple iPhone 15"
    assert media["rotation_meta"] == 90
    assert media["bit_depth"] == 8
    assert not media["has_audio"]


def test_parse_probe_clock_reset_date_is_still_used():
    data = {"format": {"tags": {"creation_time": "1970-01-01T00:05:00Z"}}, "streams": []}
    _media, dt = probe.parse_probe(data)
    assert dt == "1970-01-01T00:05:00"


@needs_ffmpeg
def test_real_ffprobe_on_synthetic_video(tmp_path):
    make_video(tmp_path / "GX010001.MP4", seconds=1.0)
    result = metadata.extract(tmp_path, _scanned(tmp_path, "GX010001.MP4"), Path(FFPROBE))
    assert result.error is None
    assert result.datetime_source == "ffprobe"
    assert result.datetime_original == "2026-05-01T10:00:00"
    assert result.media["codec"] == "h264"
    assert (result.media["width"], result.media["height"]) == (160, 90)
    assert result.media["has_audio"] is True
    assert 0.9 <= result.media["duration_s"] <= 1.2


def test_photo_thumbnail_goes_to_app_cache(tmp_path):
    project = tmp_path / "project"
    make_jpeg(project / "a.jpg", orientation=6, size=(800, 400))
    st = (project / "a.jpg").stat()
    thumb = thumbs.ensure_thumbnail(project / "a.jpg", "photo", st.st_size, st.st_mtime)
    assert thumb is not None and thumb.exists()
    assert paths.cache_dir() in thumb.parents
    assert sorted(p.name for p in project.iterdir()) == ["a.jpg"]  # nothing in the project folder
    with Image.open(thumb) as img:
        assert max(img.size) <= thumbs.THUMB_MAX_PX
        assert img.height > img.width  # EXIF orientation 6 applied
    again = thumbs.ensure_thumbnail(project / "a.jpg", "photo", st.st_size, st.st_mtime)
    assert again == thumb


def test_thumbnail_failure_returns_none(tmp_path):
    (tmp_path / "x.jpg").write_bytes(b"garbage")
    assert thumbs.ensure_thumbnail(tmp_path / "x.jpg", "photo", 7, 0.0) is None
    assert thumbs.ensure_thumbnail(tmp_path / "x.mp4", "video", 7, 0.0, ffmpeg=None) is None


@needs_ffmpeg
def test_video_thumbnail(tmp_path):
    make_video(tmp_path / "v.mp4", seconds=2.0, size="320x180")
    st = (tmp_path / "v.mp4").stat()
    thumb = thumbs.ensure_thumbnail(tmp_path / "v.mp4", "video", st.st_size, st.st_mtime, Path(FFMPEG), 2.0)
    assert thumb is not None
    with Image.open(thumb) as img:
        assert img.size == (320, 180)


def test_meta_cache_round_trip(tmp_path):
    meta_cache.save(tmp_path, {"a.jpg": {"size": 5, "mtime": 12.5, "media": {"type": "photo"}}})
    cache = meta_cache.load(tmp_path)
    assert meta_cache.lookup(cache, "a.jpg", 5, 12.5)["media"] == {"type": "photo"}
    assert meta_cache.lookup(cache, "a.jpg", 6, 12.5) is None
    assert meta_cache.lookup(cache, "a.jpg", 5, 13.0) is None


def test_folder_lock(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    first = lock.FolderLock(folder)
    assert first.acquire()
    # the same process may reopen its own folder
    assert lock.FolderLock(folder).acquire()
    first.release()
    assert not first.path.exists()


def test_folder_lock_held_by_other_live_process(tmp_path, monkeypatch):
    folder = tmp_path / "proj"
    folder.mkdir()
    holder = lock.FolderLock(folder)
    holder.path.write_text(f'{{"pid": {os.getppid()}, "host": "{lock.socket.gethostname()}"}}')
    assert not lock.FolderLock(folder).acquire()
    # stale lock (dead pid) is taken over
    monkeypatch.setattr(lock, "pid_alive", lambda pid: False)
    assert lock.FolderLock(folder).acquire()


def test_pid_alive():
    assert lock.pid_alive(os.getpid())
    assert not lock.pid_alive(0)
