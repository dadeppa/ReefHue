"""Export flow (SPEC §14): dialog → confirmation → conflict planning → queue → bookkeeping."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon, QWidget

from reefhue.core import export_plan as ep
from reefhue.core import status as st
from reefhue.core.project import FileItem
from reefhue.i18n import tr
from reefhue.ui.context import AppContext
from reefhue.ui.editor_controller import EditorController, is_editable
from reefhue.ui.export_dialog import ConflictDialog, ExportDialog, OverwriteConfirmDialog
from reefhue.ui.export_queue import DONE, ExportQueue, JobState
from reefhue.ui.icons import icon
from reefhue.ui.project_controller import ProjectController

log = logging.getLogger(__name__)

AskConflict = Callable[[str], tuple[str, bool]]  # target → (action, apply to all)


def plan_jobs(folder: Path, items: list[FileItem], opts: ep.ExportOptions, ask: AskConflict,
              existing: Callable[[Path], bool] | None = None) -> tuple[list[ep.ExportJob], list[FileItem]]:
    """Targets for all items with conflict handling ("ask once per run", optionally for all).

    Returns ``(jobs, skipped items)``. Pure apart from ``ask`` and the existence check.
    """
    exists = existing or (lambda p: p.exists())
    taken: set[str] = set()
    jobs: list[ep.ExportJob] = []
    skipped: list[FileItem] = []
    remembered: str | None = None
    for item in items:
        target = ep.target_rel_path(item.rel_path, item.kind, opts)
        replace = False
        in_place = opts.mode == ep.MODE_OVERWRITE and target == item.rel_path
        conflict = target in taken or (not in_place and exists(folder / target))
        if conflict:
            action = remembered
            if action is None:
                action, apply_all = ask(target)
                if apply_all:
                    remembered = action
            if action == ep.CONFLICT_SKIP:
                skipped.append(item)
                continue
            if action == ep.CONFLICT_REPLACE and target not in taken:
                replace = True
            else:  # auto-number (also when "replace" would clash with another file of this run)
                target = ep.auto_number(target, lambda rel: rel in taken or exists(folder / rel))
        taken.add(target)
        job = ep.make_job(item.entry, item.kind, item.capture_datetime, item.capture_overridden, opts, target)
        job.replace_existing = replace
        jobs.append(job)
    return jobs, skipped


class ExportController(QObject):
    queue_started = Signal()

    def __init__(self, ctx: AppContext, projects: ProjectController, editor: EditorController,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.ctx, self.projects, self.editor = ctx, projects, editor
        self.queue = ExportQueue(ctx, self)
        self.queue.job_finished.connect(self._on_job_finished)
        self.queue.finished.connect(self._on_queue_finished)
        self._reload_after_refresh: set[str] = set()
        self._tray: QSystemTrayIcon | None = None
        projects.items_reset.connect(self._on_items_reset)

    # ------------------------------------------------------------------ selection helpers
    def exportable(self, items: list[FileItem]) -> list[FileItem]:
        return [i for i in items if is_editable(i, True)]

    def pending_items(self) -> list[FileItem]:
        """"Export all edited but not yet exported" (includes "changed after export")."""
        project = self.projects.project
        if project is None:
            return []
        wanted = (st.EDITED, st.CHANGED_AFTER_EXPORT)
        items = [i for i in project.items.values() if i.exists and i.status in wanted]
        return sorted(self.exportable(items), key=lambda i: i.rel_path.casefold())

    # ------------------------------------------------------------------ flow
    def start(self, items: list[FileItem], parent: QWidget | None = None,
              dialog_factory=ExportDialog, ask: AskConflict | None = None) -> bool:
        project = self.projects.project
        if project is None:
            return False
        if project.read_only:
            QMessageBox.information(parent, tr("export.title"), tr("export.error.read_only"))
            return False
        items = self.exportable(items)
        if not items:
            QMessageBox.information(parent, tr("export.title"), tr("export.nothing"))
            return False
        defaults = ep.ExportOptions.from_defaults(project.settings["export_defaults"])
        dialog = dialog_factory(self.ctx, items, defaults, parent)
        if dialog.exec() != ExportDialog.Accepted:
            dialog.deleteLater()
            return False
        opts = dialog.options()
        remove_audio = dialog.remove_audio.isChecked()
        video_ok = dialog.video_export_ok
        dialog.deleteLater()
        if opts.mode == ep.MODE_OVERWRITE:
            confirm = OverwriteConfirmDialog(len(items), parent)
            accepted = confirm.exec() == OverwriteConfirmDialog.Accepted
            confirm.deleteLater()
            if not accepted:
                return False
        unavailable = [i for i in items if i.kind == "video" and not video_ok]
        items = [i for i in items if i not in unavailable]
        for item in items:
            if item.kind == "video":
                self.projects.set_export_options(item.id, keep_audio=not remove_audio,
                                                 keep_telemetry=opts.keep_telemetry)
        self.projects.set_export_defaults(opts.to_defaults())
        jobs, skipped = plan_jobs(project.folder, items, opts, ask or (lambda t: self._ask_conflict(t, parent)))
        for item in skipped:
            job = ep.make_job(item.entry, item.kind, item.capture_datetime, item.capture_overridden, opts,
                              ep.target_rel_path(item.rel_path, item.kind, opts))
            self.queue.add_skipped(job, "export.status.conflict_skipped")
        for item in unavailable:
            job = ep.make_job(item.entry, item.kind, item.capture_datetime, item.capture_overridden, opts, item.rel_path)
            self.queue.add_skipped(job, "export.videos_unavailable_short")
        if jobs:
            self.queue.enqueue(jobs, project.folder)
        self.queue_started.emit()
        return bool(jobs)

    def _ask_conflict(self, target: str, parent: QWidget | None) -> tuple[str, bool]:
        dialog = ConflictDialog(target, parent)
        if dialog.exec() != ConflictDialog.Accepted:
            dialog.deleteLater()
            return ep.CONFLICT_SKIP, False
        result = (dialog.action, dialog.apply_all.isChecked())
        dialog.deleteLater()
        return result

    # ------------------------------------------------------------------ results
    def _on_job_finished(self, state: JobState) -> None:
        job = state.job
        if state.status == DONE:
            result = state.result  # type: ignore[attr-defined]
            record = ep.history_record(job, "ok", result.encoder, result.original_trashed, notes=result.notes)
            self.projects.record_export(job.item_id, record, job.target_rel)
            if job.overwrite:
                self._reload_after_refresh.add(job.item_id)
        elif state.status == "failed":
            record = ep.history_record(job, "failed", notes=[state.error_key or ""])
            self.projects.record_export(job.item_id, record, None)

    def _on_queue_finished(self, summary: dict) -> None:
        self.projects.refresh()  # new files appear (hidden while "Hide exported files" is on)
        self._notify(summary)

    def _on_items_reset(self) -> None:
        for item_id in list(self._reload_after_refresh):
            self._reload_after_refresh.discard(item_id)
            self.editor.forget(item_id)

    def _notify(self, summary: dict) -> None:
        """System notification at the end of a run (falls back silently if unavailable)."""
        if not QSystemTrayIcon.isSystemTrayAvailable() or QApplication.platformName() == "offscreen":
            return
        if self._tray is None:
            self._tray = QSystemTrayIcon(icon("waves", self.ctx.color("accent")), self)
        self._tray.show()
        key = "export.notify_failed" if summary.get("failed") else "export.notify_done"
        self._tray.showMessage(tr("app.name"), tr(key, done=summary.get("done", 0), failed=summary.get("failed", 0)))
