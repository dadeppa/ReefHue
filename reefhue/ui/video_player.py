"""Video playback (SPEC §6 "Video transport"): audio is the clock, frames come from PyAV
through the shared pipeline, late frames are dropped instead of slowing down.

* :class:`MediaClock` – audio via Qt Multimedia (video track disabled), interpolated
  between position updates; an internal monotonic clock when there is no audio or
  for reverse playback.
* :class:`_DecodeThread` – decodes + renders at preview size on its own thread,
  skips frames that are already late, seeks ahead when far behind, and serves
  "still" requests (seek, frame step, scrubbing, reverse) with "latest wins".
* :class:`VideoPlayer` – UI-thread orchestration and a display tick that shows the
  newest frame due according to the clock.
"""

from __future__ import annotations

import collections
import copy
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QElapsedTimer, QObject, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QImage

from reefhue.core.timecode import snap_to_frame
from reefhue.io.video import VideoReader
from reefhue.pipeline import geometry as geo
from reefhue.pipeline import pipeline as pl
from reefhue.pipeline.analysis import histogram
from reefhue.ui.preview_engine import RenderResult, to_qimage

log = logging.getLogger(__name__)

QUEUE_MAX = 4
CATCH_UP_S = 2.0     # further behind than this → try seeking ahead (late frames are dropped cheaply anyway)
CATCH_UP_LEAD_S = 0.25
HIST_EVERY = 6       # compute the histogram every n-th played frame
TICK_MS = 8
MAX_RATE = 8
REVERSE_STILL_MS = 120
RENDER_WORKERS = max(2, min(4, (os.cpu_count() or 4) // 2))  # frames rendered in parallel, emitted in order
STATS_REFRESH_FRAMES = 30  # re-estimate whole-frame stats (dehaze airlight) about twice a second


# =============================================================================== clock
class MediaClock(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._timer = QElapsedTimer()
        self._timer.start()
        self._pos = 0.0          # media time when paused / at the last anchor
        self._anchor_wall = 0.0
        self._rate = 1.0
        self._playing = False
        self._audio = None       # (QMediaPlayer, QAudioOutput) when the file has audio
        self._last_audio = (-1.0, 0.0)
        self.volume = 0.8
        self.muted = False

    def _wall(self) -> float:
        return self._timer.nsecsElapsed() / 1e9

    def open(self, path: Path | None, has_audio: bool) -> None:
        self.close()
        self._pos, self._playing, self._rate = 0.0, False, 1.0
        if path is None or not has_audio:
            return
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError:  # pragma: no cover – Qt Multimedia missing
            log.warning("Qt Multimedia unavailable – playing without sound")
            return
        player, output = QMediaPlayer(self), QAudioOutput(self)
        player.setAudioOutput(output)
        output.setVolume(self.volume)
        output.setMuted(self.muted)
        player.mediaStatusChanged.connect(lambda status, p=player: self._on_status(p, status))
        player.setSource(QUrl.fromLocalFile(str(path)))
        self._audio = (player, output)

    @staticmethod
    def _on_status(player, status) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        if status == QMediaPlayer.LoadedMedia:
            try:
                player.setActiveVideoTrack(-1)  # audio only: never decode the video twice
            except AttributeError:
                pass

    def close(self) -> None:
        if self._audio is not None:
            player, output = self._audio
            player.stop()
            player.deleteLater()
            output.deleteLater()
            self._audio = None

    @property
    def has_audio(self) -> bool:
        return self._audio is not None

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def rate(self) -> float:
        return self._rate

    def _audio_active(self) -> bool:
        return self._audio is not None and self._playing and self._rate > 0

    def now(self) -> float:
        if not self._playing:
            return self._pos
        wall = self._wall()
        internal = self._pos + (wall - self._anchor_wall) * self._rate
        if self._audio_active():
            player = self._audio[0]
            pos = player.position() / 1000.0
            last_pos, last_wall = self._last_audio
            if pos != last_pos:
                self._last_audio = (pos, wall)
                last_pos, last_wall = pos, wall
            if last_pos >= 0:
                audio_t = last_pos + min(0.25, wall - last_wall) * self._rate
                # Right after seek/play the audio position can still be stale: trust it
                # only when it agrees with the expected time.
                if abs(audio_t - internal) < 0.3:
                    self._pos, self._anchor_wall = audio_t, wall  # audio stays the master clock
                    return audio_t
        return internal

    def play(self, rate: float = 1.0) -> None:
        self._pos = self.now()
        self._anchor_wall = self._wall()
        self._rate = rate
        self._playing = True
        self._last_audio = (-1.0, 0.0)
        if self._audio is not None:
            player = self._audio[0]
            if rate > 0:
                player.setPosition(int(self._pos * 1000))
                player.setPlaybackRate(rate)
                player.play()
            else:
                player.pause()  # reverse: silent, internal clock

    def pause(self) -> None:
        self._pos = self.now()
        self._playing = False
        if self._audio is not None:
            self._audio[0].pause()

    def seek(self, t: float) -> None:
        self._pos = max(0.0, t)
        self._anchor_wall = self._wall()
        self._last_audio = (-1.0, 0.0)
        if self._audio is not None:
            self._audio[0].setPosition(int(self._pos * 1000))

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, volume))
        if self._audio is not None:
            self._audio[1].setVolume(self.volume)

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if self._audio is not None:
            self._audio[1].setMuted(muted)


# =============================================================================== decoding
@dataclass
class Frame:
    t: float
    result: RenderResult
    raw: np.ndarray | None = None  # float RGB at preview size (stills only)
    request_id: int = 0


def render_frame(raw: np.ndarray, edit: dict, full_size: tuple[int, int], crop_mode: bool,
                 want_original: bool, with_hist: bool, generation: int, stats: dict | None = None) -> RenderResult:
    """One frame through the shared pipeline (same functions as photos and export).

    ``raw`` may be uint8 (exact LUT linearisation) or float. ``stats`` carries whole-frame
    statistics between frames; newly computed ones are added to it.
    """
    ctx = pl.make_context(full_size, (raw.shape[1], raw.shape[0]), edit, apply_crop=not crop_mode, stats=stats)
    geom = pl.process_geometry(raw, edit, ctx)
    out = pl.to_uint8(pl.process_color(geom, edit, ctx))
    if stats is not None:
        for key, value in ctx.stats.items():
            stats.setdefault(key, value)
    image = to_qimage(out)
    original = to_qimage(pl.to_uint8(pl.render_original(geom))) if want_original else image
    return RenderResult(
        generation=generation, request_id=0, draft=True, image=image, original=original, overlay=QImage(),
        hist=histogram(out) if with_hist else None,
        full_size=geo.output_size(full_size[0], full_size[1], edit["geometry"], apply_crop=not crop_mode),
        scale=ctx.scale, crop_mode=crop_mode,
    )


class _DecodeThread(QThread):
    frame_ready = Signal(object)  # Frame (playback)
    still_ready = Signal(object)  # Frame (seek / step / scrub)
    play_ready = Signal(object)   # Frame: first frame of a playback start (preroll done)
    ended = Signal()
    failed = Signal(str)

    def __init__(self, path: Path, rotation: int, max_side: int, generation: int) -> None:
        super().__init__()
        self.path, self.rotation, self.max_side, self.generation = path, rotation, max_side, generation
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False
        self._command: tuple | None = None
        self._edit: dict | None = None
        self.crop_mode = False
        self.want_original = False
        self.clock = (0.0, time.monotonic(), 1.0, False)  # (media t, wall, rate, playing) published by the UI
        self.pending = 0   # frames emitted but not yet consumed by the UI
        self.dropped = 0
        self.full_size = (1, 1)
        self.duration = 0.0
        self.fps = 30.0
        self._stats: dict = {}
        self._stats_edit: dict | None = None

    # ---- called from the UI thread ----------------------------------------------------
    def set_edit(self, edit: dict) -> None:
        with self._lock:
            self._edit = copy.deepcopy(edit)

    def command(self, *cmd) -> None:
        with self._lock:
            self._command = cmd
        self._wake.set()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    def publish_clock(self, t: float, rate: float, playing: bool) -> None:
        self.clock = (t, time.monotonic(), rate, playing)
        if playing:
            self._wake.set()

    # ---- decode thread ----------------------------------------------------------------
    def _now(self) -> float:
        t, wall, rate, playing = self.clock
        return t + (time.monotonic() - wall) * rate if playing else t

    def _render(self, reader: VideoReader, frame, with_hist: bool) -> tuple[RenderResult, np.ndarray]:
        """Synchronous render (stills)."""
        raw = reader.to_array(frame, self.max_side)
        with self._lock:
            edit = self._edit
        result = render_frame(raw, edit, reader.full_size, self.crop_mode, self.want_original, with_hist,
                              self.generation)
        return result, raw.astype(np.float32) / np.float32(255.0)

    def _submit(self, executor, raw: np.ndarray, full_size, with_hist: bool, count: int):
        with self._lock:
            edit = self._edit
            if edit is not self._stats_edit or count % STATS_REFRESH_FRAMES == 0:
                self._stats, self._stats_edit = {}, edit
            stats = self._stats
        return executor.submit(render_frame, raw, edit, full_size, self.crop_mode, self.want_original,
                               with_hist, self.generation, stats)

    def run(self) -> None:  # noqa: C901 – one small state machine
        try:
            reader = VideoReader(self.path, self.rotation)
        except Exception as exc:  # noqa: BLE001 – unreadable video
            log.error("cannot open %s: %s", self.path, exc)
            self.failed.emit("viewer.unreadable")
            return
        self.full_size, self.duration, self.fps = reader.full_size, reader.duration, reader.fps
        playing = False
        count = 0
        catchup_from: float | None = None
        catchup_useless = False
        executor = ThreadPoolExecutor(max_workers=RENDER_WORKERS, thread_name_prefix="reefhue-render")
        inflight: collections.deque = collections.deque()  # (frame time, future), in decode order

        def drain() -> None:
            for _t, fut in inflight:
                fut.cancel()
            inflight.clear()

        try:
            while not self._stop:
                with self._lock:
                    cmd, self._command = self._command, None
                if cmd is not None:
                    drain()
                    kind = cmd[0]
                    if kind == "still":
                        _kind, t, request_id = cmd
                        frame = reader.frame_at(t)
                        if frame is not None and self._edit is not None:
                            result, raw = self._render(reader, frame, True)
                            self.still_ready.emit(Frame(reader.frame_time(frame), result, raw, request_id))
                        playing = False
                    elif kind == "play":
                        # Preroll: decode from the keyframe up to the start frame, show it, and only
                        # then let the clock run – audio and video start together.
                        _kind, t, request_id = cmd
                        frame = reader.frame_at(t)
                        if frame is None:
                            self.ended.emit()
                            continue
                        result, _raw = self._render(reader, frame, True)
                        self.play_ready.emit(Frame(reader.frame_time(frame), result, None, request_id))
                        playing = True
                        catchup_from = None
                        catchup_useless = False
                    elif kind == "pause":
                        playing = False
                if not playing or not self.clock[3] or self._edit is None:
                    drain()
                    self._wake.wait(0.05)
                    self._wake.clear()
                    continue
                # 1) hand finished renders to the UI, strictly in order
                while inflight and inflight[0][1].done():
                    ft, fut = inflight.popleft()
                    if ft < self._now() - reader.frame_duration:
                        self.dropped += 1  # rendered, but too late to show
                        continue
                    self.pending += 1
                    self.frame_ready.emit(Frame(ft, fut.result()))
                # 2) keep the render workers busy
                if len(inflight) >= RENDER_WORKERS or self.pending >= QUEUE_MAX:
                    time.sleep(0.001)
                    continue
                frame = reader.next_frame()
                if frame is None:
                    if not inflight:
                        playing = False
                        self.ended.emit()
                    else:
                        time.sleep(0.001)
                    continue
                ft = reader.frame_time(frame)
                now = self._now()
                if catchup_from is not None:
                    catchup_useless = ft <= catchup_from  # the seek landed no further ahead (long GOP)
                    catchup_from = None
                if ft < now - reader.frame_duration:  # already late: drop without converting/rendering
                    self.dropped += 1
                    if ft < now - CATCH_UP_S and not catchup_useless:
                        catchup_from = ft
                        reader.seek(now + CATCH_UP_LEAD_S)
                    continue
                count += 1
                raw = reader.to_array(frame, self.max_side)  # uint8, decode thread
                inflight.append((ft, self._submit(executor, raw, reader.full_size, count % HIST_EVERY == 0, count)))
        except Exception as exc:  # noqa: BLE001
            log.exception("video decode failed: %s", exc)
            self.failed.emit("viewer.unreadable")
        finally:
            drain()
            executor.shutdown(wait=True)
            reader.close()


# =============================================================================== player
class VideoPlayer(QObject):
    frame_shown = Signal(object)       # RenderResult (playback and stills)
    still_shown = Signal(float, object)  # paused frame: time, raw float array (for the preview engine)
    position_changed = Signal(float)
    state_changed = Signal(bool, float)  # playing, rate
    fps_measured = Signal(float)
    opened = Signal(float, float)      # duration, fps
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.clock = MediaClock(self)
        self._thread: _DecodeThread | None = None
        self._queue: collections.deque[Frame] = collections.deque()
        self._tick = QTimer(self, interval=TICK_MS)
        self._tick.timeout.connect(self._on_tick)
        self._reverse = QTimer(self, interval=REVERSE_STILL_MS)
        self._reverse.timeout.connect(self._on_reverse)
        self._generation = 0
        self._still_id = 0
        self._shown_times: collections.deque[float] = collections.deque(maxlen=90)
        self.path: Path | None = None
        self.duration = 0.0
        self.fps = 30.0
        self.position = 0.0
        self.segment: tuple[float, float] | None = None
        self.segment_only = False
        self.loop = False
        self._edit: dict | None = None
        self._last_still_t: float | None = None
        self._starting: tuple[int, float] | None = None  # (request id, rate) while the decoder prerolls
        self._play_id = 0

    # ---- lifecycle --------------------------------------------------------------------
    def open(self, path: Path, rotation: int, has_audio: bool, duration: float, fps: float, edit: dict,
             max_side: int, start: float = 0.0) -> None:
        self.close()
        self._generation += 1
        self.path = path
        self.duration = max(0.0, duration or 0.0)
        self.fps = fps or 30.0
        self.position = start
        self._edit = copy.deepcopy(edit)
        self.clock.open(path, has_audio)
        self.clock.seek(start)
        thread = _DecodeThread(path, rotation, max_side, self._generation)
        thread.set_edit(edit)
        thread.frame_ready.connect(self._on_frame)
        thread.still_ready.connect(self._on_still)
        thread.play_ready.connect(self._on_play_ready)
        thread.ended.connect(self._on_ended)
        thread.failed.connect(self.failed)
        self._thread = thread
        thread.start()
        self.opened.emit(self.duration, self.fps)
        self.show_still(start)

    def close(self) -> None:
        self._tick.stop()
        self._reverse.stop()
        if self._thread is not None:
            self._thread.stop()
            self._thread.wait(3000)
            self._thread.deleteLater()
            self._thread = None
        self.clock.close()
        self._queue.clear()
        self.path = None

    @property
    def is_open(self) -> bool:
        return self._thread is not None

    @property
    def playing(self) -> bool:
        return self.clock.playing or self._starting is not None

    @property
    def rate(self) -> float:
        """Current (or, while the decoder prerolls, requested) playback rate."""
        if self._starting is not None:
            return self._starting[1]
        return self.clock.rate if self.clock.playing else 0.0

    @property
    def dropped(self) -> int:
        return self._thread.dropped if self._thread else 0

    # ---- settings from the UI ---------------------------------------------------------
    def set_edit(self, edit: dict) -> None:
        self._edit = copy.deepcopy(edit)
        if self._thread is not None:
            self._thread.set_edit(edit)

    def set_view(self, crop_mode: bool, want_original: bool) -> None:
        if self._thread is not None:
            self._thread.crop_mode = crop_mode
            self._thread.want_original = want_original

    def set_segment(self, segment: tuple[float, float] | None) -> None:
        self.segment = segment

    def play_range(self) -> tuple[float, float]:
        if self.segment_only and self.segment is not None:
            return self.segment
        return 0.0, self.duration

    # ---- transport --------------------------------------------------------------------
    def play(self, rate: float = 1.0) -> None:
        if self._thread is None:
            return
        rate = max(-MAX_RATE, min(MAX_RATE, rate)) or 1.0
        start, end = self.play_range()
        t = self.position
        if rate > 0 and (t < start or t >= end - 1.0 / self.fps):
            t = start
        self.clock.pause()
        self.clock.seek(t)
        self.position = t
        self._queue.clear()
        self._thread.pending = 0
        if rate > 0:
            self._reverse.stop()
            self._tick.stop()
            self._play_id += 1
            self._starting = (self._play_id, rate)
            self._thread.command("play", t, self._play_id)  # the clock starts in _on_play_ready
            self._shown_times.clear()
        else:
            self._starting = None  # reverse needs no preroll: frames come as stills
            self._thread.command("pause")
            self.clock.play(rate)
            self._tick.start()
            self._reverse.start()
        self._publish()
        self.state_changed.emit(True, rate)

    def pause(self) -> None:
        was_playing = self.playing
        self._starting = None
        self.clock.pause()
        self._tick.stop()
        self._reverse.stop()
        if self._thread is not None:
            self._thread.command("pause")
        self._publish()
        self._consumed(len(self._queue))
        self._queue.clear()
        if was_playing:
            self.position = snap_to_frame(self.clock.now(), self.fps)
            self.state_changed.emit(False, 1.0)
            self.show_still(self.position)

    def toggle(self) -> None:
        if self.playing:
            self.pause()
        else:
            self.play(1.0)

    def faster(self, direction: int) -> None:
        """J / L: start in that direction, or double the speed when repeated."""
        rate = self.rate
        if rate and (rate > 0) == (direction > 0):
            new = rate * 2
        else:
            new = float(direction)
        self.play(new)

    def seek(self, t: float, final: bool = True) -> None:
        t = max(0.0, min(self.duration, t))
        if self.playing and final:
            self.position = t
            self.play(self.clock.rate if self.clock.rate > 0 else (self._starting or (0, 1.0))[1])
            return
        if self.playing:
            self.pause()
        self.position = snap_to_frame(t, self.fps)
        self.clock.seek(self.position)
        self.position_changed.emit(self.position)
        self.show_still(self.position)

    def step(self, frames: int) -> None:
        if self.playing:
            self.pause()
        self.seek(self.position + frames / self.fps + 1e-4)

    def jump(self, seconds: float) -> None:
        self.seek(self.position + seconds)

    def show_still(self, t: float) -> None:
        if self._thread is None:
            return
        self._still_id += 1
        self._thread.command("still", t, self._still_id)

    def refresh_still(self) -> None:
        """Re-render the paused frame (e.g. after the crop mode changed)."""
        if not self.playing:
            self.show_still(self.position)

    # ---- callbacks ----------------------------------------------------------------------
    def _publish(self) -> None:
        if self._thread is not None:
            self._thread.publish_clock(self.clock.now(), self.clock.rate, self.clock.playing)

    def _on_play_ready(self, frame: Frame) -> None:
        if self._starting is None or frame.request_id != self._starting[0]:
            return  # paused or restarted meanwhile
        _id, rate = self._starting
        self._starting = None
        self.position = frame.t
        self.frame_shown.emit(frame.result)
        self.clock.seek(frame.t)
        self.clock.play(rate)
        self._publish()
        self._tick.start()

    def _consumed(self, n: int = 1) -> None:
        """Frames leave the pipeline only when shown or discarded (decoder back-pressure)."""
        if self._thread is not None:
            self._thread.pending = max(0, self._thread.pending - n)

    def _on_frame(self, frame: Frame) -> None:
        if self.playing and frame.result.generation == self._generation:
            self._queue.append(frame)
        else:
            self._consumed()

    def _on_still(self, frame: Frame) -> None:
        if frame.request_id != self._still_id or frame.result.generation != self._generation:
            return  # a newer still request is on its way
        if self.playing and self.clock.rate > 0:
            return
        self.frame_shown.emit(frame.result)
        if not self.playing:
            self.position = frame.t
            self.position_changed.emit(frame.t)
            self.still_shown.emit(frame.t, frame.raw)

    def _on_ended(self) -> None:
        # While playing, the clock-driven tick ends playback once the last queued frame
        # has been shown; the decoder reaching the end of the file is not the end yet.
        if not self.playing:
            self.pause()

    def _on_tick(self) -> None:
        t = self.clock.now()
        self._publish()
        start, end = self.play_range()
        if self.clock.rate > 0 and t >= end - 0.5 / self.fps:
            if self.loop:
                self.position = start
                self.play(self.clock.rate)
            else:
                self.clock.seek(max(start, end - 1.0 / self.fps))
                self.pause()
            return
        if self.clock.rate < 0 and t <= start:
            self.clock.seek(start)
            self.pause()
            return
        frame = None
        half = 0.5 / self.fps
        while self._queue and self._queue[0].t <= t + half:
            if frame is not None and self._thread is not None:
                self._thread.dropped += 1  # shown too late: skipped in favour of a newer one
            frame = self._queue.popleft()
            self._consumed()
        if frame is not None:
            self.position = frame.t
            self.frame_shown.emit(frame.result)
            now = time.monotonic()
            self._shown_times.append(now)
            if len(self._shown_times) >= 10:
                span = self._shown_times[-1] - self._shown_times[0]
                if span > 0:
                    self.fps_measured.emit((len(self._shown_times) - 1) / span)
        elif self.clock.rate < 0:
            self.position = t
        self.position_changed.emit(self.position)

    def _on_reverse(self) -> None:
        t = self.clock.now()
        if t != self._last_still_t:
            self._last_still_t = t
            self.show_still(max(0.0, t))

    def measured_fps(self) -> float:
        if len(self._shown_times) < 2:
            return 0.0
        span = self._shown_times[-1] - self._shown_times[0]
        return (len(self._shown_times) - 1) / span if span > 0 else 0.0
