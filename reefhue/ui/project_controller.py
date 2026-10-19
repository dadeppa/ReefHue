"""Owns the open project: loading, scanning, metadata, autosave, folder lock.

All slow work runs on thread pools; results are applied to the (single-threaded)
:class:`~reefhue.core.project.Project` on the UI thread. A generation counter
discards results that arrive after the folder was switched or rescanned.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QThreadPool, QTimer, Signal

from reefhue.core import meta_cache, scan, sidecar
from reefhue.core.lock import FolderLock
from reefhue.core.project import FileItem, Project, ShiftGroup, plan_reconcile
from reefhue.io import metadata
from reefhue.ui.context import AppContext
from reefhue.ui.workers import run_task

log = logging.getLogger(__name__)

AUTOSAVE_DELAY_MS = 600
META_FLUSH_MS = 150
WATCH_DEBOUNCE_MS = 1500
MAX_WATCHED_DIRS = 256


FOLDER_ERRORS = {scan.ACCESS_MISSING: "error.folder_missing", scan.ACCESS_NONE: "error.folder_no_access"}


class ProjectController(QObject):
    project_changed = Signal(object)   # Project | None (opened / closed)
    items_reset = Signal()             # list of files changed (scan, filters, settings)
    items_updated = Signal(list)       # ids whose data changed (metadata, dates)
    busy_changed = Signal(bool)
    warnings_changed = Signal(list)    # i18n keys (+ save errors)
    folder_failed = Signal(object, str)  # folder, i18n key: cannot open it / it disappeared

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.project: Project | None = None
        self._lock: FolderLock | None = None
        self._generation = 0
        self._busy = 0
        self._save_error: str | None = None
        self._meta_pool = QThreadPool(self)
        self._meta_pool.setMaxThreadCount(4)
        self._save_pool = QThreadPool(self)
        self._save_pool.setMaxThreadCount(1)  # keeps saves in order
        self._pending_meta: list[metadata.MetadataResult] = []
        self._meta_inflight: set[str] = set()
        self._meta_busy = False

        self._save_timer = QTimer(self, singleShot=True, interval=AUTOSAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.save_async)
        self._meta_timer = QTimer(self, singleShot=True, interval=META_FLUSH_MS)
        self._meta_timer.timeout.connect(self._flush_metadata)
        self._watch_timer = QTimer(self, singleShot=True, interval=WATCH_DEBOUNCE_MS)
        self._watch_timer.timeout.connect(self.refresh)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(lambda _p: self._watch_timer.start())

    # ------------------------------------------------------------------ state
    @property
    def busy(self) -> bool:
        return self._busy > 0

    def _set_busy(self, delta: int) -> None:
        was = self.busy
        self._busy = max(0, self._busy + delta)
        if was != self.busy:
            self.busy_changed.emit(self.busy)

    def warnings(self) -> list[str]:
        result = list(self.project.warnings) if self.project else []
        if self._save_error:
            result.append("sidecar.error.save")
        return result

    @property
    def save_error(self) -> str | None:
        return self._save_error

    def _ffprobe(self) -> Path | None:
        status = self.ctx.ffmpeg_status
        return status.ffprobe.path if status is not None and status.ffprobe.ok else None

    # ------------------------------------------------------------------ open / close
    def open(self, folder: Path) -> None:
        self.close()
        self._generation += 1
        generation = self._generation
        lock = FolderLock(folder)
        locked_elsewhere = not lock.acquire()
        self._lock = None if locked_elsewhere else lock
        self._set_busy(+1)

        def work():
            access = scan.folder_access(folder)
            if access in (scan.ACCESS_MISSING, scan.ACCESS_NONE):
                return access, None
            return access, sidecar.load(folder)

        def done(result) -> None:
            self._set_busy(-1)
            if generation != self._generation:
                return
            access, loaded = result
            if loaded is None:
                if self._lock is not None:
                    self._lock.release()
                    self._lock = None
                self.folder_failed.emit(folder, FOLDER_ERRORS[access])
                return
            project = Project(folder, loaded)
            if not loaded.exists:  # a new project starts from the app's default export values
                project.settings["export_defaults"].update(self._app_export_defaults())
            if locked_elsewhere:
                project.read_only = True
                project.warnings.append("sidecar.warn.locked")
            elif access == scan.ACCESS_READ_ONLY:
                project.read_only = True
                project.warnings.append("sidecar.warn.read_only_folder")
            self.project = project
            self._save_error = None
            self._update_watcher()
            self.project_changed.emit(project)
            self.warnings_changed.emit(self.warnings())
            self.refresh()

        def failed(_details: str) -> None:
            self._set_busy(-1)
            if generation == self._generation:
                if self._lock is not None:
                    self._lock.release()
                    self._lock = None
                self.folder_failed.emit(folder, "error.folder_open")

        run_task(work, on_done=done, on_failed=failed)

    def close(self) -> None:
        if self.project is None:
            return
        self.flush()
        self._generation += 1
        self._pending_meta.clear()
        self._meta_inflight.clear()
        if self._meta_busy:
            self._meta_busy = False
            self._set_busy(-1)
        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())
        if self._lock is not None:
            self._lock.release()
            self._lock = None
        self.project = None
        self.project_changed.emit(None)
        self.warnings_changed.emit([])

    # ------------------------------------------------------------------ scanning
    def refresh(self) -> None:
        project = self.project
        if project is None:
            return
        generation = self._generation
        folder, recursive = project.folder, project.recursive
        persisted = project.persisted_entries()
        self._set_busy(+1)

        def work():
            try:
                files = scan.scan_folder(folder, recursive)
            except OSError:
                return scan.folder_access(folder), None, None
            plan = plan_reconcile(folder, persisted, files, recursive)
            return scan.ACCESS_OK, plan, meta_cache.load(folder)

        def done(result) -> None:
            self._set_busy(-1)
            if generation != self._generation or self.project is not project:
                return
            access, plan, cache = result
            if plan is None:
                log.warning("cannot scan %s (%s)", folder, access)
                if access in FOLDER_ERRORS:  # the folder vanished or access was revoked: close it
                    self.close()
                    self.folder_failed.emit(folder, FOLDER_ERRORS[access])
                return
            project.apply_reconcile(plan)
            self._apply_meta_cache(cache)
            self.items_reset.emit()
            self._update_watcher()
            self._start_metadata()
            self._schedule_save()

        def failed(_details: str) -> None:
            self._set_busy(-1)

        run_task(work, on_done=done, on_failed=failed)

    def _apply_meta_cache(self, cache: dict) -> None:
        project = self.project
        for item in project.items.values():
            if item.scanned is None or item.meta_ready:
                continue
            record = meta_cache.lookup(cache, item.rel_path, item.scanned.size, item.scanned.mtime)
            if record is None:
                continue
            fresh = item.entry.get("size") == item.scanned.size and item.entry["capture"].get("datetime_source")
            if fresh and item.persisted:
                item.meta_ready = True
                continue
            project.apply_metadata(
                item.rel_path, item.scanned.size, item.scanned.mtime, record.get("media") or {},
                record.get("datetime_original"), record.get("datetime_source"), record.get("fingerprint"),
            )

    def _start_metadata(self) -> None:
        project = self.project
        ffprobe = self._ffprobe()
        todo = [
            i for i in project.items.values()
            if i.rel_path not in self._meta_inflight and project.needs_metadata(i, ffprobe is not None)
        ]
        if not todo:
            return
        generation = self._generation
        folder = project.folder
        if not self._meta_busy:
            self._meta_busy = True
            self._set_busy(+1)
        for item in todo:
            scanned = item.scanned
            self._meta_inflight.add(scanned.rel_path)

            def work(scanned=scanned):
                return metadata.extract(folder, scanned, ffprobe)

            def done(result, generation=generation) -> None:
                if generation != self._generation:
                    return
                self._pending_meta.append(result)
                self._meta_inflight.discard(result.rel_path)
                if not self._meta_timer.isActive():
                    self._meta_timer.start()

            def failed(_details: str, generation=generation, rel=scanned.rel_path) -> None:
                if generation != self._generation:
                    return
                self._meta_inflight.discard(rel)
                if not self._meta_timer.isActive():
                    self._meta_timer.start()

            run_task(work, on_done=done, on_failed=failed, pool=self._meta_pool)

    def _flush_metadata(self) -> None:
        project = self.project
        if project is None:
            return
        ids = []
        for r in self._pending_meta:
            item = project.apply_metadata(
                r.rel_path, r.size, r.mtime, r.media, r.datetime_original, r.datetime_source, r.fingerprint, r.error
            )
            if item is not None:
                ids.append(item.id)
        self._pending_meta.clear()
        if ids:
            self.items_updated.emit(ids)
        if not self._meta_inflight and self._meta_busy:
            self._meta_busy = False
            self._set_busy(-1)
            records, folder = project.meta_cache_records(), project.folder
            run_task(lambda: meta_cache.save(folder, records))
        self._schedule_save()

    def wait_for_metadata(self, timeout_ms: int = 10000) -> None:
        """For tests and shutdown: block until queued metadata jobs are finished."""
        self._meta_pool.waitForDone(timeout_ms)

    # ------------------------------------------------------------------ watcher
    def _update_watcher(self) -> None:
        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())
        project = self.project
        if project is None or not self.ctx.settings.get("files/auto_refresh"):
            return
        dirs = {str(project.folder)}
        if project.recursive:
            for item in project.items.values():
                if item.exists and item.subfolder:
                    dirs.add(str(project.folder / item.subfolder))
                if len(dirs) >= MAX_WATCHED_DIRS:
                    break
        self._watcher.addPaths(sorted(dirs))

    def set_auto_refresh(self, enabled: bool) -> None:
        self.ctx.settings.set("files/auto_refresh", enabled)
        self._update_watcher()

    # ------------------------------------------------------------------ project settings
    def set_recursive(self, value: bool) -> None:
        if self.project is None or self.project.recursive == value:
            return
        self.project.set_recursive(value)
        self._after_change(reset=True)
        self.refresh()

    def set_hide_exported(self, value: bool) -> None:
        if self.project is None:
            return
        self.project.set_hide_exported(value)
        self._after_change(reset=True)

    def set_extension_filter(self, exts: list[str]) -> None:
        if self.project is None:
            return
        self.project.set_extension_filter(exts)
        self._after_change(reset=True)

    # ------------------------------------------------------------------ capture dates
    def set_capture_override(self, item_id: str, value: datetime) -> None:
        if self.project is None:
            return
        self.project.set_capture_override(item_id, value)
        self._after_change(ids=[item_id])

    def reset_capture(self, item_ids: list[str]) -> None:
        if self.project is None:
            return
        self.project.reset_capture(item_ids)
        self._after_change(ids=list(item_ids))

    def apply_time_shift(self, groups: list[ShiftGroup], base: dict[str, datetime] | None = None) -> None:
        if self.project is None:
            return
        self.project.apply_time_shift(groups, base)
        self._after_change(ids=[i for g in groups for i in g.file_ids])

    def _app_export_defaults(self) -> dict:
        s = self.ctx.settings
        return {"mode": s.get("export/mode"), "suffix": s.get("export/suffix"), "subfolder": s.get("export/subfolder"),
                "jpeg_quality": s.get("export/jpeg_quality"), "video_quality": s.get("export/video_quality"),
                "hw_encoder": s.get("export/hw_encoder"), "keep_telemetry": s.get("export/keep_telemetry")}

    # ------------------------------------------------------------------ export bookkeeping
    def set_export_defaults(self, values: dict) -> None:
        if self.project is None:
            return
        defaults = self.project.settings["export_defaults"]
        if any(defaults.get(k) != v for k, v in values.items()):
            defaults.update(values)
            self.project.mark_dirty()
            self._schedule_save()

    def set_export_options(self, item_id: str, **options) -> None:
        if self.project is None or item_id not in self.project.items:
            return
        current = self.project.items[item_id].entry["export_options"]
        if any(current.get(k) != v for k, v in options.items()):
            current.update(options)
            self.project.mark_dirty()
            self._after_change(ids=[item_id])

    def record_export(self, item_id: str, record: dict, target_rel: str | None) -> None:
        if self.project is None:
            return
        self.project.record_export(item_id, record, target_rel)
        self._after_change(ids=[item_id])

    # ------------------------------------------------------------------ video / profile / derived
    def set_segment(self, item_id: str, segment: dict | None) -> None:
        if self.project is None or item_id not in self.project.items:
            return
        self.project.set_segment(item_id, segment)
        self._after_change(ids=[item_id])

    def set_source_profile(self, item_id: str, value: str) -> None:
        if self.project is None or item_id not in self.project.items:
            return
        self.project.set_source_profile(item_id, value)
        self._after_change(ids=[item_id])

    def add_derived(self, rel_path: str, source_id: str) -> None:
        if self.project is None:
            return
        self.project.add_derived(rel_path, source_id)
        self._schedule_save()
        self.refresh()  # the new file appears (or stays hidden with "Hide exported files")

    # ------------------------------------------------------------------ edits
    def set_edit(self, item_id: str, edit: dict) -> None:
        """Store a file's new ``edit`` section (autosaved; status updates in the list)."""
        if self.project is None or item_id not in self.project.items:
            return
        self.project.items[item_id].entry["edit"] = edit
        self.project.mark_dirty()
        self._after_change(ids=[item_id])

    def _after_change(self, reset: bool = False, ids: list[str] | None = None) -> None:
        if reset:
            self.items_reset.emit()
        elif ids:
            self.items_updated.emit(ids)
        self._schedule_save()

    # ------------------------------------------------------------------ saving
    def _schedule_save(self) -> None:
        if self.project is not None and self.project.dirty and not self.project.read_only:
            self._save_timer.start()

    def save_async(self) -> None:
        project = self.project
        if project is None or not project.dirty or project.read_only:
            return
        data, revision = project.snapshot(), project.save_revision

        def done(_path) -> None:
            project.mark_saved(revision)
            if self._save_error is not None:
                self._save_error = None
                self.warnings_changed.emit(self.warnings())

        def failed(details: str) -> None:
            last = details.strip().splitlines()[-1] if details.strip() else ""
            self._save_error = last
            log.error("saving sidecar failed: %s", last)
            if self.project is project:
                self.warnings_changed.emit(self.warnings())

        run_task(lambda: sidecar.save(project.folder, data), on_done=done, on_failed=failed, pool=self._save_pool)

    def flush(self) -> None:
        """Synchronous final save (folder switch, quit). Small JSON, fast."""
        self._save_timer.stop()
        self._save_pool.waitForDone(5000)
        project = self.project
        if project is None or not project.dirty or project.read_only:
            return
        try:
            sidecar.save(project.folder, project.snapshot())
            project.mark_saved(project.save_revision)
        except OSError as exc:
            log.error("final save of %s failed: %s", project.folder, exc)

    def item(self, item_id: str) -> FileItem | None:
        return self.project.items.get(item_id) if self.project else None
