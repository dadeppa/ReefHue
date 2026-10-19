"""Milestone 5: export planning, photo/video export, export == preview, safe writing, trash."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import piexif
import pytest
from PIL import Image

from reefhue.core import export_plan as ep
from reefhue.core import sidecar
from reefhue.io import export as ex
from reefhue.io.decode import load_photo
from reefhue.io.video import read_frame
from reefhue.pipeline import pipeline as pl
from reefhue.pipeline.params import default_edit
from tests.media import FFMPEG, FFPROBE, gradient, make_heic, make_jpeg, make_png, make_video

needs_ffmpeg = pytest.mark.skipif(FFMPEG is None or FFPROBE is None, reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture
def fake_trash(tmp_path, monkeypatch):
    """send2trash replacement: files are moved into a temp "Trash" folder we can inspect."""
    trash = tmp_path / "Trash"
    trash.mkdir()

    def move(path: str) -> None:
        shutil.move(path, trash / Path(path).name)

    monkeypatch.setattr(ex, "send2trash", move)
    return trash


def entry_for(rel: str, kind: str, media: dict | None = None, edit=None, segment=None) -> dict:
    e = sidecar.default_file_entry(rel)
    if edit:
        edit(e["edit"])
    e["video"]["segment"] = segment
    e["media"].update(media or {})
    return e


def job(rel: str, kind: str, opts: ep.ExportOptions | None = None, override: datetime | None = None, **kw) -> ep.ExportJob:
    opts = opts or ep.ExportOptions()
    e = entry_for(rel, kind, **kw)
    return ep.make_job(e, kind, override, override is not None, opts, ep.target_rel_path(rel, kind, opts))


def run(j: ep.ExportJob, folder: Path) -> ex.ExportResult:
    ff = (Path(FFMPEG), Path(FFPROBE)) if FFMPEG else (None, None)
    return ex.run_job(j, folder, ff[0], ff[1], lambda f: None, ex.Control())


# ---------------------------------------------------------------- planning
@pytest.mark.parametrize(
    "rel, kind, opts, expected",
    [
        ("IMG_1.JPG", "photo", ep.ExportOptions(), "IMG_1_edited.JPG"),
        ("Day1/IMG_1.jpg", "photo", ep.ExportOptions(suffix="_v2"), "Day1/IMG_1_v2.jpg"),
        ("IMG_1.jpg", "photo", ep.ExportOptions(mode="subfolder"), "edited/IMG_1.jpg"),
        ("Day1/GX010001.MP4", "video", ep.ExportOptions(mode="subfolder", subfolder="out"), "Day1/out/GX010001.MP4"),
        ("IMG_1.HEIC", "photo", ep.ExportOptions(), "IMG_1_edited.jpg"),
        ("IMG_1.HEIC", "photo", ep.ExportOptions(heic_as="heic"), "IMG_1_edited.HEIC"),
        ("IMG_1.jpg", "photo", ep.ExportOptions(mode="overwrite"), "IMG_1.jpg"),
        ("IMG_1.heic", "photo", ep.ExportOptions(mode="overwrite"), "IMG_1.jpg"),
    ],
)
def test_target_paths(rel, kind, opts, expected):
    assert ep.target_rel_path(rel, kind, opts) == expected


def test_overwrite_is_never_a_default():
    opts = ep.ExportOptions.from_defaults({"mode": "overwrite"})
    assert opts.mode == "suffix"
    assert ep.ExportOptions(mode="overwrite").to_defaults()["mode"] == "suffix"


def test_auto_number():
    taken = {"a_edited_2.jpg"}
    assert ep.auto_number("a_edited.jpg", lambda r: r in taken) == "a_edited_3.jpg"
    assert ep.numbered("Day1/a.jpg", 2) == "Day1/a_2.jpg"


def test_video_decisions():
    assert ep.video_mode(default_edit()) == ep.VIDEO_COPY
    edited = default_edit()
    edited["geometry"]["rotate_90"] = 1
    assert ep.video_mode(edited) == ep.VIDEO_REENCODE
    assert ep.encoder_for("hevc", False) == "libx265" and ep.encoder_for("h264", False) == "libx264"
    assert ep.encoder_for("hevc", True) == "hevc_videotoolbox" and ep.encoder_for("prores", False) == "libx264"
    args = ep.encoder_args("libx265", "high", True)
    assert args[:4] == ["-c:v", "libx265", "-crf", "22"] and "main10" in args and "hvc1" in args
    assert ep.output_pix_fmt("libx265", True) == "yuv420p10le"
    assert ep.output_pix_fmt("h264_videotoolbox", True) == "yuv420p"
    assert ep.encoder_args("hevc_videotoolbox", "draft", False)[:4] == ["-c:v", "hevc_videotoolbox", "-q:v", "45"]


def test_plan_jobs_conflicts(tmp_path, qapp):
    from reefhue.core.project import Project, plan_reconcile
    from reefhue.core.scan import scan_folder
    from reefhue.ui.export_controller import plan_jobs

    for name in ("a.jpg", "b.jpg", "c.jpg"):
        make_jpeg(tmp_path / name)
    for name in ("a_edited.jpg", "b_edited.jpg"):
        make_jpeg(tmp_path / name)
    project = Project.open(tmp_path)
    project.apply_reconcile(plan_reconcile(tmp_path, [], scan_folder(tmp_path, False), False))
    items = [project.item_by_path(n) for n in ("a.jpg", "b.jpg", "c.jpg")]
    asked = []

    def ask(target):
        asked.append(target)
        return ep.CONFLICT_NUMBER, True  # "apply to all"

    jobs, skipped = plan_jobs(tmp_path, items, ep.ExportOptions(), ask)
    assert asked == ["a_edited.jpg"]  # asked once per run
    assert [j.target_rel for j in jobs] == ["a_edited_2.jpg", "b_edited_2.jpg", "c_edited.jpg"]
    jobs, skipped = plan_jobs(tmp_path, items, ep.ExportOptions(), lambda t: (ep.CONFLICT_SKIP, False))
    assert [i.name for i in skipped] == ["a.jpg", "b.jpg"] and len(jobs) == 1
    jobs, _ = plan_jobs(tmp_path, items, ep.ExportOptions(), lambda t: (ep.CONFLICT_REPLACE, True))
    assert [j.replace_existing for j in jobs] == [True, True, False]


# ---------------------------------------------------------------- photos
def test_photo_export_keeps_metadata_and_writes_date(tmp_path, fake_trash):
    src = tmp_path / "IMG_1.jpg"
    gps = {piexif.GPSIFD.GPSLatitudeRef: b"N", piexif.GPSIFD.GPSLatitude: ((12, 1), (30, 1), (0, 1))}
    exif = piexif.dump({"0th": {piexif.ImageIFD.Make: b"OM", piexif.ImageIFD.Model: b"TG-7",
                                piexif.ImageIFD.Orientation: 6},
                        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2024:05:01 10:00:00"}, "GPS": gps})
    icc = b"\x00" * 128 + b"fake-icc"
    gradient(300, 200).save(src, "JPEG", exif=exif, icc_profile=icc, quality=95)
    result = run(job("IMG_1.jpg", "photo", override=datetime(2025, 2, 3, 4, 5, 6)), tmp_path)
    assert result.target == tmp_path / "IMG_1_edited.jpg" and not (tmp_path / "IMG_1_edited.jpg.part").exists()
    with Image.open(result.target) as im:
        assert im.size == (200, 300)  # orientation 6 applied to the pixels …
        assert im.info.get("icc_profile") == icc
        data = piexif.load(im.info["exif"])
    assert data["0th"][piexif.ImageIFD.Orientation] == 1  # … and the tag says "upright"
    assert data["0th"][piexif.ImageIFD.Model] == b"TG-7"
    assert data["GPS"][piexif.GPSIFD.GPSLatitudeRef] == b"N"
    assert data["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2025:02:03 04:05:06"
    assert data["Exif"][piexif.ExifIFD.PixelXDimension] == 200
    assert src.read_bytes()  # original untouched
    assert list(fake_trash.iterdir()) == []


def test_png_stays_png_and_heic_becomes_jpeg(tmp_path, fake_trash):
    make_png(tmp_path / "p.png")
    make_heic(tmp_path / "h.heic")
    assert run(job("p.png", "photo"), tmp_path).target.name == "p_edited.png"
    with Image.open(tmp_path / "p_edited.png") as im:
        assert im.format == "PNG"
    out = run(job("h.heic", "photo"), tmp_path).target
    with Image.open(out) as im:
        assert im.format == "JPEG" and out.name == "h_edited.jpg"


def test_16bit_png_stays_16bit(tmp_path, fake_trash):
    deep = (np.linspace(0, 65535, 40 * 30 * 3).reshape(30, 40, 3)).astype(np.uint16)
    cv2.imwrite(str(tmp_path / "d.png"), deep)
    result = run(job("d.png", "photo"), tmp_path)
    assert "export.note.no_metadata_16bit" in result.notes
    assert cv2.imread(str(result.target), cv2.IMREAD_UNCHANGED).dtype == np.uint16


def test_photo_export_matches_preview(tmp_path, fake_trash):
    """Acceptance: export == preview within tolerance (same pipeline, different resolution)."""
    src = tmp_path / "reef.jpg"
    base = np.asarray(gradient(1200, 800, cast=(0.3, 0.75, 1.0)), dtype=np.float32)
    rng = np.random.default_rng(2)
    blobs = cv2.GaussianBlur(rng.random((800, 1200, 3)).astype(np.float32) * 255, (0, 0), 6)
    Image.fromarray(np.clip(base * 0.7 + blobs * 0.3, 0, 255).astype(np.uint8)).save(src, quality=97)

    def edit(e):
        e["underwater"]["depth_m"] = 6
        e["light"].update(contrast=20, shadows=25)
        e["details"].update(dehaze=20, clarity=25, sharpen_amount=30)
        e["color"]["vibrance"] = 20
        e["geometry"].update(straighten_deg=3.0, crop={"x": 0.05, "y": 0.1, "w": 0.85, "h": 0.8, "aspect": "free"})

    exported = run(job("reef.jpg", "photo", edit=edit), tmp_path).target
    edit_dict = default_edit()
    edit(edit_dict)
    small, full = load_photo(src, max_side=600)  # what the preview engine decodes
    preview = pl.to_uint8(pl.process(small, edit_dict, pl.make_context(full, (small.shape[1], small.shape[0]), edit_dict)))
    with Image.open(exported) as im:
        out = np.asarray(im.convert("RGB"))
    assert abs(out.shape[1] / preview.shape[1] - 2.0) < 0.02
    out_small = cv2.resize(out, (preview.shape[1], preview.shape[0]), interpolation=cv2.INTER_AREA)
    diff = np.abs(out_small.astype(np.float32) - preview.astype(np.float32)) / 255.0
    assert diff.mean() < 0.012, diff.mean()
    assert np.percentile(diff, 99) < 0.08


# ---------------------------------------------------------------- safe writing, trash, space
def test_overwrite_moves_original_to_trash(tmp_path, fake_trash):
    make_jpeg(tmp_path / "a.jpg")
    original = (tmp_path / "a.jpg").read_bytes()
    result = run(job("a.jpg", "photo", ep.ExportOptions(mode="overwrite"),
                     edit=lambda e: e["color"].update(saturation=-100)), tmp_path)
    assert result.original_trashed
    assert (fake_trash / "a.jpg").read_bytes() == original  # in the trash, not deleted
    with Image.open(tmp_path / "a.jpg") as im:
        rgb = np.asarray(im.convert("RGB")).astype(int)
    assert np.abs(rgb[..., 0] - rgb[..., 2]).mean() < 3  # desaturated version now in place


def test_replace_moves_previous_export_to_trash(tmp_path, fake_trash):
    make_jpeg(tmp_path / "a.jpg")
    (tmp_path / "a_edited.jpg").write_bytes(b"old export")
    j = job("a.jpg", "photo")
    with pytest.raises(ex.ExportError) as err:
        run(j, tmp_path)
    assert err.value.key == "export.error.exists"
    j.replace_existing = True
    run(j, tmp_path)
    assert (fake_trash / "a_edited.jpg").read_bytes() == b"old export"


def test_failure_and_cancel_leave_no_part_files(tmp_path, fake_trash, monkeypatch):
    make_jpeg(tmp_path / "a.jpg")
    monkeypatch.setattr(pl, "process", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        run(job("a.jpg", "photo"), tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["a.jpg"]
    monkeypatch.undo()
    control = ex.Control()
    control.cancel.set()
    with pytest.raises(ex.ExportCancelled):
        ex.run_job(job("a.jpg", "photo"), tmp_path, None, None, lambda f: None, control)
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["a.jpg"]


def test_free_space_is_checked(tmp_path, monkeypatch):
    make_jpeg(tmp_path / "a.jpg")
    monkeypatch.setattr(ex.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10, 9, 1))
    with pytest.raises(ex.ExportError) as err:
        run(job("a.jpg", "photo"), tmp_path)
    assert err.value.key == "export.error.disk_space"


def test_missing_source(tmp_path):
    with pytest.raises(ex.ExportError) as err:
        run(job("nope.jpg", "photo"), tmp_path)
    assert err.value.key == "export.error.missing_source"


# ---------------------------------------------------------------- videos
def _probe(path: Path) -> dict:
    out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


VIDEO_MEDIA = {"width": 320, "height": 180, "fps": 25.0, "duration_s": 3.0, "codec": "h264", "bit_depth": 8}


@needs_ffmpeg
def test_video_reencode_segment_audio_and_date(tmp_path, fake_trash):
    make_video(tmp_path / "GX010001.MP4", seconds=3.0, size="320x180")
    result = run(job("GX010001.MP4", "video", media=VIDEO_MEDIA, segment={"start_s": 0.5, "end_s": 2.5},
                     edit=lambda e: e["light"].update(contrast=30), override=datetime(2026, 6, 7, 8, 9, 10)), tmp_path)
    assert result.encoder["codec"] == "x264"
    data = _probe(result.target)
    assert abs(float(data["format"]["duration"]) - 2.0) < 0.15
    assert {s["codec_type"] for s in data["streams"]} >= {"video", "audio"}
    assert data["format"]["tags"]["creation_time"].startswith("2026-06-07T08:09:10")


@needs_ffmpeg
def test_video_crop_rotate_changes_size_even(tmp_path, fake_trash):
    make_video(tmp_path / "v.mp4", seconds=1.0, size="320x180", audio=False)

    def edit(e):
        e["geometry"]["rotate_90"] = 1
        e["geometry"]["crop"] = {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.51, "aspect": "free"}

    result = run(job("v.mp4", "video", media={**VIDEO_MEDIA, "duration_s": 1.0}, edit=edit), tmp_path)
    video = next(s for s in _probe(result.target)["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (90, 162)  # rotated (180×320), cropped, even


@needs_ffmpeg
def test_video_lossless_cut_and_remove_audio(tmp_path, fake_trash):
    make_video(tmp_path / "v.mp4", seconds=3.0, size="320x180")
    j = job("v.mp4", "video", ep.ExportOptions(mode="subfolder"), media=VIDEO_MEDIA,
            segment={"start_s": 1.0, "end_s": 2.0})
    j.export_options["keep_audio"] = False
    result = run(j, tmp_path)
    assert result.encoder == {"codec": "copy", "quality": None, "hw": False}
    data = _probe(result.target)
    assert [s["codec_type"] for s in data["streams"]] == ["video"]
    assert result.target == tmp_path / "edited" / "v.mp4"


@needs_ffmpeg
def test_video_10bit_hevc_stays_10bit(tmp_path, fake_trash):
    src = tmp_path / "h.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=1",
                    "-c:v", "libx265", "-x265-params", "log-level=error", "-pix_fmt", "yuv420p10le", str(src)], check=True)
    media = {"width": 320, "height": 240, "fps": 25.0, "duration_s": 1.0, "codec": "hevc", "bit_depth": 10}
    result = run(job("h.mp4", "video", media=media, edit=lambda e: e["light"].update(exposure_ev=0.3)), tmp_path)
    video = next(s for s in _probe(result.target)["streams"] if s["codec_type"] == "video")
    assert video["codec_name"] == "hevc" and video["pix_fmt"] == "yuv420p10le"
    assert video.get("codec_tag_string") == "hvc1"


@needs_ffmpeg
def test_telemetry_failure_falls_back_with_notice(tmp_path, fake_trash, monkeypatch):
    make_video(tmp_path / "v.mp4", seconds=1.0, size="320x180")
    monkeypatch.setattr(ex, "telemetry_streams", lambda probe: [7])  # a stream that does not exist
    result = run(job("v.mp4", "video", media={**VIDEO_MEDIA, "duration_s": 1.0},
                     edit=lambda e: e["light"].update(contrast=10)), tmp_path)
    assert "export.note.telemetry_dropped" in result.notes and result.target.exists()
    assert not list(tmp_path.glob("*.part*"))


def test_telemetry_stream_detection():
    probe = {"streams": [{"index": 0, "codec_type": "video"}, {"index": 2, "codec_type": "data", "codec_tag_string": "tmcd"},
                         {"index": 3, "codec_type": "data", "codec_tag_string": "gpmd",
                          "tags": {"handler_name": "\tGoPro MET"}}]}
    assert ex.telemetry_streams(probe) == [3]


@needs_ffmpeg
def test_video_export_matches_preview(tmp_path, fake_trash, qapp):
    """Acceptance: a frame of the exported video ≈ the same frame in the playback preview."""
    from reefhue.ui.video_player import render_frame

    src = make_video(tmp_path / "v.mp4", seconds=2.0, size="640x360")

    def edit(e):
        e["underwater"]["depth_m"] = 5
        e["light"]["contrast"] = 25
        e["color"]["saturation"] = 20

    media = {**VIDEO_MEDIA, "width": 640, "height": 360, "duration_s": 2.0}
    result = run(job("v.mp4", "video", ep.ExportOptions(video_quality="very_high"), media=media, edit=edit), tmp_path)
    edit_dict = default_edit()
    edit(edit_dict)
    t = 1.0
    raw, full, _ = read_frame(src, t, 0, max_side=320)
    preview = render_frame((raw * 255).round().astype(np.uint8), edit_dict, full, False, False, False, 1)
    img = preview.image
    ptr = img.constBits()
    prev = np.frombuffer(ptr, np.uint8, img.sizeInBytes()).reshape(img.height(), img.bytesPerLine())[:, : img.width() * 3]
    prev = prev.reshape(img.height(), img.width(), 3)
    exported, _full, _ = read_frame(result.target, t, 0, max_side=320)
    exported = (exported * 255).astype(np.float32)
    diff = np.abs(exported - prev.astype(np.float32)) / 255.0
    assert diff.mean() < 0.03, diff.mean()


def test_sleep_inhibitor(monkeypatch):
    from reefhue.io import sleep_inhibit

    calls = []

    class FakeProc:
        def __init__(self, cmd, **kw):
            calls.append(cmd)

        def terminate(self):
            calls.append("terminate")

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(sleep_inhibit.subprocess, "Popen", FakeProc)
    inhibitor = sleep_inhibit.SleepInhibitor("darwin")
    inhibitor.start()
    inhibitor.start()  # idempotent
    inhibitor.stop()
    assert calls[0][:2] == ["/usr/bin/caffeinate", "-i"] and calls[-1] == "terminate" and len(calls) == 2
    sleep_inhibit.SleepInhibitor("linux").start()  # no-op elsewhere
