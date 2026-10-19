"""Background export queue + queue panel (SPEC §14 "Batch").

Photos run in parallel, videos one at a time; the app stays usable and editing
continues (jobs are snapshots taken at queue time). Pause stops new jobs and
holds running ones at their next checkpoint; cancel works per job or for all.
The computer is kept awake while the queue runs.
"""

from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from reefhue.core import export_plan as ep
from reefhue.i18n import tr
from reefhue.io import export as ex
from reefhue.io.sleep_inhibit import SleepInhibitor
from reefhue.ui.context import AppContext

log = logging.getLogger(__name__)

PHOTO_WORKERS = 2

QUEUED, RUNNING, DONE, FAILED, SKIPPED, CANCELLED = "queued", "running", "done", "failed", "skipped", "cancelled"
FINISHED = (DONE, FAILED, SKIPPED, CANCELLED)


@dataclass
class JobState:
    job: ep.ExportJob
    status: str = QUEUED
    progress: float = 0.0
    error_key: str | None = None
    error_detail: str = ""
    notes: list[str] = field(default_factory=list)
    target: Path | None = None
    control: ex.Control = field(default_factory=ex.Control)

    @property
    def id(self) -> str:
        return self.job.id


class _Signals(QObject):
    progress = Signal(str, float)
    done = Signal(str, object)
    failed = Signal(str, str, str, bool)  # id, i18n key, detail, cancelled


class _Runner(QRunnable):
    def __init__(self, state: JobState, folder: Path, ffmpeg, ffprobe, signals: _Signals) -> None:
        super().__init__()
        self.state, self.folder, self.ffmpeg, self.ffprobe, self.signals = state, folder, ffmpeg, ffprobe, signals

    def run(self) -> None:  # worker thread
        job_id = self.state.id
        try:
            result = ex.run_job(self.state.job, self.folder, self.ffmpeg, self.ffprobe,
                                lambda f: self.signals.progress.emit(job_id, float(f)), self.state.control)
        except ex.ExportCancelled:
            self.signals.failed.emit(job_id, "export.status.cancelled", "", True)
        except ex.ExportError as exc:
            log.error("export of %s failed: %s %s", self.state.job.name, exc.key, exc.detail)
            self.signals.failed.emit(job_id, exc.key, exc.detail, False)
        except Exception:  # noqa: BLE001 – report, never crash the app
            detail = traceback.format_exc()
            log.error("export of %s crashed:\n%s", self.state.job.name, detail)
            self.signals.failed.emit(job_id, "export.error.unexpected", detail, False)
        else:
            self.signals.done.emit(job_id, result)


class ExportQueue(QObject):
    changed = Signal(object)        # JobState
    added = Signal(object)          # JobState
    totals_changed = Signal()
    finished = Signal(dict)         # summary counts
    job_finished = Signal(object)   # JobState (done / failed / cancelled)

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.states: dict[str, JobState] = {}
        self.order: list[str] = []
        self.folder: Path | None = None
        self.paused = False
        self._signals = _Signals()
        self._signals.progress.connect(self._on_progress)
        self._signals.done.connect(self._on_done)
        self._signals.failed.connect(self._on_failed)
        self._photo_pool = QThreadPool(self)
        self._photo_pool.setMaxThreadCount(PHOTO_WORKERS)
        self._video_pool = QThreadPool(self)
        self._video_pool.setMaxThreadCount(1)
        self._sleep = SleepInhibitor()
        self._active_since: float | None = None
        self._active_time = 0.0
        self._batch_counts: dict[str, int] = {}

    # ---- queue management -------------------------------------------------------------
    def enqueue(self, jobs: list[ep.ExportJob], folder: Path) -> None:
        self.folder = folder
        for job in jobs:
            state = JobState(job)
            self.states[job.id] = state
            self.order.append(job.id)
            self.added.emit(state)
        self._schedule()
        self.totals_changed.emit()

    def add_skipped(self, job: ep.ExportJob, reason_key: str) -> None:
        state = JobState(job, status=SKIPPED, error_key=reason_key)
        self.states[job.id] = state
        self.order.append(job.id)
        self.added.emit(state)
        self.totals_changed.emit()

    @property
    def running(self) -> list[JobState]:
        return [s for s in self.states.values() if s.status == RUNNING]

    @property
    def queued(self) -> list[JobState]:
        return [self.states[i] for i in self.order if self.states[i].status == QUEUED]

    @property
    def busy(self) -> bool:
        return bool(self.running or self.queued)

    def _ffmpeg(self):
        status = self.ctx.ffmpeg_status
        if status is None or not status.available:
            return None, None
        return status.ffmpeg.path, status.ffprobe.path

    def _schedule(self) -> None:
        if self.paused or self.folder is None:
            return
        ffmpeg, ffprobe = self._ffmpeg()
        photos_running = sum(1 for s in self.running if s.job.kind == "photo")
        video_running = any(s.job.kind == "video" for s in self.running)
        for state in self.queued:
            if state.job.kind == "photo" and photos_running < PHOTO_WORKERS:
                photos_running += 1
            elif state.job.kind == "video" and not video_running:
                video_running = True
            else:
                continue
            state.status = RUNNING
            pool = self._photo_pool if state.job.kind == "photo" else self._video_pool
            pool.start(_Runner(state, self.folder, ffmpeg, ffprobe, self._signals))
            self.changed.emit(state)
        if self.running:
            self._sleep.start()
            if self._active_since is None:
                self._active_since = time.monotonic()

    def pause(self) -> None:
        self.paused = True
        for state in self.running:
            state.control.resume.clear()
        self._stop_clock()
        self.totals_changed.emit()

    def resume(self) -> None:
        self.paused = False
        for state in self.running:
            state.control.resume.set()
        if self.running:
            self._active_since = time.monotonic()
        self._schedule()
        self.totals_changed.emit()

    def cancel(self, job_id: str) -> None:
        state = self.states.get(job_id)
        if state is None or state.status in FINISHED:
            return
        if state.status == QUEUED:
            state.status = CANCELLED
            state.error_key = "export.status.cancelled"
            self.changed.emit(state)
            self._check_finished()
        else:
            state.control.cancel.set()
            state.control.resume.set()  # a paused job must wake up to notice
        self.totals_changed.emit()

    def cancel_all(self) -> None:
        for job_id in list(self.order):
            self.cancel(job_id)

    def clear_finished(self) -> None:
        self.order = [i for i in self.order if self.states[i].status not in FINISHED]
        self.states = {i: self.states[i] for i in self.order}
        self.totals_changed.emit()

    def wait(self, timeout_ms: int = 60000) -> None:
        self._photo_pool.waitForDone(timeout_ms)
        self._video_pool.waitForDone(timeout_ms)

    # ---- results ------------------------------------------------------------------------
    @Slot(str, float)
    def _on_progress(self, job_id: str, fraction: float) -> None:
        state = self.states.get(job_id)
        if state is not None:
            state.progress = fraction
            self.changed.emit(state)
            self.totals_changed.emit()

    @Slot(str, object)
    def _on_done(self, job_id: str, result: ex.ExportResult) -> None:
        state = self.states[job_id]
        state.status, state.progress, state.target, state.notes = DONE, 1.0, result.target, list(result.notes)
        state.result = result  # type: ignore[attr-defined]
        self._finish(state)

    @Slot(str, str, str, bool)
    def _on_failed(self, job_id: str, key: str, detail: str, cancelled: bool) -> None:
        state = self.states[job_id]
        state.status = CANCELLED if cancelled else FAILED
        state.error_key, state.error_detail = key, detail
        self._finish(state)

    def _finish(self, state: JobState) -> None:
        self.changed.emit(state)
        self.job_finished.emit(state)
        self._schedule()
        self.totals_changed.emit()
        self._check_finished()

    def _check_finished(self) -> None:
        if self.busy:
            return
        self._sleep.stop()
        self._stop_clock()
        summary = {s: 0 for s in FINISHED}
        for state in self.states.values():
            if state.status in summary:
                summary[state.status] += 1
        self.finished.emit(summary)

    def _stop_clock(self) -> None:
        if self._active_since is not None:
            self._active_time += time.monotonic() - self._active_since
            self._active_since = None

    # ---- totals ---------------------------------------------------------------------------
    def totals(self) -> tuple[float, int, int, float | None]:
        """``(fraction, finished, total, seconds remaining or None)`` for the jobs in the list."""
        states = [self.states[i] for i in self.order]
        if not states:
            return 0.0, 0, 0, None
        cost = sum(s.job.cost for s in states) or 1.0
        done = sum(s.job.cost * (1.0 if s.status in FINISHED else s.progress) for s in states)
        fraction = min(1.0, done / cost)
        finished = sum(1 for s in states if s.status in FINISHED)
        elapsed = self._active_time + (time.monotonic() - self._active_since if self._active_since else 0.0)
        remaining = None
        if 0.02 < fraction < 1.0 and elapsed > 2.0:
            remaining = elapsed * (1.0 - fraction) / fraction
        return fraction, finished, len(states), remaining


def format_remaining(seconds: float | None) -> str:
    if seconds is None:
        return tr("queue.remaining_unknown")
    seconds = int(round(seconds))
    if seconds < 60:
        return tr("queue.remaining_seconds", n=max(1, seconds))
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return tr("queue.remaining_minutes", m=minutes, s=f"{sec:02d}")
    hours, minutes = divmod(minutes, 60)
    return tr("queue.remaining_hours", h=hours, m=f"{minutes:02d}")


class QueuePanel(QWidget):
    """Per-file rows with progress, status and cancel; totals, pause, cancel all, error log."""

    COL_FILE, COL_TARGET, COL_PROGRESS, COL_STATUS, COL_CANCEL = range(5)

    def __init__(self, ctx: AppContext, queue: ExportQueue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx, self.queue = ctx, queue
        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionResizeMode(self.COL_TARGET, QHeaderView.Stretch)
        header.setSectionResizeMode(self.COL_CANCEL, QHeaderView.ResizeToContents)
        self.rows: dict[str, QTreeWidgetItem] = {}
        self.bars: dict[str, QProgressBar] = {}
        self.cancels: dict[str, QToolButton] = {}
        self.total = QProgressBar()
        self.total.setRange(0, 1000)
        self.summary = QLabel()
        self.summary.setObjectName("MutedLabel")
        self.pause_button = QPushButton()
        self.pause_button.clicked.connect(self._toggle_pause)
        self.cancel_all_button = QPushButton()
        self.cancel_all_button.clicked.connect(queue.cancel_all)
        self.copy_log_button = QPushButton()
        self.copy_log_button.clicked.connect(self.copy_error_log)
        self.clear_button = QPushButton()
        self.clear_button.clicked.connect(self._clear)
        footer = QHBoxLayout()
        footer.addWidget(self.total, 1)
        footer.addWidget(self.summary)
        for button in (self.pause_button, self.cancel_all_button, self.copy_log_button, self.clear_button):
            footer.addWidget(button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.addWidget(self.tree, 1)
        layout.addLayout(footer)

        queue.added.connect(self._add_row)
        queue.changed.connect(self._update_row)
        queue.totals_changed.connect(self._update_totals)
        ctx.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    def retranslate_ui(self, *_args) -> None:
        self.tree.setHeaderLabels([tr("queue.col.file"), tr("queue.col.target"), tr("queue.col.progress"),
                                   tr("queue.col.status"), ""])
        self.cancel_all_button.setText(tr("queue.cancel_all"))
        self.cancel_all_button.setToolTip(tr("queue.cancel_all_tip"))
        self.pause_button.setToolTip(tr("queue.pause_tip"))
        self.copy_log_button.setToolTip(tr("queue.copy_log_tip"))
        self.clear_button.setToolTip(tr("queue.clear_tip"))
        self.copy_log_button.setText(tr("queue.copy_log"))
        self.clear_button.setText(tr("queue.clear"))
        for job_id in self.rows:
            self._update_row(self.queue.states[job_id])
        self._update_totals()

    def _add_row(self, state: JobState) -> None:
        item = QTreeWidgetItem([state.job.name, state.job.target_rel, "", ""])
        self.tree.addTopLevelItem(item)
        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setTextVisible(False)
        bar.setMaximumHeight(10)
        self.tree.setItemWidget(item, self.COL_PROGRESS, bar)
        cancel = QToolButton()
        cancel.setText("✕")
        cancel.setAutoRaise(True)
        cancel.setToolTip(tr("queue.cancel_one"))
        cancel.clicked.connect(lambda _=False, i=state.id: self.queue.cancel(i))
        self.tree.setItemWidget(item, self.COL_CANCEL, cancel)
        self.rows[state.id], self.bars[state.id], self.cancels[state.id] = item, bar, cancel
        self._update_row(state)

    def _update_row(self, state: JobState) -> None:
        item = self.rows.get(state.id)
        if item is None:
            return
        self.bars[state.id].setValue(int(state.progress * 1000))
        text = tr(f"export.status.{state.status}")
        if state.status in (FAILED, SKIPPED) and state.error_key:
            text = f"{text}: {tr(state.error_key)}"
        elif state.status == DONE and state.notes:
            text = f"{text} – {'; '.join(tr(n) for n in state.notes)}"
        item.setText(self.COL_STATUS, text)
        item.setToolTip(self.COL_STATUS, state.error_detail[-1500:] if state.error_detail else text)
        if state.target is not None and self.queue.folder is not None:
            try:
                item.setText(self.COL_TARGET, state.target.relative_to(self.queue.folder).as_posix())
            except ValueError:
                item.setText(self.COL_TARGET, str(state.target))
        color = {FAILED: "danger", DONE: "status_ok", CANCELLED: "text_muted", SKIPPED: "status_warn"}.get(state.status)
        if color:
            item.setForeground(self.COL_STATUS, QColor(self.ctx.color(color)))
        self.cancels[state.id].setEnabled(state.status not in FINISHED)

    def _update_totals(self) -> None:
        fraction, finished, total, remaining = self.queue.totals()
        self.total.setValue(int(fraction * 1000))
        if total == 0:
            self.summary.setText("")
        elif self.queue.busy:
            key = "queue.summary_paused" if self.queue.paused else "queue.summary"
            self.summary.setText(tr(key, done=finished, total=total, remaining=format_remaining(remaining)))
        else:
            self.summary.setText(tr("queue.summary_done", done=finished, total=total))
        self.pause_button.setText(tr("queue.resume") if self.queue.paused else tr("queue.pause"))
        self.pause_button.setEnabled(self.queue.busy)
        self.cancel_all_button.setEnabled(self.queue.busy)
        self.copy_log_button.setEnabled(any(s.status == FAILED for s in self.queue.states.values()))
        self.clear_button.setEnabled(any(s.status in FINISHED for s in self.queue.states.values()))

    def _toggle_pause(self) -> None:
        if self.queue.paused:
            self.queue.resume()
        else:
            self.queue.pause()

    def _clear(self) -> None:
        for job_id, state in list(self.queue.states.items()):
            if state.status in FINISHED:
                item = self.rows.pop(job_id, None)
                self.bars.pop(job_id, None)
                self.cancels.pop(job_id, None)
                if item is not None:
                    self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        self.queue.clear_finished()

    def error_log(self) -> str:
        lines = []
        for job_id in self.queue.order:
            state = self.queue.states[job_id]
            if state.status == FAILED:
                lines.append(f"{state.job.src_rel} → {state.job.target_rel}: {tr(state.error_key or '')}")
                if state.error_detail:
                    lines.append(state.error_detail.strip())
                lines.append("")
        return "\n".join(lines)

    def copy_error_log(self) -> None:
        QGuiApplication.clipboard().setText(self.error_log())

