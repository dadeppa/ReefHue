"""Right pane: FFmpeg banner, preview area (empty state / placeholder), edit panel."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QSizePolicy,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QComboBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from reefhue.core import extensions
from reefhue.core.project import FileItem
from reefhue.i18n import tr
from reefhue.io import ffmpeg as ffmpeg_io
from reefhue.io.ffmpeg import FfmpegStatus
from reefhue.ui.context import AppContext
from reefhue.pipeline import geometry as geo
from reefhue.ui.canvas import PreviewCanvas
from reefhue.ui.edit_panel import EditPanel
from reefhue.ui.editor_controller import EditorController, is_editable
from reefhue.core.project import SOURCE_PROFILES
from reefhue.ui.preview_engine import PreviewEngine, RegionResult, RenderResult
from reefhue.ui.timeline import VideoTransport
from reefhue.ui.video_view import VideoController
from reefhue.ui.file_model import OVERRIDE_MARKER, display_resolution, format_datetime, format_duration
from reefhue.ui.icons import icon, pixmap
from reefhue.ui.thumbnail_service import ThumbnailService


class FfmpegBanner(QFrame):
    """Non-blocking notice shown in the preview area when FFmpeg is unavailable."""

    help_requested = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("Banner")
        self.icon_label = QLabel()
        self.text_label = QLabel()
        self.text_label.setWordWrap(True)
        self.help_button = QPushButton()
        self.help_button.clicked.connect(self.help_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(10)
        layout.addWidget(self.icon_label, 0, Qt.AlignTop)
        layout.addWidget(self.text_label, 1)
        layout.addWidget(self.help_button, 0, Qt.AlignVCenter)

        self._status: FfmpegStatus | None = None
        self.setVisible(False)
        ctx.language_changed.connect(self.retranslate_ui)
        ctx.theme_changed.connect(self._refresh_icon)
        ctx.ffmpeg_status_changed.connect(self.set_status)
        self._refresh_icon()
        self.retranslate_ui()

    def _refresh_icon(self, *_args) -> None:
        self.icon_label.setPixmap(
            pixmap("alert", self.ctx.color("banner_text"), 20, self.devicePixelRatioF())
        )

    def set_status(self, status: FfmpegStatus | None) -> None:
        self._status = status
        self.setVisible(status is not None and (not status.available or bool(status.warnings)))
        self.retranslate_ui()

    def retranslate_ui(self, *_args) -> None:
        self.help_button.setText(tr("ffmpeg.banner.help"))
        status = self._status
        if status is None:
            self.text_label.setText("")
        elif not status.available:
            self.text_label.setText(tr("ffmpeg.banner.missing" if status.state == ffmpeg_io.MISSING
                                       else "ffmpeg.banner.blocked"))
        else:
            lines = []
            if status.missing_encoders:
                lines.append(tr("ffmpeg.banner.missing_encoders", encoders=", ".join(status.missing_encoders)))
            if status.rosetta:
                lines.append(tr("ffmpeg.banner.rosetta"))
            self.text_label.setText("\n".join(lines))


class EmptyState(QWidget):
    """Shown when no folder is open: big 'Open folder' button + drag-and-drop hint."""

    open_folder_requested = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        card = QFrame()
        card.setObjectName("EmptyStateCard")
        card.setMaximumWidth(520)

        self.icon_label = QLabel(alignment=Qt.AlignCenter)
        self.title = QLabel(alignment=Qt.AlignCenter)
        self.title.setObjectName("EmptyStateTitle")
        self.subtitle = QLabel(alignment=Qt.AlignCenter)
        self.subtitle.setObjectName("MutedLabel")
        self.subtitle.setWordWrap(True)
        self.open_button = QPushButton()
        self.open_button.setObjectName("PrimaryButton")
        self.open_button.setIconSize(QSize(18, 18))
        self.open_button.clicked.connect(self.open_folder_requested)
        self.hint = QLabel(alignment=Qt.AlignCenter)
        self.hint.setObjectName("MutedLabel")

        inner = QVBoxLayout(card)
        inner.setContentsMargins(40, 36, 40, 36)
        inner.setSpacing(12)
        inner.addWidget(self.icon_label)
        inner.addWidget(self.title)
        inner.addWidget(self.subtitle)
        inner.addSpacing(8)
        inner.addWidget(self.open_button, 0, Qt.AlignHCenter)
        inner.addWidget(self.hint)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card, 4)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        ctx.language_changed.connect(self.retranslate_ui)
        ctx.theme_changed.connect(self.refresh_icons)
        self.refresh_icons()
        self.retranslate_ui()

    def refresh_icons(self, *_args) -> None:
        dpr = self.devicePixelRatioF()
        self.icon_label.setPixmap(pixmap("waves", self.ctx.color("accent"), 56, dpr))
        self.open_button.setIcon(icon("folder-open", self.ctx.color("accent_text")))

    def retranslate_ui(self, *_args) -> None:
        self.title.setText(tr("empty.title"))
        self.subtitle.setText(tr("empty.subtitle"))
        self.open_button.setText(tr("empty.open_button"))
        self.hint.setText(tr("empty.drop_hint"))


class FileInfoView(QWidget):
    """Selected file: thumbnail + key metadata (the full preview canvas comes with editing)."""

    def __init__(self, ctx: AppContext, thumbs: ThumbnailService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.thumbs = thumbs
        self.item: FileItem | None = None
        self.image = QLabel(alignment=Qt.AlignCenter)
        self.image.setMinimumSize(1, 1)
        self.title = QLabel(alignment=Qt.AlignCenter)
        self.title.setObjectName("EmptyStateTitle")
        self.title.setWordWrap(True)
        self.details = QLabel(alignment=Qt.AlignCenter)
        self.details.setObjectName("MutedLabel")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.notice = QLabel(alignment=Qt.AlignCenter)
        self.notice.setWordWrap(True)
        self.notice.setObjectName("Notice")
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(self.image)
        layout.addSpacing(8)
        layout.addWidget(self.title)
        layout.addWidget(self.details)
        layout.addWidget(self.notice, 0, Qt.AlignHCenter)
        layout.addStretch(1)
        thumbs.thumbnail_ready.connect(self._on_thumbnail)
        ctx.language_changed.connect(self.refresh)

    def set_item(self, item: FileItem | None) -> None:
        self.item = item
        self.refresh()

    def _on_thumbnail(self, rel_path: str) -> None:
        if self.item is not None and self.item.rel_path == rel_path:
            self.refresh()

    def refresh(self, *_args) -> None:
        item = self.item
        if item is None:
            return
        self.title.setText(item.name)
        media = item.entry["media"]
        lines = [tr(f"files.type.{item.kind}")]
        res = display_resolution(media)
        if res:
            lines.append(f"{res[0]} × {res[1]}")
        if media.get("duration_s"):
            lines.append(format_duration(media["duration_s"]))
        if media.get("camera"):
            lines.append(str(media["camera"]))
        dt = item.capture_datetime
        if dt:
            lines.append(format_datetime(dt) + (OVERRIDE_MARKER if item.capture_overridden else ""))
        self.details.setText("  ·  ".join(lines))
        notice = None
        if not item.exists:
            notice = tr("viewer.missing")
        elif not extensions.is_displayable(item.ext):
            notice = tr("viewer.cannot_display", ext=item.ext.upper() or "?")
        elif item.meta_error:
            notice = tr("viewer.unreadable")
        elif item.kind == "video" and not self.ctx.video_enabled:
            notice = tr("viewer.video_needs_ffmpeg")
        self.notice.setText(notice or "")
        self.notice.setVisible(notice is not None)
        thumb = self.thumbs.get(item) if item.exists else None
        if thumb is not None and not thumb.isNull():
            self.image.setPixmap(thumb)
        else:
            icon_name = "alert" if notice else "waves"
            self.image.setPixmap(pixmap(icon_name, self.ctx.color("text_muted"), 64, self.devicePixelRatioF()))


def info_lines(item: FileItem) -> list[str]:
    """Info overlay (Cmd/Ctrl+I): camera, resolution, codec, bit depth, profile, duration, date."""
    media = item.entry["media"]
    lines = [item.name]
    if media.get("camera"):
        lines.append(tr("info.camera", value=media["camera"]))
    res = display_resolution(media)
    if res:
        lines.append(tr("info.resolution", value=f"{res[0]} × {res[1]}"))
    if media.get("codec"):
        lines.append(tr("info.codec", value=str(media["codec"]).upper(),
                        bits=media.get("bit_depth") or "?", pix=media.get("pix_fmt") or "–"))
    if media.get("color_transfer"):
        lines.append(tr("info.transfer", value=media["color_transfer"]))
    lines.append(tr("info.profile", value=tr(f"profile.{item.entry['source_profile'].get('value', 'standard')}")))
    if media.get("duration_s"):
        lines.append(tr("info.duration", value=format_duration(media["duration_s"]),
                        fps=f"{media.get('fps') or 0:.2f}".rstrip("0").rstrip(".")))
    if item.capture_datetime:
        lines.append(tr("info.captured", value=format_datetime(item.capture_datetime)
                        + (OVERRIDE_MARKER if item.capture_overridden else "")))
    return lines


class CanvasPage(QWidget):
    """Canvas with a small view toolbar (original/edited, split view, fit, 100 %, source profile)
    and – for videos – the transport/timeline below."""

    view_changed = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.canvas = PreviewCanvas()
        self.original_button = QToolButton(checkable=True)
        self.split_button = QToolButton(checkable=True)
        self.fit_button = QToolButton()
        self.actual_button = QToolButton()
        self.zoom_label = QLabel()
        self.zoom_label.setObjectName("MutedLabel")
        self.status_label = QLabel()
        self.status_label.setObjectName("MutedLabel")
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # may be cut, never widens
        self.profile_label = QLabel()
        self.profile_combo = QComboBox()
        for value in SOURCE_PROFILES:
            self.profile_combo.addItem("", value)
        self.transport = VideoTransport(ctx)
        self.transport.setVisible(False)
        for button in (self.original_button, self.split_button, self.fit_button, self.actual_button):
            button.setFocusPolicy(Qt.NoFocus)  # keep keys (Space, arrows) with the canvas
        for button in (self.transport.play_button, self.transport.snapshot_button, self.transport.mute_button):
            button.setFocusPolicy(Qt.NoFocus)
        self.original_button.toggled.connect(self._on_original)
        self.split_button.toggled.connect(self._on_split)
        self.fit_button.clicked.connect(self.canvas.fit)
        self.actual_button.clicked.connect(self.canvas.actual_size)
        self.canvas.zoom_changed.connect(self._update_zoom_label)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 4)
        for widget in (self.original_button, self.split_button):
            bar.addWidget(widget)
        bar.addSpacing(12)
        bar.addWidget(self.profile_label)
        bar.addWidget(self.profile_combo)
        bar.addSpacing(12)
        bar.addWidget(self.status_label, 1)  # takes the free space; text may be elided, never widens the pane
        bar.addSpacing(12)
        for widget in (self.fit_button, self.actual_button, self.zoom_label):
            bar.addWidget(widget)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(bar)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.transport)
        ctx.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    def retranslate_ui(self, *_args) -> None:
        self.original_button.setText(tr("viewer.original"))
        self.original_button.setToolTip(tr("viewer.original_tip"))
        self.split_button.setText(tr("viewer.split"))
        self.split_button.setToolTip(tr("viewer.split_tip"))
        self.fit_button.setText(tr("viewer.fit"))
        self.fit_button.setToolTip(tr("viewer.fit_tip"))
        self.actual_button.setText(tr("viewer.actual"))
        self.actual_button.setToolTip(tr("viewer.actual_tip"))
        self.profile_label.setText(tr("viewer.source_profile"))
        self.profile_combo.setToolTip(tr("viewer.source_profile_tip"))
        for i in range(self.profile_combo.count()):
            self.profile_combo.setItemText(i, tr(f"profile.{self.profile_combo.itemData(i)}"))
        self._update_zoom_label()

    def _on_original(self, on: bool) -> None:
        self.canvas.show_original = on
        self.canvas.update()
        self.view_changed.emit()

    def _on_split(self, on: bool) -> None:
        self.canvas.split = on
        self.canvas.update()
        self.view_changed.emit()

    def _update_zoom_label(self) -> None:
        if self.canvas.result is None:
            self.zoom_label.setText("")
        elif self.canvas.zoom is None:
            self.zoom_label.setText(tr("viewer.zoom_fit", percent=self.canvas.zoom_percent()))
        else:
            self.zoom_label.setText(tr("viewer.zoom", percent=self.canvas.zoom_percent()))


class PreviewArea(QWidget):
    PAGE_EMPTY = 0
    PAGE_NO_SELECTION = 1
    PAGE_FILE = 2
    PAGE_CANVAS = 3

    def __init__(self, ctx: AppContext, thumbs: ThumbnailService, editor: EditorController,
                 engine: PreviewEngine, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.editor = editor
        self.engine = engine
        self.edit_panel: EditPanel | None = None
        self.crop_mode = False
        self._region_revision = -1
        self.setObjectName("PreviewArea")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.banner = FfmpegBanner(ctx)
        self.empty_state = EmptyState(ctx)
        self.no_selection = QLabel(alignment=Qt.AlignCenter)
        self.no_selection.setObjectName("MutedLabel")
        self.no_selection.setWordWrap(True)
        self.file_view = FileInfoView(ctx, thumbs)
        self.canvas_page = CanvasPage(ctx)
        self.canvas = self.canvas_page.canvas
        self.video = VideoController(ctx, editor, engine, self.canvas, self.canvas_page.transport, self)
        self.video.status.connect(self._on_video_status)
        self.video.hist_ready.connect(self._on_video_hist)
        self.canvas_page.view_changed.connect(self._sync_video_view)
        self.canvas_page.profile_combo.activated.connect(self._on_profile)
        self.info_visible = False
        self._error_shown = False

        self.stack = QStackedWidget()
        for page in (self.empty_state, self.no_selection, self.file_view, self.canvas_page):
            self.stack.addWidget(page)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.banner)
        layout.addWidget(self.stack, 1)

        editor.item_changed.connect(self._on_item_changed)
        editor.edit_changed.connect(self._on_edit_changed)
        engine.rendered.connect(self._on_rendered)
        engine.region_rendered.connect(self._on_region)
        engine.failed.connect(self._on_failed)
        self.canvas.region_needed.connect(self._on_region_needed)
        self.canvas.region_cleared.connect(engine.drop_full_resolution)
        self.canvas.crop_changed.connect(lambda rect, final: editor.set_crop(rect, live=not final))
        self.canvas.rotate_requested.connect(editor.rotate)
        self.canvas.eyedropper_picked.connect(self._on_eyedropper_picked)
        ctx.language_changed.connect(self.retranslate_ui)
        ctx.theme_changed.connect(self._on_theme)
        ctx.folder_changed.connect(self._on_folder_changed)
        self.canvas.set_accent(ctx.color("accent"))
        self._on_folder_changed(ctx.current_folder)
        self.retranslate_ui()

    def _on_theme(self, *_args) -> None:
        self.canvas.set_accent(self.ctx.color("accent"))

    def attach_edit_panel(self, panel: EditPanel) -> None:
        self.edit_panel = panel
        panel.current_frame_time = lambda: self.video.player.position if self.video.active else None
        panel.crop_mode_changed.connect(self.set_crop_mode)
        panel.eyedropper_toggled.connect(self._on_eyedropper_toggled)
        panel.actual_size_requested.connect(self.canvas.actual_size)
        panel.clipping_box.toggled.connect(self._on_clipping)

    def retranslate_ui(self, *_args) -> None:
        self.no_selection.setText(tr("viewer.no_selection"))

    # ------------------------------------------------------------------ pages
    def _on_folder_changed(self, folder: Path | None) -> None:
        self.file_view.set_item(None)
        self.canvas.clear()
        self.stack.setCurrentIndex(self.PAGE_EMPTY if folder is None else self.PAGE_NO_SELECTION)

    def show_item(self, item: FileItem | None) -> None:
        if self.ctx.current_folder is None:
            return
        if item is not None and is_editable(item, self.ctx.video_enabled):
            self.stack.setCurrentIndex(self.PAGE_CANVAS)
            return
        self.file_view.set_item(item)
        self.stack.setCurrentIndex(self.PAGE_NO_SELECTION if item is None else self.PAGE_FILE)

    # ------------------------------------------------------------------ rendering
    def preview_target(self, item: FileItem | None = None) -> int:
        quality = self.ctx.settings.get("preview/quality")
        item = item or self.editor.item
        if quality in ("50", "25") and item is not None:
            media = item.entry["media"]
            full = max(int(media.get("width") or 0), int(media.get("height") or 0)) or 4000
            return int(full * int(quality) / 100)
        dpr = self.canvas.devicePixelRatioF()
        side = max(self.canvas.width(), self.canvas.height(), 800) * dpr
        return int(min(3072, max(1024, (side + 255) // 256 * 256)))

    def _on_item_changed(self, item: FileItem | None) -> None:
        self.canvas.clear()
        self.canvas_page.status_label.setText("")
        self.video.close()
        is_video = item is not None and item.kind == "video"
        self.canvas_page.transport.setVisible(is_video)
        self._update_info()
        self._update_profile()
        if item is None:
            self.engine.set_source(None)
            return
        self.engine.set_target_size(self.preview_target(item))
        folder = self.editor.projects.project.folder
        if is_video:
            dpr = self.canvas.devicePixelRatioF()
            physical = int(max(self.canvas.width(), self.canvas.height(), 800) * dpr)
            self.video.open(item, folder, physical)
        else:
            self.engine.set_source(folder / item.rel_path, item.scanned.mtime if item.scanned else 0.0)
        self.stack.setCurrentIndex(self.PAGE_CANVAS)

    def _on_edit_changed(self, edit: dict, live: bool) -> None:
        if self.editor.item is None:
            return
        self._sync_crop(edit)
        if self.video.active:
            self.video.set_edit(edit)
            if self.video.playing:
                return  # the player renders the next frames with the new settings
        self.engine.request(edit, interactive=live, crop_mode=self.crop_mode)

    def rerender(self) -> None:
        if self.editor.item is not None:
            self.engine.set_target_size(self.preview_target())
            self.engine.request(self.editor.edit, crop_mode=self.crop_mode)

    def _on_rendered(self, result: RenderResult) -> None:
        if self.video.active and self.video.playing:
            return  # a late still render must not interrupt playback
        self.canvas.set_result(result)
        self.canvas_page._update_zoom_label()
        if self.edit_panel is not None:
            self.edit_panel.histogram.set_histogram(result.hist)
        if not result.draft and self._error_shown:
            self._error_shown = False
            self._update_profile()  # restores the normal status text (e.g. the HDR note)

    def _on_failed(self, key: str) -> None:
        self._error_shown = True
        self.canvas_page.status_label.setText(tr(key))

    def _on_region_needed(self, rect: tuple) -> None:
        final = self.engine.last_final
        if final is None or self.editor.item is None:
            return
        self._region_revision = self.editor.revision
        self.engine.request_region(self.editor.edit, rect, self.crop_mode, final.stats)

    def _on_region(self, region: RegionResult) -> None:
        if self._region_revision == self.editor.revision:
            self.canvas.set_region(region)

    # ------------------------------------------------------------------ crop / eyedropper / overlays
    def set_crop_mode(self, on: bool) -> None:
        self.crop_mode = bool(on)
        self.canvas.crop_mode = self.crop_mode
        self.canvas.region = None
        self._sync_video_view()
        if self.editor.item is not None and not (self.video.active and self.video.playing):
            self._sync_crop(self.editor.edit)
            self.engine.request(self.editor.edit, crop_mode=self.crop_mode)
        self.canvas.update()

    def _sync_crop(self, edit: dict) -> None:
        crop = edit["geometry"]["crop"]
        self.canvas.crop = dict(crop)
        result = self.engine.last_final
        if result is not None and result.crop_mode:
            w, h = result.full_size
            self.canvas.crop_ratio = geo.aspect_ratio(crop.get("aspect", "free"), w, h)
        else:
            self.canvas.crop_ratio = None
        self.canvas.update()

    def _on_eyedropper_toggled(self, on: bool) -> None:
        self.canvas.eyedropper = on
        self.canvas.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)

    def _on_eyedropper_picked(self, x: float, y: float) -> None:
        size = self.edit_panel.eyedropper_size.value() if self.edit_panel else 11
        self.editor.pick_white_balance(x, y, size)
        if self.edit_panel is not None:
            self.edit_panel.eyedropper_done()

    def _on_clipping(self, on: bool) -> None:
        self.canvas.show_clipping = on
        self.canvas.update()

    # ------------------------------------------------------------------ video, info, profile
    def _sync_video_view(self) -> None:
        page = self.canvas_page
        self.video.set_view(self.crop_mode, page.original_button.isChecked() or page.split_button.isChecked())

    def _on_video_hist(self, hist) -> None:
        if self.edit_panel is not None:
            self.edit_panel.histogram.set_histogram(hist)

    def _on_video_status(self, key: str) -> None:
        if key.startswith("snapshot:"):
            text = tr("video.snapshot_saved", name=key.split(":", 1)[1])
        else:
            text = tr(key)
        self.canvas_page.status_label.setText(text)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self.video.handle_key(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def set_info_visible(self, on: bool) -> None:
        self.info_visible = on
        self._update_info()

    def _update_info(self) -> None:
        item = self.editor.item
        self.canvas.show_info = self.info_visible and item is not None
        self.canvas.info_lines = info_lines(item) if item is not None else []
        self.canvas.update()

    def _update_profile(self) -> None:
        item = self.editor.item
        combo = self.canvas_page.profile_combo
        combo.setEnabled(item is not None)
        if item is None:
            return
        value = item.entry["source_profile"].get("value", "standard")
        combo.setCurrentIndex(max(0, combo.findData(value)))
        self.canvas_page.status_label.setText(tr("viewer.hdr_warning") if value == "hdr_hlg" else "")

    def _on_profile(self, index: int) -> None:
        item = self.editor.item
        if item is None:
            return
        self.editor.projects.set_source_profile(item.id, self.canvas_page.profile_combo.itemData(index))
        self._update_profile()
        self._update_info()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.editor.item is not None and self.ctx.settings.get("preview/quality") == "auto":
            target = self.preview_target()
            if abs(target - self.engine.target_size) / self.engine.target_size > 0.2:
                self.rerender()


class ViewerPane(QWidget):
    """Preview on top, collapsible/resizable edit panel below (vertical splitter)."""

    def __init__(self, ctx: AppContext, thumbs: ThumbnailService, editor: EditorController,
                 engine: PreviewEngine, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.preview = PreviewArea(ctx, thumbs, editor, engine)
        self.edit_panel = EditPanel(ctx, editor)
        self.preview.attach_edit_panel(self.edit_panel)
        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setObjectName("ViewerSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.preview)
        self.splitter.addWidget(self.edit_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter)

        self._edit_panel_wanted = True
        ctx.folder_changed.connect(self._update_edit_panel_visibility)
        self._update_edit_panel_visibility()

    def set_edit_panel_wanted(self, visible: bool) -> None:
        self._edit_panel_wanted = visible
        self._update_edit_panel_visibility()

    def _update_edit_panel_visibility(self, *_args) -> None:
        # Empty state (no folder) uses the whole right side (SPEC §4).
        self.edit_panel.setVisible(self._edit_panel_wanted and self.ctx.current_folder is not None)
