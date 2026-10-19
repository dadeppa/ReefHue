"""RGB + luminance histogram (SPEC §6, §7)."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

COLORS = {"r": QColor(235, 70, 70, 110), "g": QColor(70, 200, 90, 110), "b": QColor(70, 120, 245, 110)}


class HistogramWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hist: dict | None = None
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._grid = QColor(128, 128, 128, 60)
        self._lum = QColor(200, 200, 200, 140)
        self._dark = True

    def set_colors(self, grid: str, luminance: str, dark: bool = True) -> None:
        self._grid = QColor(grid)
        self._lum = QColor(luminance)
        self._lum.setAlpha(150)
        self._dark = dark
        self.update()

    def set_histogram(self, hist: dict | None) -> None:
        self.hist = hist
        self.update()

    def _path(self, values: np.ndarray, rect: QRectF) -> QPainterPath:
        # square-root scale keeps small populations visible
        v = np.sqrt(np.clip(values, 0, 1))
        path = QPainterPath(QPointF(rect.left(), rect.bottom()))
        n = len(v)
        for i, value in enumerate(v):
            path.lineTo(rect.left() + rect.width() * i / (n - 1), rect.bottom() - rect.height() * float(value))
        path.lineTo(rect.right(), rect.bottom())
        path.closeSubpath()
        return path

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(self._grid, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, 6, 6)
        for i in (1, 2, 3):
            x = rect.left() + rect.width() * i / 4
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        if not self.hist:
            painter.end()
            return
        inner = rect.adjusted(2, 4, -2, -2)
        painter.setPen(Qt.NoPen)
        # additive colours on dark backgrounds, multiplied on light ones (Plus would saturate to white)
        painter.setCompositionMode(QPainter.CompositionMode_Plus if self._dark else QPainter.CompositionMode_Multiply)
        for key, color in COLORS.items():
            painter.setBrush(color)
            painter.drawPath(self._path(self.hist[key], inner))
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(self._lum, 1.2))
        painter.drawPath(self._path(self.hist["l"], inner))
        painter.end()
