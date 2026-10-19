"""Background thumbnails: files in the app cache, decoded QImages kept in a small LRU."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap

from reefhue.core.project import FileItem
from reefhue.io import thumbs
from reefhue.ui.context import AppContext
from reefhue.ui.workers import run_task

MEMORY_LIMIT = 1500


class ThumbnailService(QObject):
    thumbnail_ready = Signal(str)  # rel_path

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(3)
        self._cache: OrderedDict[tuple, tuple[QPixmap, QIcon] | None] = OrderedDict()
        self._pending: set[tuple] = set()
        self._folder: Path | None = None
        self._generation = 0

    def set_folder(self, folder: Path | None) -> None:
        self._folder = folder
        self._generation += 1
        self._pending.clear()
        self._pool.clear()  # drop queued (not yet running) jobs of the previous folder

    def clear_memory(self) -> None:
        self._cache.clear()

    @staticmethod
    def _key(item: FileItem) -> tuple | None:
        if item.scanned is None:
            return None
        return item.rel_path, item.scanned.size, item.scanned.mtime

    def get(self, item: FileItem) -> QPixmap | None:
        """Cached pixmap or ``None`` (then a background job is started)."""
        entry = self._lookup(item)
        return entry[0] if entry else None

    def get_icon(self, item: FileItem) -> QIcon | None:
        """Same as :meth:`get` but as a QIcon (scales with aspect ratio, caches sizes)."""
        entry = self._lookup(item)
        return entry[1] if entry else None

    def _lookup(self, item: FileItem) -> tuple[QPixmap, QIcon] | None:
        key = self._key(item)
        if key is None or self._folder is None:
            return None
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if key in self._pending or item.kind not in ("photo", "video"):
            return None
        ffmpeg = None
        status = self.ctx.ffmpeg_status
        if item.kind == "video":
            if status is None or not status.ffmpeg.ok:
                return None
            ffmpeg = status.ffmpeg.path
        self._pending.add(key)
        abs_path = self._folder / item.rel_path
        kind, size, mtime = item.kind, item.scanned.size, item.scanned.mtime
        duration = item.entry["media"].get("duration_s")
        generation = self._generation

        def work():
            path = thumbs.ensure_thumbnail(abs_path, kind, size, mtime, ffmpeg, duration)
            return QImage(str(path)) if path is not None else None  # QImage is thread-safe

        def done(image) -> None:
            if generation != self._generation:
                return
            self._pending.discard(key)
            if image is not None and not image.isNull():
                pixmap = QPixmap.fromImage(image)
                self._cache[key] = (pixmap, QIcon(pixmap))
            else:
                self._cache[key] = None
            while len(self._cache) > MEMORY_LIMIT:
                self._cache.popitem(last=False)
            self.thumbnail_ready.emit(key[0])

        def failed(_details: str) -> None:
            self._pending.discard(key)

        run_task(work, on_done=done, on_failed=failed, pool=self._pool)
        return None

    def wait(self, timeout_ms: int = 10000) -> None:
        self._pool.waitForDone(timeout_ms)
