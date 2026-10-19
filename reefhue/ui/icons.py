"""SVG icon loader. ``currentColor`` in the SVG is replaced by a theme colour."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from reefhue.core import paths

_SIZES = (16, 20, 24, 32, 48, 64, 96, 128)


def icon_path(name: str):
    return paths.resources_dir() / "icons" / f"{name}.svg"


@lru_cache(maxsize=256)
def icon(name: str, color: str) -> QIcon:
    """Return a QIcon for ``resources/icons/<name>.svg`` drawn in ``color``."""
    path = icon_path(name)
    if not path.is_file():
        return QIcon()
    svg = path.read_text(encoding="utf-8").replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    result = QIcon()
    for size in _SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter)
        painter.end()
        result.addPixmap(pixmap)
    return result


def pixmap(name: str, color: str, size: int, device_pixel_ratio: float = 1.0) -> QPixmap:
    # Qt 6 applies the device pixel ratio itself: request the *logical* size.
    return icon(name, color).pixmap(QSize(size, size), device_pixel_ratio)
