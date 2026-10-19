"""Milestone 4: video decoding, timeline rules, snapshot, player and viewer integration."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import piexif
import pytest
from PIL import Image

from reefhue.core import sidecar, timecode
from reefhue.core.project import Project, detect_source_profile
from reefhue.io import snapshot as snapshot_io
from reefhue.io.video import VideoReader, read_frame, rotate_array, scaled_size
from reefhue.pipeline.params import default_edit
from tests.conftest import wait_until
from tests.media import FFMPEG, FFPROBE, make_video

needs_ffmpeg = pytest.mark.skipif(FFMPEG is None or FFPROBE is None, reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    if FFMPEG is None:
        pytest.skip("ffmpeg not on PATH")
    folder = tmp_path_factory.mktemp("clips")
    return make_video(folder / "GX010001.MP4", seconds=3.0, size="320x180")


# ---------------------------------------------------------------- headless: reader
@needs_ffmpeg
def test_reader_properties_and_accurate_seek(clip):
    with VideoReader(clip) as r:
        assert r.full_size == (320, 180)
        assert abs(r.fps - 25) < 0.01 and 2.9 <= r.duration <= 3.1
        frame = r.frame_at(1.0)
        assert abs(r.frame_time(frame) - 1.0) < 0.5 / r.fps  # exact frame, not the keyframe before it
        frame = r.frame_at(2.48)
        assert abs(r.frame_time(frame) - 2.48) < 0.5 / r.fps
        arr = r.to_array(frame, 160)
        assert arr.shape == (90, 160, 3) and arr.dtype == np.uint8
        deep = r.to_array(frame, None, deep=True)
        assert deep.shape == (180, 320, 3) and deep.dtype == np.uint16


@needs_ffmpeg
def test_reader_sequential_frames(clip):
    with VideoReader(clip) as r:
        r.seek(0.0)
        times = []
        while (frame := r.next_frame()) is not None:
            times.append(r.frame_time(frame))
        assert len(times) == 75
        assert times == sorted(times)


@needs_ffmpeg
def test_read_frame_with_rotation(clip):
    img, full, t = read_frame(clip, 0.5, rotation_cw=90, max_side=None)
    assert full == (180, 320) and img.shape == (320, 180, 3)
    assert img.dtype == np.float32 and 0 <= img.min() and img.max() <= 1
    assert abs(t - 0.5) < 0.03


def test_sizes_and_rotation_helpers():
    assert scaled_size(3840, 2160, 960) == (960, 540)
    assert scaled_size(320, 180, 960) == (320, 180)
    a = np.zeros((2, 3, 3), np.uint8)
    a[0, 0] = 255
    rotated = rotate_array(a, 90)
    assert rotated.shape == (3, 2, 3) and rotated[0, -1, 0] == 255  # clockwise


# ---------------------------------------------------------------- headless: timeline rules
def test_timecode_and_names():
    assert timecode.format_timecode(83.45, 59.94) == "00:01:23:21"
    assert timecode.format_timecode(0, 25) == "00:00:00:00"
    assert timecode.frame_number(1.0, 25) == 25
    assert timecode.snapshot_name("GX010001.MP4", 83.9) == "GX010001_00-01-23.jpg"
    assert timecode.snapshot_name("clip.mov", 3725) == "clip_01-02-05.jpg"
    assert timecode.capture_at(datetime(2026, 5, 1, 10, 0), 83.5) == datetime(2026, 5, 1, 10, 1, 23, 500000)
    assert timecode.capture_at(None, 3) is None


@pytest.mark.parametrize(
    "start, end, moved, expected",
    [
        (2.0, 5.0, "end", (2.0, 5.0)),
        (5.0, 5.0, "end", (5.0, 5.06)),     # end pushed past start + 1 frame
        (5.0, 5.0, "start", (4.94, 5.0)),  # start pushed before end
        (-1.0, 99.0, "end", (0.0, 10.0)),  # clamped to the clip
        (10.0, 10.0, "end", (9.94, 10.0)),  # squeezed at the end: the other handle moves
    ],
)
def test_clamp_segment(start, end, moved, expected):
    s, e = timecode.clamp_segment(start, end, 10.0, 25.0, moved)
    assert (s, e) == pytest.approx(expected, abs=1e-3)
    assert e - s > 1 / 25


def test_segment_or_none():
    assert timecode.segment_or_none(0.0, 10.0, 10.0, 25) is None
    assert timecode.segment_or_none(0.01, 9.99, 10.0, 25) is None  # within half a frame
    assert timecode.segment_or_none(1.0, 10.0, 10.0, 25) == {"start_s": 1.0, "end_s": 10.0}


def test_unique_path_never_overwrites(tmp_path):
    (tmp_path / "a_00-00-01.jpg").write_bytes(b"x")
    (tmp_path / "a_00-00-01_2.jpg.part").write_bytes(b"x")
    assert timecode.unique_path(tmp_path, "a_00-00-01.jpg").name == "a_00-00-01_3.jpg"


def test_write_jpeg_with_exif_and_safe_write(tmp_path):
    img = np.full((20, 30, 3), 120, np.uint8)
    target = tmp_path / "snap.jpg"
    snapshot_io.write_jpeg(img, target, datetime(2026, 5, 1, 10, 1, 23), "GoPro")
    assert not (tmp_path / "snap.jpg.part").exists()
    exif = piexif.load(str(target))
    assert exif["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:05:01 10:01:23"
    assert exif["0th"][piexif.ImageIFD.Model] == b"GoPro"
    with pytest.raises(FileExistsError):
        snapshot_io.write_jpeg(img, target)


# ---------------------------------------------------------------- headless: project
def test_source_profile_detection_and_override(tmp_path):
    assert detect_source_profile({"color_transfer": "arib-std-b67"}) == "hdr_hlg"
    assert detect_source_profile({"color_transfer": "bt709"}) == "standard"
    (tmp_path / "v.mp4").write_bytes(b"\0" * 10)
    project = Project.open(tmp_path)
    from reefhue.core import scan
    from reefhue.core.project import plan_reconcile

    project.apply_reconcile(plan_reconcile(tmp_path, [], scan.scan_folder(tmp_path, False), False))
    item = project.item_by_path("v.mp4")
    project.apply_metadata("v.mp4", 10, 0.0, {"type": "video", "color_transfer": "arib-std-b67"}, None, "filesystem", "f")
    assert item.entry["source_profile"] == {"value": "hdr_hlg", "auto_detected": True}
    project.set_source_profile(item.id, "log")
    project.apply_metadata("v.mp4", 10, 0.0, {"type": "video", "color_transfer": "bt709"}, None, "filesystem", "f")
    assert item.entry["source_profile"] == {"value": "log", "auto_detected": False}  # user choice kept
    project.set_segment(item.id, {"start_s": 1.0, "end_s": 2.0})
    assert item.status == "edited"
    project.add_derived("v_00-00-01.jpg", item.id)
    data = project.snapshot()
    assert data["derived_files"][0]["source_id"] == item.id
    assert [f["id"] for f in data["files"]] == [item.id]  # the source stays referenced


# ---------------------------------------------------------------- headless: frame loader / snapshot render
@needs_ffmpeg
def test_frame_loader_uses_cache_and_decodes_full(clip, qapp):
    from reefhue.ui.video_view import FrameLoader, playback_size, render_snapshot

    cached = np.zeros((90, 160, 3), np.float32)
    loader = FrameLoader(clip, 1.0, 0, cached, (320, 180))
    img, full = loader(160)
    assert img is cached and full == (320, 180)
    img, full = loader(None)  # full resolution decoded on demand
    assert img.shape == (180, 320, 3)
    assert playback_size("auto", 60, 3840, 2000) == 960
    assert playback_size("auto", 25, 3840, 2000) == 1280
    assert playback_size("25", 60, 3840, 2000) == 960
    edit = default_edit()
    edit["light"]["exposure_ev"] = 1.0
    bright = render_snapshot(clip, 1.0, 0, edit)
    plain = render_snapshot(clip, 1.0, 0, default_edit())
    assert bright.shape == (180, 320, 3) and bright.mean() > plain.mean()


# ---------------------------------------------------------------- Qt: player
@needs_ffmpeg
def test_player_still_play_pause_step(clip, qapp):
    from reefhue.ui.video_player import VideoPlayer

    player = VideoPlayer()
    frames, stills = [], []
    player.frame_shown.connect(frames.append)
    player.still_shown.connect(lambda t, raw: stills.append((t, raw)))
    player.open(clip, 0, True, 3.0, 25.0, default_edit(), 320, start=0.5)
    try:
        assert wait_until(lambda: stills, timeout=10)
        assert abs(stills[-1][0] - 0.5) < 0.03 and stills[-1][1].shape == (180, 320, 3)
        player.play()
        assert wait_until(lambda: player.position > 1.0, timeout=10)
        assert len(frames) > 5
        player.pause()
        assert wait_until(lambda: len(stills) >= 2, timeout=10)
        pos = player.position
        player.step(1)
        assert wait_until(lambda: abs(player.position - (pos + 0.04)) < 0.021, timeout=10)
        player.seek(2.0)
        assert wait_until(lambda: abs(player.position - 2.0) < 0.03, timeout=10)
        # segment only: playback stops at the segment end
        player.set_segment((0.2, 0.6))
        player.segment_only = True
        player.play()
        assert wait_until(lambda: not player.playing, timeout=10)
        assert player.position <= 0.6 + 0.05
        # J/K/L
        player.faster(1)
        assert player.rate == 1.0
        player.faster(1)
        assert player.rate == 2.0
        player.faster(-1)  # J reverses
        assert player.rate == -1.0
        player.pause()
        assert not player.playing
    finally:
        player.close()


# ---------------------------------------------------------------- Qt: viewer integration
@pytest.fixture
def video_window(window, tmp_path):
    from reefhue.io import ffmpeg

    if FFMPEG is None:
        pytest.skip("ffmpeg not on PATH")
    window.apply_ffmpeg_status(ffmpeg.check_ffmpeg(), notify=False)
    folder = tmp_path / "Dive"
    make_video(folder / "GX010001.MP4", seconds=3.0, size="320x180")
    window.open_folder(folder)
    ctl = window.controller
    assert wait_until(lambda: ctl.project is not None and not ctl.busy, timeout=15)
    item = ctl.project.item_by_path("GX010001.MP4")
    assert wait_until(lambda: item.entry["media"].get("codec"), timeout=15)
    window.file_pane.select_ids([item.id], item.id)
    assert window.editor.item is item
    assert wait_until(lambda: window.engine.last_final is not None and not window.engine.busy, timeout=15)
    yield window, item, folder
    window.viewer.preview.video.close()


def _key(widget, key, modifiers=None):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    event = QKeyEvent(QEvent.KeyPress, key, modifiers or Qt.NoModifier)
    widget.keyPressEvent(event)
    return event


def test_video_opens_in_editor_with_transport_and_info(video_window):
    window, item, _folder = video_window
    preview = window.viewer.preview
    assert preview.stack.currentIndex() == preview.PAGE_CANVAS
    assert not preview.canvas_page.transport.isHidden()
    assert window.viewer.edit_panel.tabs.isEnabled()
    window._actions["action.info_overlay"].trigger()
    lines = " ".join(preview.canvas.info_lines)
    assert "320 × 180" in lines and "H264" in lines and "25 fps" in lines
    assert preview.canvas.show_info


def test_video_keys_segment_and_snapshot(video_window):
    from PySide6.QtCore import Qt

    window, item, folder = video_window
    preview = window.viewer.preview
    video = preview.video
    _key(preview, Qt.Key_Right, Qt.ShiftModifier)  # +1 s
    assert wait_until(lambda: abs(video.player.position - 1.0) < 0.05, timeout=10)
    _key(preview, Qt.Key_I)
    _key(preview, Qt.Key_Period)  # one frame forward
    assert wait_until(lambda: abs(video.player.position - 1.04) < 0.03, timeout=10)
    video.player.seek(2.0)
    assert wait_until(lambda: abs(video.player.position - 2.0) < 0.03, timeout=10)
    _key(preview, Qt.Key_O)
    assert item.entry["video"]["segment"] == pytest.approx({"start_s": 1.0, "end_s": 2.0}, abs=0.05)
    assert window.file_pane.model.data(window.file_pane.model.index(
        window.file_pane.model.row_of(item.id), 9)) == "Edited"
    window.editor.set_param(("light", "exposure_ev"), 1.0, live=False)
    _key(preview, Qt.Key_S)  # snapshot
    snap = folder / "GX010001_00-00-02.jpg"
    assert wait_until(snap.exists, timeout=15)
    with Image.open(snap) as im:
        assert im.size == (320, 180)
        assert piexif.load(im.info["exif"])["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:05:01 10:00:02"
    assert wait_until(lambda: window.controller.project.derived_paths == {"GX010001_00-00-02.jpg"}, timeout=5)
    path = folder / sidecar.SIDECAR_NAME
    assert wait_until(lambda: path.exists() and json.loads(path.read_text())["derived_files"], timeout=5)
    # "Hide exported files" (default on) keeps the snapshot out of the list
    assert wait_until(lambda: not window.controller.busy, timeout=10)
    names = [window.file_pane.proxy.index(r, 1).data() for r in range(window.file_pane.proxy.rowCount())]
    assert names == ["GX010001.MP4"]
    assert "GX010001_00-00-02.jpg" in preview.canvas_page.status_label.text()


def test_space_toggles_playback_and_paused_frame_is_editable(video_window):
    window, item, _folder = video_window
    preview = window.viewer.preview
    preview.canvas.space_tapped.emit()
    assert wait_until(lambda: preview.video.player.playing, timeout=5)
    assert wait_until(lambda: preview.video.player.position > 0.4, timeout=10)
    preview.canvas.space_tapped.emit()
    assert wait_until(lambda: not preview.video.player.playing, timeout=5)
    assert wait_until(lambda: not window.engine.busy and window.engine.last_final is not None, timeout=10)
    w, h = window.engine.last_final.full_size
    assert window.editor.pick_white_balance(w / 2, h / 2, 5)  # eyedropper works on a video frame


def test_source_profile_override_saved(video_window):
    window, item, folder = video_window
    combo = window.viewer.preview.canvas_page.profile_combo
    combo.setCurrentIndex(combo.findData("log"))
    combo.activated.emit(combo.currentIndex())
    assert item.entry["source_profile"] == {"value": "log", "auto_detected": False}
    path = folder / sidecar.SIDECAR_NAME
    assert wait_until(lambda: path.exists() and json.loads(path.read_text())["files"][0]["source_profile"]["value"] == "log",
                      timeout=5)


@needs_ffmpeg
def test_player_repeated_play_pause_advances(clip, qapp):
    from reefhue.ui.video_player import VideoPlayer

    player = VideoPlayer()
    player.open(clip, 0, True, 3.0, 25.0, default_edit(), 320, start=0.0)
    try:
        last = 0.0
        for _ in range(3):
            player.play()
            assert wait_until(lambda: player.position > last + 0.3, timeout=10)
            player.pause()
            assert wait_until(lambda: not player.playing, timeout=5)
            assert player.position > last
            last = player.position
        assert player.dropped < 20
    finally:
        player.close()
