"""Interactive tone-curve editor (SPEC §7): add/move/delete points, rules from pipeline.curves."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from reefhue.pipeline import curves as C

POINT_RADIUS = 5
HIT_PX = 9
CHANNEL_COLORS = {"master": None, "r": QColor(235, 80, 80), "g": QColor(70, 190, 90), "b": QColor(80, 130, 245)}


class CurvesWidget(QWidget):
    points_changed = Signal(list)  # live
    committed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.points: list[list[float]] = [list(p) for p in C.IDENTITY]
        self.channel = "master"
        self._drag: int | None = None
        self._line = QColor(220, 220, 220)
        self._grid = QColor(128, 128, 128, 60)
        self.setMinimumSize(160, 160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

    def set_colors(self, line: str, grid: str) -> None:
        self._line, self._grid = QColor(line), QColor(grid)
        self.update()

    def set_curve(self, points, channel: str) -> None:
        if self._drag is None:
            self.points = C.normalize_points(points)
        self.channel = channel
        self.update()

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        return w

    def _area(self) -> QRectF:
        side = min(self.width(), self.height()) - 2 * POINT_RADIUS - 2
        return QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)

    def _to_widget(self, x: float, y: float) -> QPointF:
        a = self._area()
        return QPointF(a.left() + x * a.width(), a.bottom() - y * a.height())

    def _to_curve(self, pos: QPointF) -> tuple[float, float]:
        a = self._area()
        return (pos.x() - a.left()) / a.width(), (a.bottom() - pos.y()) / a.height()

    def point_at(self, pos: QPointF) -> int | None:
        for i, (x, y) in enumerate(self.points):
            p = self._to_widget(x, y)
            if abs(p.x() - pos.x()) <= HIT_PX and abs(p.y() - pos.y()) <= HIT_PX:
                return i
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        index = self.point_at(pos)
        if event.button() == Qt.RightButton:
            if index is not None:
                self._delete(index)
            return
        if event.button() != Qt.LeftButton:
            return
        if index is None:
            x, y = self._to_curve(pos)
            if not (0 <= x <= 1):
                return
            self.points, index = C.add_point(self.points, x, y)
            if index is None:
                return
            self.points_changed.emit([list(p) for p in self.points])
        self._drag = index
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is None:
            self.setCursor(Qt.PointingHandCursor if self.point_at(event.position()) is not None else Qt.CrossCursor)
            return
        x, y = self._to_curve(event.position())
        self.points = C.move_point(self.points, self._drag, x, y)
        self.points_changed.emit([list(p) for p in self.points])
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            self._drag = None
            self.committed.emit()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        index = self.point_at(event.position())
        if index is not None:
            self._delete(index)

    def _delete(self, index: int) -> None:
        new = C.delete_point(self.points, index)
        if new != self.points:
            self.points = new
            self._drag = None
            self.points_changed.emit([list(p) for p in self.points])
            self.committed.emit()
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        a = self._area()
        painter.setPen(QPen(self._grid, 1))
        painter.drawRect(a)
        for i in (1, 2, 3):
            painter.drawLine(QPointF(a.left() + a.width() * i / 4, a.top()), QPointF(a.left() + a.width() * i / 4, a.bottom()))
            painter.drawLine(QPointF(a.left(), a.top() + a.height() * i / 4), QPointF(a.right(), a.top() + a.height() * i / 4))
        painter.drawLine(a.bottomLeft(), a.topRight())
        color = CHANNEL_COLORS.get(self.channel) or self._line
        xs = np.linspace(0, 1, 129)
        ys = C.evaluate(self.points, xs)
        path = QPainterPath(self._to_widget(0, float(ys[0])))
        for x, y in zip(xs[1:], ys[1:]):
            path.lineTo(self._to_widget(float(x), float(y)))
        painter.setPen(QPen(color, 2))
        painter.drawPath(path)
        painter.setBrush(color)
        for x, y in self.points:
            painter.drawEllipse(self._to_widget(x, y), POINT_RADIUS, POINT_RADIUS)
        painter.end()
