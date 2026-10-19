"""Preview canvas (SPEC §6): fit/zoom/pan, original/split view, clipping + crop overlays, eyedropper."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from reefhue.pipeline import geometry as geo
from reefhue.ui.preview_engine import RegionResult, RenderResult

ZOOM_MIN_FACTOR = 0.5   # relative to fit
ZOOM_MAX = 8.0          # 800 % (logical px per image px, see zoom_100)
HANDLE_PX = 10
REGION_DELAY_MS = 150
FIT_MARGIN = 0.98


class PreviewCanvas(QWidget):
    zoom_changed = Signal()
    region_needed = Signal(tuple)       # (x, y, w, h) in full-resolution output pixels
    region_cleared = Signal()
    eyedropper_picked = Signal(float, float)  # full-resolution coordinates
    crop_changed = Signal(dict, bool)   # rect, final
    rotate_requested = Signal(int)      # +1 clockwise, -1 counter-clockwise
    hold_original_changed = Signal(bool)
    space_tapped = Signal()             # Space pressed and released without panning (video: play/pause)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(200, 150)
        self.result: RenderResult | None = None
        self.region: RegionResult | None = None
        self.zoom: float | None = None  # None = fit
        self.center = QPointF(0, 0)
        self.show_original = False
        self.hold_original = False
        self.split = False
        self.split_pos = 0.5
        self.show_clipping = False
        self.crop_mode = False
        self.crop: dict = {"x": 0, "y": 0, "w": 1, "h": 1, "aspect": "free"}
        self.crop_ratio: float | None = None
        self.eyedropper = False
        self.show_info = False
        self.info_lines: list[str] = []
        self._space = False
        self._panned = False
        self._drag: dict | None = None
        self._region_timer = QTimer(self, singleShot=True, interval=REGION_DELAY_MS)
        self._region_timer.timeout.connect(self._check_region)
        self._accent = QColor("#2ea6e3")

    # ------------------------------------------------------------------ state
    def set_accent(self, color: str) -> None:
        self._accent = QColor(color)
        self.update()

    def set_result(self, result: RenderResult | None) -> None:
        size_changed = (
            self.result is None or result is None or self.result.full_size != result.full_size
            or self.result.crop_mode != result.crop_mode
        )
        self.result = result
        if result is not None:
            self.region = None  # stale for the new edit; re-requested after a final render
            if not result.draft:
                self._region_timer.start()
        if size_changed and self.zoom is not None and result is not None:
            self._clamp_center()
        self.update()

    def set_region(self, region: RegionResult) -> None:
        self.region = region
        self.update()

    def clear(self) -> None:
        self.result = None
        self.region = None
        self.zoom = None
        self.update()
        self.zoom_changed.emit()

    @property
    def full_size(self) -> tuple[int, int]:
        return self.result.full_size if self.result else (1, 1)

    def fit_zoom(self) -> float:
        w, h = self.full_size
        return min(self.width() / w, self.height() / h) * FIT_MARGIN

    def zoom_100(self) -> float:
        return 1.0 / self.devicePixelRatioF()  # one image pixel per physical screen pixel

    def effective_zoom(self) -> float:
        return self.fit_zoom() if self.zoom is None else self.zoom

    def zoom_percent(self) -> int:
        return int(round(self.effective_zoom() / self.zoom_100() * 100))

    def image_rect(self) -> QRectF:
        w, h = self.full_size
        z = self.effective_zoom()
        cx, cy = (w / 2, h / 2) if self.zoom is None else (self.center.x(), self.center.y())
        return QRectF(self.width() / 2 - cx * z, self.height() / 2 - cy * z, w * z, h * z)

    def widget_to_full(self, pos: QPointF) -> QPointF:
        rect, z = self.image_rect(), self.effective_zoom()
        return QPointF((pos.x() - rect.x()) / z, (pos.y() - rect.y()) / z)

    # ------------------------------------------------------------------ zoom / pan
    def fit(self) -> None:
        self.zoom = None
        self._after_view_change()

    def actual_size(self) -> None:
        self.set_zoom(self.zoom_100(), QPointF(self.width() / 2, self.height() / 2))

    def set_zoom(self, zoom: float, anchor: QPointF) -> None:
        if self.result is None:
            return
        zoom = max(self.fit_zoom() * ZOOM_MIN_FACTOR, min(ZOOM_MAX, zoom))
        if self.zoom is None and abs(zoom - self.fit_zoom()) < 1e-6:
            return
        before = self.widget_to_full(anchor)
        self.zoom = zoom
        # keep the image point under the anchor where it was
        self.center = QPointF(before.x() - (anchor.x() - self.width() / 2) / zoom,
                              before.y() - (anchor.y() - self.height() / 2) / zoom)
        if zoom <= self.fit_zoom() + 1e-9:
            self.zoom = None
        self._clamp_center()
        self._after_view_change()

    def _clamp_center(self) -> None:
        w, h = self.full_size
        self.center = QPointF(min(w, max(0.0, self.center.x())), min(h, max(0.0, self.center.y())))

    def _after_view_change(self) -> None:
        if self.zoom is None:
            w, h = self.full_size
            self.center = QPointF(w / 2, h / 2)
        self.update()
        self.zoom_changed.emit()
        self._region_timer.start()

    def needs_region(self) -> bool:
        if self.result is None:
            return False
        return self.effective_zoom() * self.devicePixelRatioF() > self.result.scale * 1.05

    def visible_full_rect(self) -> tuple[int, int, int, int]:
        tl = self.widget_to_full(QPointF(0, 0))
        br = self.widget_to_full(QPointF(self.width(), self.height()))
        w, h = self.full_size
        x0, y0 = max(0, int(math.floor(tl.x()))), max(0, int(math.floor(tl.y())))
        x1, y1 = min(w, int(math.ceil(br.x()))), min(h, int(math.ceil(br.y())))
        return x0, y0, max(0, x1 - x0), max(0, y1 - y0)

    def _check_region(self) -> None:
        if self.needs_region():
            rect = self.visible_full_rect()
            if rect[2] > 0 and rect[3] > 0:
                self.region_needed.emit(rect)
        elif self.region is not None:
            self.region = None
            self.region_cleared.emit()
            self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._region_timer.start()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.result is None:
            return
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if not delta:
            return
        factor = 2 ** (delta / 480.0)
        self.set_zoom(self.effective_zoom() * factor, event.position())
        event.accept()

    def event(self, event) -> bool:
        if event.type() == event.Type.NativeGesture and event.gestureType() == Qt.ZoomNativeGesture:
            self.set_zoom(self.effective_zoom() * (1.0 + event.value()), event.position())
            return True
        return super().event(event)

    # ------------------------------------------------------------------ keyboard
    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key_Space:
            if not event.isAutoRepeat():
                self._space = True
                self._panned = False
                self.setCursor(Qt.OpenHandCursor)
        elif key == Qt.Key_Backslash and not event.isAutoRepeat():
            self.hold_original = True
            self.hold_original_changed.emit(True)
            self.update()
        elif key == Qt.Key_F and not event.modifiers() & ~Qt.KeypadModifier:
            self.fit()
        elif key == Qt.Key_1 and not event.modifiers() & ~Qt.KeypadModifier:
            self.actual_size()
        elif key == Qt.Key_R and not event.modifiers() & ~(Qt.ShiftModifier | Qt.KeypadModifier):
            self.rotate_requested.emit(-1 if event.modifiers() & Qt.ShiftModifier else 1)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        if event.isAutoRepeat():
            return
        if event.key() == Qt.Key_Space:
            self._space = False
            if not self._panned:
                self.space_tapped.emit()
            self._update_cursor(self.mapFromGlobal(QCursor.pos()))
        elif event.key() == Qt.Key_Backslash:
            self.hold_original = False
            self.hold_original_changed.emit(False)
            self.update()
        else:
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self._space = False
        if self.hold_original:
            self.hold_original = False
            self.hold_original_changed.emit(False)
            self.update()
        super().focusOutEvent(event)

    # ------------------------------------------------------------------ mouse
    def _crop_widget_rect(self) -> QRectF:
        rect, (w, h) = self.image_rect(), self.full_size
        z = self.effective_zoom()
        c = self.crop
        return QRectF(rect.x() + c["x"] * w * z, rect.y() + c["y"] * h * z, c["w"] * w * z, c["h"] * h * z)

    def crop_handle_at(self, pos: QPointF) -> str | None:
        r = self._crop_widget_rect()
        near_l, near_r = abs(pos.x() - r.left()) <= HANDLE_PX, abs(pos.x() - r.right()) <= HANDLE_PX
        near_t, near_b = abs(pos.y() - r.top()) <= HANDLE_PX, abs(pos.y() - r.bottom()) <= HANDLE_PX
        inside_x = r.left() - HANDLE_PX <= pos.x() <= r.right() + HANDLE_PX
        inside_y = r.top() - HANDLE_PX <= pos.y() <= r.bottom() + HANDLE_PX
        if not (inside_x and inside_y):
            return None
        vertical = "n" if near_t else "s" if near_b else ""
        horizontal = "w" if near_l else "e" if near_r else ""
        if vertical or horizontal:
            return vertical + horizontal
        return "move" if r.contains(pos) else None

    def _split_x(self) -> float:
        return self.width() * self.split_pos

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.setFocus()
        pos = event.position()
        if self.result is None:
            return
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and self._space):
            self._drag = {"kind": "pan", "start": pos, "center": QPointF(self.center)}
            self._panned = True
            if self.zoom is None:
                self.zoom = self.fit_zoom()
                w, h = self.full_size
                self._drag["center"] = QPointF(w / 2, h / 2)
            self.setCursor(Qt.ClosedHandCursor)
            return
        if event.button() != Qt.LeftButton:
            return
        if self.eyedropper:
            p = self.widget_to_full(pos)
            w, h = self.full_size
            if 0 <= p.x() < w and 0 <= p.y() < h:
                self.eyedropper_picked.emit(p.x(), p.y())
            return
        if self.crop_mode:
            handle = self.crop_handle_at(pos)
            if handle:
                self._drag = {"kind": "crop", "handle": handle, "start": pos, "rect": dict(self.crop)}
            return
        if self.split and not self.show_original and abs(pos.x() - self._split_x()) <= HANDLE_PX:
            self._drag = {"kind": "split"}

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        drag = self._drag
        if drag is None:
            self._update_cursor(pos)
            return
        if drag["kind"] == "pan":
            z = self.effective_zoom()
            delta = pos - drag["start"]
            self.center = QPointF(drag["center"].x() - delta.x() / z, drag["center"].y() - delta.y() / z)
            self._clamp_center()
            self._after_view_change()
        elif drag["kind"] == "crop":
            w, h = self.full_size
            z = self.effective_zoom()
            delta = pos - drag["start"]
            rect = geo.drag_crop(drag["rect"], drag["handle"], delta.x() / (w * z), delta.y() / (h * z),
                                 self.crop_ratio, w, h)
            self.crop = rect
            self.crop_changed.emit(rect, False)
            self.update()
        elif drag["kind"] == "split":
            self.split_pos = min(0.98, max(0.02, pos.x() / max(1, self.width())))
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        drag, self._drag = self._drag, None
        if drag and drag["kind"] == "crop":
            self.crop_changed.emit(dict(self.crop), True)
        self._update_cursor(event.position())

    def _update_cursor(self, pos: QPointF) -> None:
        if self._space:
            self.setCursor(Qt.OpenHandCursor)
        elif self.eyedropper:
            self.setCursor(Qt.CrossCursor)
        elif self.crop_mode and self.result is not None:
            handle = self.crop_handle_at(pos)
            shapes = {"n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor, "e": Qt.SizeHorCursor, "w": Qt.SizeHorCursor,
                      "nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor, "ne": Qt.SizeBDiagCursor,
                      "sw": Qt.SizeBDiagCursor, "move": Qt.SizeAllCursor}
            self.setCursor(shapes.get(handle or "", Qt.ArrowCursor))
        elif self.split and not self.show_original and abs(pos.x() - self._split_x()) <= HANDLE_PX:
            self.setCursor(Qt.SplitHCursor)
        else:
            self.unsetCursor()

    # ------------------------------------------------------------------ painting
    def paintEvent(self, event) -> None:  # noqa: N802
        if self.result is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = self.image_rect()
        original_only = self.show_original or self.hold_original
        main = self.result.original if original_only else self.result.image
        painter.drawImage(rect, main)
        z = self.effective_zoom()
        region_rect = None
        if self.region is not None:
            x, y, w, h = self.region.rect
            region_rect = QRectF(rect.x() + x * z, rect.y() + y * z, w * z, h * z)
            region_image = self.region.original if original_only else self.region.image
            if region_image is not None:
                painter.drawImage(region_rect, region_image)
        if self.show_clipping and not original_only:
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.drawImage(rect, self.result.overlay)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.split and not original_only:
            sx = self._split_x()
            painter.save()
            painter.setClipRect(QRectF(0, 0, sx, self.height()))
            painter.drawImage(rect, self.result.original)
            if region_rect is not None and self.region.original is not None:
                painter.drawImage(region_rect, self.region.original)  # full-resolution original at 100 %
            painter.restore()
            painter.setPen(QPen(QColor(255, 255, 255, 230), 2))
            painter.drawLine(QPointF(sx, 0), QPointF(sx, self.height()))
            painter.setBrush(QColor(255, 255, 255, 230))
            painter.drawEllipse(QPointF(sx, self.height() / 2), 7, 7)
        if self.crop_mode:
            self._paint_crop(painter)
        if self.show_info and self.info_lines:
            self._paint_info(painter)
        painter.end()

    def _paint_info(self, painter: QPainter) -> None:
        metrics = painter.fontMetrics()
        line_h = metrics.height() + 2
        width = max(metrics.horizontalAdvance(line) for line in self.info_lines) + 24
        box = QRectF(12, 12, width, line_h * len(self.info_lines) + 16)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 165))
        painter.drawRoundedRect(box, 8, 8)
        painter.setPen(QColor(255, 255, 255))
        for i, line in enumerate(self.info_lines):
            painter.drawText(QPointF(box.left() + 12, box.top() + 8 + metrics.ascent() + i * line_h), line)

    def _paint_crop(self, painter: QPainter) -> None:
        r = self._crop_widget_rect()
        outside = QPainterPath()
        outside.addRect(self.image_rect())
        inner = QPainterPath()
        inner.addRect(r)
        painter.fillPath(outside.subtracted(inner), QColor(0, 0, 0, 140))
        painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
        for i in (1, 2):  # rule-of-thirds grid
            x = r.left() + r.width() * i / 3
            y = r.top() + r.height() * i / 3
            painter.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        painter.setPen(QPen(self._accent, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(r)
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(QPen(self._accent, 1))
        for px in (r.left(), r.center().x(), r.right()):
            for py in (r.top(), r.center().y(), r.bottom()):
                if px == r.center().x() and py == r.center().y():
                    continue
                painter.drawRect(QRectF(px - 4, py - 4, 8, 8))
