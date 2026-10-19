"""Video mode of the viewer: player ↔ canvas/engine, transport, keys, snapshot (SPEC §6)."""

from __future__ import annotations

import copy
import logging
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QObject, Qt, Signal

from reefhue.core import timecode
from reefhue.core.project import FileItem
from reefhue.io import snapshot as snapshot_io
from reefhue.io.video import read_frame
from reefhue.pipeline import pipeline as pl
from reefhue.ui.canvas import PreviewCanvas
from reefhue.ui.context import AppContext
from reefhue.ui.editor_controller import EditorController
from reefhue.ui.preview_engine import PreviewEngine
from reefhue.ui.timeline import VideoTransport
from reefhue.ui.video_player import VideoPlayer
from reefhue.ui.workers import run_task

log = logging.getLogger(__name__)

JUMP_S = 5.0
JUMP_FINE_S = 1.0
VOLUME_STEP = 0.1


class FrameLoader:
    """Preview-engine source for a paused video frame: cached preview-size pixels,
    full resolution decoded on demand (100 % view, region renders)."""

    def __init__(self, path: Path, t: float, rotation: int, cached: np.ndarray | None,
                 full_size: tuple[int, int]) -> None:
        self.path, self.t, self.rotation, self.cached, self.full_size = path, t, rotation, cached, full_size

    def __call__(self, max_side: int | None):
        if max_side and self.cached is not None:
            h, w = self.cached.shape[:2]
            if max(h, w) >= min(max_side, max(self.full_size)) * 0.98:
                if max(h, w) > max_side:
                    f = max_side / max(h, w)
                    return cv2.resize(self.cached, (round(w * f), round(h * f)), interpolation=cv2.INTER_AREA), self.full_size
                return self.cached, self.full_size
        img, full, _t = read_frame(self.path, self.t, self.rotation, max_side, deep=max_side is None)
        return img, full


def playback_size(quality: str, fps: float, source_long_side: int, canvas_physical: int) -> int:
    """Preview resolution during playback (smaller than for stills so 4K plays smoothly)."""
    if quality in ("50", "25"):
        return max(320, int(source_long_side * int(quality) / 100))
    cap = 960 if fps > 40 else 1280
    return max(480, min(cap, canvas_physical, source_long_side))


def render_snapshot(path: Path, t: float, rotation: int, edit: dict) -> np.ndarray:
    """Full-resolution frame through the same pipeline as the preview → 8-bit RGB."""
    img, full, _ = read_frame(path, t, rotation, None, deep=True)
    ctx = pl.make_context(full, full, edit)
    return pl.to_uint8(pl.process(img, edit, ctx), dither=True)


class VideoController(QObject):
    status = Signal(str)          # transient message for the canvas toolbar
    hist_ready = Signal(object)   # histogram of a played frame

    def __init__(self, ctx: AppContext, editor: EditorController, engine: PreviewEngine,
                 canvas: PreviewCanvas, transport: VideoTransport, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.ctx, self.editor, self.engine, self.canvas, self.transport = ctx, editor, engine, canvas, transport
        self.player = VideoPlayer(self)
        self.item: FileItem | None = None
        self.path: Path | None = None
        self.rotation = 0
        self.full_size = (1, 1)
        self.crop_mode = False
        self.want_original = False
        self._snapshot_busy = False

        p, t = self.player, transport
        p.frame_shown.connect(self._on_frame)
        p.still_shown.connect(self._on_still)
        p.position_changed.connect(t.set_position)
        p.state_changed.connect(lambda playing, _rate: t.set_playing(playing))
        p.fps_measured.connect(t.set_fps)
        p.failed.connect(self.status)
        t.play_toggled.connect(p.toggle)
        t.snapshot_requested.connect(self.snapshot)
        t.volume_changed.connect(p.clock.set_volume)
        t.mute_toggled.connect(p.clock.set_muted)
        t.segment_only_toggled.connect(lambda on: setattr(p, "segment_only", on))
        t.loop_toggled.connect(lambda on: setattr(p, "loop", on))
        t.timeline.seek_requested.connect(lambda sec, final: p.seek(sec, final))
        t.timeline.segment_changed.connect(self._on_segment)
        canvas.space_tapped.connect(self._on_space)

    # ---- lifecycle --------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.item is not None

    def open(self, item: FileItem, folder: Path, canvas_physical: int) -> None:
        self.close()
        media = item.entry["media"]
        self.item = item
        self.path = folder / item.rel_path
        self.rotation = int(media.get("rotation_meta") or 0)
        w, h = int(media.get("width") or 0), int(media.get("height") or 0)
        self.full_size = (h, w) if self.rotation in (90, 270) else (w, h)
        fps = float(media.get("fps") or 30.0)
        duration = float(media.get("duration_s") or 0.0)
        max_side = playback_size(self.ctx.settings.get("preview/quality"), fps, max(w, h) or 1920, canvas_physical)
        segment = item.entry["video"].get("segment")
        start = float(segment["start_s"]) if segment else 0.0
        self.transport.fps = fps
        self.transport.timeline.set_duration(duration, fps)
        self.transport.timeline.set_segment(segment)
        self.transport.set_fps(None)
        self.player.set_segment((float(segment["start_s"]), float(segment["end_s"])) if segment else None)
        self.player.segment_only = self.transport.segment_only.isChecked()
        self.player.loop = self.transport.loop.isChecked()
        self.player.clock.volume = self.transport.volume.value() / 100.0
        self.player.clock.muted = self.transport.mute_button.isChecked()
        self.player.open(self.path, self.rotation, bool(media.get("has_audio")), duration, fps,
                         self.editor.edit, max_side, start)
        self.player.set_view(self.crop_mode, self.want_original)
        self.transport.set_position(start)

    def close(self) -> None:
        self.player.close()
        self.item = None
        self.path = None
        self.transport.set_playing(False)

    # ---- integration with the viewer --------------------------------------------------------
    @property
    def playing(self) -> bool:
        return self.player.playing

    def set_edit(self, edit: dict) -> None:
        self.player.set_edit(edit)

    def set_view(self, crop_mode: bool, want_original: bool) -> None:
        self.crop_mode, self.want_original = crop_mode, want_original
        self.player.set_view(crop_mode, want_original)

    def _on_frame(self, result) -> None:
        self.canvas.set_result(result)
        if result.hist is not None:
            self.hist_ready.emit(result.hist)

    def _on_still(self, t: float, raw: np.ndarray) -> None:
        """Paused: hand the frame to the preview engine (full editing tools, 100 % view)."""
        if self.path is None:
            return
        loader = FrameLoader(self.path, t, self.rotation, raw, self.full_size)
        self.engine.set_source(self.path, 0.0, loader=loader, key=("frame", round(t, 4)))
        self.engine.request(self.editor.edit, crop_mode=self.crop_mode)

    # ---- segment -------------------------------------------------------------------------------
    def _on_segment(self, start: float, end: float, final: bool) -> None:
        self.player.set_segment((start, end))
        if final and self.item is not None:
            segment = timecode.segment_or_none(start, end, self.player.duration, self.player.fps)
            self.editor.projects.set_segment(self.item.id, segment)

    def set_segment_point(self, which: str) -> None:
        """I / O: segment start / end at the playhead."""
        bar = self.transport.timeline
        t = self.player.position
        start, end = (t, bar.end) if which == "start" else (bar.start, t)
        start, end = timecode.clamp_segment(start, end, self.player.duration, self.player.fps, which)
        bar.start, bar.end = start, end
        bar.update()
        self._on_segment(start, end, True)

    # ---- keyboard (video focus) ----------------------------------------------------------------------
    def _on_space(self) -> None:
        if self.active:
            self.player.toggle()

    def handle_key(self, event) -> bool:
        if not self.active:
            return False
        key, mods = event.key(), event.modifiers()
        shift = bool(mods & Qt.ShiftModifier)
        if mods & (Qt.ControlModifier | Qt.MetaModifier | Qt.AltModifier):
            return False
        p = self.player
        actions = {
            Qt.Key_Left: lambda: p.jump(-(JUMP_FINE_S if shift else JUMP_S)),
            Qt.Key_Right: lambda: p.jump(JUMP_FINE_S if shift else JUMP_S),
            Qt.Key_Up: lambda: self.transport.change_volume(VOLUME_STEP),
            Qt.Key_Down: lambda: self.transport.change_volume(-VOLUME_STEP),
            Qt.Key_Comma: lambda: p.step(-1),
            Qt.Key_Period: lambda: p.step(1),
            Qt.Key_J: lambda: p.faster(-1),
            Qt.Key_K: p.pause,
            Qt.Key_L: lambda: p.faster(1),
            Qt.Key_I: lambda: self.set_segment_point("start"),
            Qt.Key_O: lambda: self.set_segment_point("end"),
            Qt.Key_S: self.snapshot,
        }
        action = actions.get(key)
        if action is None:
            return False
        action()
        return True

    # ---- snapshot ----------------------------------------------------------------------------------
    def snapshot(self) -> None:
        """S / camera button: the current frame with corrections as JPEG next to the video."""
        if not self.active or self._snapshot_busy:
            return
        item, path = self.item, self.path
        if self.player.playing:
            self.player.pause()
        t = self.player.position
        edit = copy.deepcopy(self.editor.edit)
        folder = path.parent
        target = timecode.unique_path(folder, timecode.snapshot_name(path.name, t))
        capture = timecode.capture_at(item.capture_datetime, t)
        camera = item.entry["media"].get("camera")
        rotation = self.rotation
        project_folder = self.editor.projects.project.folder
        self._snapshot_busy = True

        def work():
            img = render_snapshot(path, t, rotation, edit)
            return snapshot_io.write_jpeg(img, target, capture, camera)

        def done(written: Path) -> None:
            self._snapshot_busy = False
            rel = written.relative_to(project_folder).as_posix()
            self.editor.projects.add_derived(rel, item.id)
            self.status.emit(f"snapshot:{written.name}")

        def failed(_details: str) -> None:
            self._snapshot_busy = False
            self.status.emit("video.snapshot_failed")

        run_task(work, on_done=done, on_failed=failed, guard=self)
