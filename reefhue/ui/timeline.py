"""Video transport + timeline (SPEC §6): play/pause, snapshot, timecode, segment handles, volume."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from reefhue.core.timecode import clamp_segment, format_timecode, frame_number
from reefhue.i18n import tr
from reefhue.ui.context import AppContext
from reefhue.ui.icons import icon

HANDLE_HIT = 8
TRACK_H = 10


class TimelineBar(QWidget):
    """Playhead + Start/End handles of the kept segment; outside the segment is dimmed."""

    seek_requested = Signal(float, bool)          # time, final
    segment_changed = Signal(float, float, bool)  # start, end, final

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.duration = 0.0
        self.fps = 30.0
        self.position = 0.0
        self.start = 0.0
        self.end = 0.0
        self._drag: str | None = None
        self._accent = QColor("#2ea6e3")
        self._track = QColor(128, 128, 128, 90)
        self._dim = QColor(0, 0, 0, 110)
        self.setMinimumHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

    def set_colors(self, accent: str, track: str) -> None:
        self._accent, self._track = QColor(accent), QColor(track)
        self.update()

    def set_duration(self, duration: float, fps: float) -> None:
        self.duration, self.fps = max(0.0, duration), fps or 30.0
        self.update()

    def set_position(self, t: float) -> None:
        self.position = t
        self.update()

    def set_segment(self, segment: dict | None) -> None:
        if segment:
            self.start, self.end = float(segment["start_s"]), float(segment["end_s"])
        else:
            self.start, self.end = 0.0, self.duration
        self.update()

    # ---- geometry -------------------------------------------------------------------------
    def _track_rect(self) -> QRectF:
        return QRectF(8, (self.height() - TRACK_H) / 2, self.width() - 16, TRACK_H)

    def x_for(self, t: float) -> float:
        r = self._track_rect()
        return r.left() + (t / self.duration if self.duration else 0) * r.width()

    def t_for(self, x: float) -> float:
        r = self._track_rect()
        return max(0.0, min(self.duration, (x - r.left()) / max(1.0, r.width()) * self.duration))

    def handle_at(self, x: float) -> str | None:
        if abs(x - self.x_for(self.start)) <= HANDLE_HIT:
            return "start"
        if abs(x - self.x_for(self.end)) <= HANDLE_HIT:
            return "end"
        return None

    # ---- mouse --------------------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton or not self.duration:
            return
        x = event.position().x()
        self._drag = self.handle_at(x) or "seek"
        self._apply_drag(x, final=False)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        x = event.position().x()
        if self._drag is None:
            self.setCursor(Qt.SizeHorCursor if self.handle_at(x) else Qt.PointingHandCursor)
            return
        self._apply_drag(x, final=False)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            self._apply_drag(event.position().x(), final=True)
            self._drag = None

    def _apply_drag(self, x: float, final: bool) -> None:
        t = self.t_for(x)
        if self._drag == "seek":
            self.position = t
            self.seek_requested.emit(t, final)
        elif self._drag in ("start", "end"):
            start, end = (t, self.end) if self._drag == "start" else (self.start, t)
            self.start, self.end = clamp_segment(start, end, self.duration, self.fps, self._drag)
            self.segment_changed.emit(self.start, self.end, final)
        self.update()

    # ---- painting -------------------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        r = self._track_rect()
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._track)
        painter.drawRoundedRect(r, 4, 4)
        if not self.duration:
            painter.end()
            return
        xs, xe = self.x_for(self.start), self.x_for(self.end)
        kept = QColor(self._accent)
        kept.setAlpha(110)
        painter.setBrush(kept)
        painter.drawRect(QRectF(xs, r.top(), xe - xs, r.height()))
        painter.setBrush(self._dim)  # dimmed outside the segment
        painter.drawRect(QRectF(r.left(), r.top(), xs - r.left(), r.height()))
        painter.drawRect(QRectF(xe, r.top(), r.right() - xe, r.height()))
        painter.setBrush(self._accent)
        for x in (xs, xe):  # segment handles
            painter.drawRoundedRect(QRectF(x - 3, r.top() - 7, 6, r.height() + 14), 2, 2)
        px = self.x_for(self.position)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawLine(QPointF(px, 2), QPointF(px, self.height() - 2))
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(QPen(self._accent, 1.5))
        painter.drawEllipse(QPointF(px, self.height() / 2), 5, 5)
        painter.end()


class VideoTransport(QWidget):
    play_toggled = Signal()
    snapshot_requested = Signal()
    volume_changed = Signal(float)
    mute_toggled = Signal(bool)
    segment_only_toggled = Signal(bool)
    loop_toggled = Signal(bool)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.fps = 30.0
        self.timeline = TimelineBar()
        self.play_button = QToolButton()
        self.play_button.setIconSize(QSize(20, 20))
        self.play_button.clicked.connect(self.play_toggled)
        self.snapshot_button = QToolButton()
        self.snapshot_button.setIconSize(QSize(20, 20))
        self.snapshot_button.clicked.connect(self.snapshot_requested)
        self.timecode = QLabel()
        self.segment_only = QCheckBox()
        self.segment_only.toggled.connect(self.segment_only_toggled)
        self.loop = QCheckBox()
        self.loop.toggled.connect(self.loop_toggled)
        self.mute_button = QToolButton(checkable=True)
        self.mute_button.setIconSize(QSize(18, 18))
        self.mute_button.toggled.connect(self._on_mute)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(70)
        self.volume.valueChanged.connect(lambda v: self.volume_changed.emit(v / 100.0))
        self.fps_label = QLabel()
        self.fps_label.setObjectName("MutedLabel")
        self.fps_label.setMinimumWidth(56)
        self._playing = False

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for widget in (self.play_button, self.snapshot_button, self.timecode):
            row.addWidget(widget)
        row.addStretch(1)
        for widget in (self.segment_only, self.loop):
            row.addWidget(widget)
        row.addSpacing(10)
        row.addWidget(self.mute_button)
        row.addWidget(self.volume)
        row.addWidget(self.fps_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.timeline)
        layout.addLayout(row)

        ctx.language_changed.connect(self.retranslate_ui)
        ctx.theme_changed.connect(self.refresh_icons)
        self.refresh_icons()
        self.retranslate_ui()

    def refresh_icons(self, *_args) -> None:
        color = self.ctx.color("text")
        self.play_button.setIcon(icon("pause" if self._playing else "play", color))
        self.snapshot_button.setIcon(icon("camera", color))
        self.mute_button.setIcon(icon("volume-x" if self.mute_button.isChecked() else "volume", color))
        self.timeline.set_colors(self.ctx.color("accent"), self.ctx.color("border_strong"))

    def retranslate_ui(self, *_args) -> None:
        self.play_button.setToolTip(tr("video.play_tip"))
        self.snapshot_button.setToolTip(tr("video.snapshot_tip"))
        self.segment_only.setText(tr("video.segment_only"))
        self.loop.setText(tr("video.loop"))
        self.segment_only.setToolTip(tr("video.segment_only_tip"))
        self.loop.setToolTip(tr("video.loop_tip"))
        self.mute_button.setToolTip(tr("video.mute_tip"))
        sample = tr("video.timecode", tc="00:00:00:00", frame=99999)
        self.timecode.setMinimumWidth(self.timecode.fontMetrics().horizontalAdvance(sample) + 8)
        self.volume.setToolTip(tr("video.volume_tip"))
        self.timeline.setToolTip(tr("video.timeline_tip"))

    def _on_mute(self, on: bool) -> None:
        self.refresh_icons()
        self.mute_toggled.emit(on)

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        self.refresh_icons()

    def set_position(self, t: float) -> None:
        self.timeline.set_position(t)
        self.timecode.setText(tr("video.timecode", tc=format_timecode(t, self.fps), frame=frame_number(t, self.fps)))

    def set_fps(self, fps: float | None) -> None:
        self.fps_label.setText(tr("video.fps", fps=f"{fps:.1f}") if fps else "")

    def change_volume(self, delta: float) -> None:
        self.volume.setValue(max(0, min(100, self.volume.value() + int(round(delta * 100)))))
