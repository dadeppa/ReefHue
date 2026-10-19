"""Edit state of the current file: parameter changes, undo/redo, copy/paste, presets (SPEC §7, §12)."""

from __future__ import annotations

import copy
import logging

from PySide6.QtCore import QObject, Signal

from reefhue.core import extensions, presets
from reefhue.core.history import EditHistory
from reefhue.core.project import FileItem
from reefhue.pipeline import auto as magic
from reefhue.pipeline import auto_basic, curves as curve_math, geometry as geo
from reefhue.pipeline import pipeline as pl
from reefhue.pipeline.params import (
    coerce_value,
    couple_levels,
    default_edit,
    get_path,
    param_for_path,
    set_path,
)
from reefhue.ui.context import AppContext
from reefhue.ui.preview_engine import PreviewEngine
from reefhue.ui.project_controller import ProjectController
from reefhue.ui.workers import run_task

log = logging.getLogger(__name__)


def is_editable(item: FileItem | None, video_enabled: bool = False) -> bool:
    """Photos always; videos only when FFmpeg is available (SPEC §3) and the file could be probed."""
    if item is None or not item.exists or not extensions.is_displayable(item.ext) or item.meta_error:
        return False
    if item.kind == extensions.PHOTO:
        return True
    return (item.kind == extensions.VIDEO and video_enabled and bool(item.entry["media"].get("codec"))
            and bool(item.entry["media"].get("duration_s")))


class EditorController(QObject):
    item_changed = Signal(object)        # FileItem | None
    edit_changed = Signal(object, bool)  # edit, live (dragging)
    history_changed = Signal()
    clipboard_changed = Signal()
    busy_changed = Signal(bool)          # auto / eyedropper computations
    magic_progress = Signal(int, int)    # done, total (apply to selection); (0, 0) when finished

    def __init__(self, ctx: AppContext, projects: ProjectController, engine: PreviewEngine,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.projects = projects
        self.engine = engine
        self.item: FileItem | None = None
        self.histories: dict[str, EditHistory] = {}
        self.clipboard: dict | None = None
        self.revision = 0
        projects.project_changed.connect(self._on_project_changed)

    # ------------------------------------------------------------------ current file
    def _on_project_changed(self, _project) -> None:
        self.histories.clear()
        self.set_item(None)

    def set_item(self, item: FileItem | None) -> None:
        item = item if is_editable(item, self.ctx.video_enabled) else None
        if item is self.item:
            return
        if self.item is not None:
            self._history(self.item).close()
        self.item = item
        self.revision += 1
        self.item_changed.emit(item)
        if item is not None:
            self.edit_changed.emit(self.edit, False)
        self.history_changed.emit()

    @property
    def edit(self) -> dict:
        return self.item.entry["edit"] if self.item is not None else default_edit()

    def _history(self, item: FileItem) -> EditHistory:
        if item.id not in self.histories:
            self.histories[item.id] = EditHistory(item.entry["edit"])
        return self.histories[item.id]

    def _store(self, item: FileItem, new_edit: dict, merge_key: str | None = None, live: bool = False) -> None:
        self._history(item).record(new_edit, merge_key)
        if merge_key is None:
            self._history(item).close()
        self.projects.set_edit(item.id, copy.deepcopy(new_edit))
        if item is self.item:
            self.revision += 1
            self.edit_changed.emit(item.entry["edit"], live)
        self.history_changed.emit()

    def forget(self, item_id: str) -> None:
        """Drop a file's undo history (e.g. after "overwrite original" baked the edit into the file)."""
        self.histories.pop(item_id, None)
        if self.item is not None and self.item.id == item_id:
            item, self.item = self.item, None
            self.set_item(item)  # reload: new pixels, neutral edit

    # ------------------------------------------------------------------ parameters
    def set_param(self, path: tuple, value, live: bool = True) -> None:
        if self.item is None:
            return
        edit = self.edit
        param = param_for_path(path)
        if param is not None:
            value = coerce_value(param, value)
        new = set_path(edit, path, value)
        if path[0] == "levels" and path[-1] in ("black", "white"):
            channel = new["levels"][path[1]]
            channel["black"], channel["white"] = couple_levels(channel["black"], channel["white"], path[-1])
        if path == ("underwater", "depth_m"):
            new["underwater"]["auto_result"]["depth_estimated"] = False  # the user's depth wins
        if path == ("geometry", "flip_h") or path == ("geometry", "flip_v"):
            if bool(value) != bool(get_path(edit, path)):
                new["geometry"]["crop"] = geo.transform_crop_for_flip(new["geometry"]["crop"], path[1] == "flip_h")
        if new == edit:
            return
        self._store(self.item, new, merge_key="/".join(map(str, path)), live=live)

    def commit(self) -> None:
        """End the current undo step (mouse/key release, Enter) and request a final render."""
        if self.item is None:
            return
        self._history(self.item).close()
        self.edit_changed.emit(self.edit, False)

    def set_curve(self, channel: str, points: list, live: bool = True) -> None:
        if self.item is None:
            return
        new = copy.deepcopy(self.edit)
        new["curves"][channel]["points"] = curve_math.normalize_points(points)
        if new != self.edit:
            self._store(self.item, new, merge_key=f"curves/{channel}", live=live)

    def set_lut(self, name: str | None) -> None:
        """Choose a LUT by file name (``None`` = no LUT); its SHA-256 goes into the sidecar."""
        if self.item is None:
            return
        from reefhue.pipeline import lut as lut_library

        new = copy.deepcopy(self.edit)
        path = lut_library.LIBRARY.resolve(name)
        new["lut"]["file"] = name
        new["lut"]["sha256"] = lut_library.sha256_of(path) if path is not None else None
        if new != self.edit:
            self._store(self.item, new)

    def set_crop(self, rect: dict, live: bool) -> None:
        if self.item is None:
            return
        new = copy.deepcopy(self.edit)
        new["geometry"]["crop"] = {**new["geometry"]["crop"], **{k: rect[k] for k in ("x", "y", "w", "h")}}
        if new != self.edit:
            self._store(self.item, new, merge_key="geometry/crop", live=live)
        if not live:
            self.commit()

    def set_crop_aspect(self, aspect: str, frame_size: tuple[int, int]) -> None:
        if self.item is None:
            return
        w, h = frame_size
        crop = dict(self.edit["geometry"]["crop"], aspect=aspect)
        crop = geo.fit_aspect(crop, geo.aspect_ratio(aspect, w, h), w, h)
        new = copy.deepcopy(self.edit)
        new["geometry"]["crop"] = crop
        self._store(self.item, new)

    def reset_crop(self) -> None:
        if self.item is None:
            return
        new = copy.deepcopy(self.edit)
        new["geometry"]["crop"] = default_edit()["geometry"]["crop"]
        self._store(self.item, new)

    def rotate(self, quarter_turns_cw: int) -> None:
        """Rotate the *displayed* image by 90° steps (R / Shift+R)."""
        if self.item is None:
            return
        g = self.edit["geometry"]
        mirrored = bool(g.get("flip_h")) != bool(g.get("flip_v"))  # one flip reverses the direction
        new = copy.deepcopy(self.edit)
        delta = -quarter_turns_cw if mirrored else quarter_turns_cw
        new["geometry"]["rotate_90"] = (int(g.get("rotate_90", 0)) + delta) % 4
        new["geometry"]["crop"] = geo.transform_crop_for_rotation(g["crop"], quarter_turns_cw % 4)
        self._store(self.item, new)

    # ------------------------------------------------------------------ undo / redo
    @property
    def can_undo(self) -> bool:
        return self.item is not None and self._history(self.item).can_undo

    @property
    def can_redo(self) -> bool:
        return self.item is not None and self._history(self.item).can_redo

    def undo(self) -> None:
        self._step(lambda h: h.undo())

    def redo(self) -> None:
        self._step(lambda h: h.redo())

    def _step(self, fn) -> None:
        if self.item is None:
            return
        state = fn(self._history(self.item))
        if state is None:
            return
        self.projects.set_edit(self.item.id, state)
        self.revision += 1
        self.edit_changed.emit(self.item.entry["edit"], False)
        self.history_changed.emit()

    # ------------------------------------------------------------------ sections, copy/paste, presets
    def reset_sections(self, sections) -> None:
        if self.item is not None:
            self._store(self.item, presets.reset_sections(self.edit, sections))

    def reset_items(self, items: list[FileItem]) -> None:
        for item in items:
            if is_editable(item, self.ctx.video_enabled) and item.entry["edit"] != default_edit():
                self._store(item, default_edit())

    def copy_settings(self) -> bool:
        if self.item is None:
            return False
        self.clipboard = copy.deepcopy(self.edit)
        self.clipboard_changed.emit()
        return True

    def apply_partial(self, items: list[FileItem], partial: dict, sections) -> int:
        """Paste / apply preset to several files; each file gets its own undo step."""
        count = 0
        for item in items:
            if not is_editable(item, self.ctx.video_enabled):
                continue
            new = presets.apply(item.entry["edit"], partial, sections)
            if new != item.entry["edit"]:
                self._store(item, new)
                count += 1
        return count

    # ------------------------------------------------------------------ auto / eyedropper
    def _preview_data(self):
        result = self.engine.last_final
        if result is None or result.geom is None or self.item is None:
            return None
        return result

    def pick_white_balance(self, x_full: float, y_full: float, size_full_px: int) -> bool:
        """Eyedropper: neutralise the area around (x, y) by setting temperature/tint."""
        result = self._preview_data()
        if result is None:
            return False
        geom, s = result.geom, result.scale
        px, py = int(x_full * s), int(y_full * s)
        n = max(1, int(round(size_full_px * s)) | 1)  # odd, at least one pixel
        h, w = geom.shape[:2]
        margin = n + 8
        x0, y0, x1, y1 = max(0, px - margin), max(0, py - margin), min(w, px + margin + 1), min(h, py + margin + 1)
        ctx = pl.make_context(result.full_size, (w, h), self.edit, apply_crop=not result.crop_mode)
        pre = pl.pre_white_balance(geom[y0:y1, x0:x1], self.edit, ctx)
        t, m = auto_basic.eyedropper_white_balance(pre, px - x0, py - y0, n)
        self.set_values({("white_balance", "temperature"): t, ("white_balance", "tint"): m})
        return True

    def set_values(self, values: dict) -> None:
        if self.item is None:
            return
        new = self.edit
        for path, value in values.items():
            param = param_for_path(path)
            new = set_path(new, path, coerce_value(param, value) if param else value)
        if new != self.edit:
            self._store(self.item, new)

    # ------------------------------------------------------------------ magic button (SPEC §9)
    def magic(self, items: list[FileItem] | None = None, current_frame_only: bool = False,
              frame_time: float | None = None) -> None:
        """Analyse ``items`` (default: the current file) one after another on a worker thread."""
        targets = [i for i in (items or ([self.item] if self.item else [])) if is_editable(i, self.ctx.video_enabled)]
        if not targets or self.projects.project is None:
            return
        folder = self.projects.project.folder
        self.busy_changed.emit(True)
        self._magic_queue = list(targets)
        self._magic_total = len(targets)
        self._magic_options = (current_frame_only, frame_time)
        self._magic_next(folder)

    def _magic_next(self, folder) -> None:
        if not self._magic_queue:
            self.busy_changed.emit(False)
            self.magic_progress.emit(0, 0)
            return
        item = self._magic_queue.pop(0)
        done = self._magic_total - len(self._magic_queue) - 1
        self.magic_progress.emit(done, self._magic_total)
        current_only, frame_time = self._magic_options
        edit = copy.deepcopy(item.entry["edit"])
        path = folder / item.rel_path
        media = copy.deepcopy(item.entry["media"])
        segment = copy.deepcopy(item.entry["video"].get("segment"))
        single = item is self.item and current_only

        def work():
            from reefhue.io.decode import load_photo
            from reefhue.io.video import read_frame

            if item.kind == "photo":
                images, source = [load_photo(path, max_side=magic.ANALYSIS_SIZE)[0]], "frame"
            else:
                rotation = int(media.get("rotation_meta") or 0)
                if single and frame_time is not None:
                    times, source = [frame_time], "frame"
                else:
                    start, end = (segment["start_s"], segment["end_s"]) if segment else (0.0, float(media.get("duration_s") or 0))
                    times, source = magic.segment_times(start, end), "segment"
                images = [read_frame(path, t, rotation, magic.ANALYSIS_SIZE)[0] for t in times]
            return magic.analyze(images, edit), source

        def done(result) -> None:
            analysis, source = result
            new = magic.apply_result(item.entry["edit"], analysis, source)
            self._store(item, new)
            self._magic_next(folder)

        def failed(_details: str) -> None:
            self._magic_next(folder)

        run_task(work, on_done=done, on_failed=failed, guard=self)

    def auto(self, kind: str) -> None:
        """``kind``: "white_balance" or "light". Computed on a worker thread from the preview."""
        result = self._preview_data()
        if result is None:
            return
        edit, item = copy.deepcopy(self.edit), self.item
        geom = result.geom
        ctx = pl.make_context(result.full_size, (geom.shape[1], geom.shape[0]), edit,
                              apply_crop=not result.crop_mode, stats=result.stats)

        def work():
            if kind == "white_balance":
                t, m = auto_basic.auto_white_balance(pl.pre_white_balance(geom, edit, ctx))
                return {("white_balance", "temperature"): t, ("white_balance", "tint"): m}
            values = auto_basic.auto_light(geom, edit, ctx)
            return {("light", k): v for k, v in values.items()}

        def done(values) -> None:
            self.busy_changed.emit(False)
            if self.item is item:
                self.set_values(values)

        def failed(_details: str) -> None:
            self.busy_changed.emit(False)

        self.busy_changed.emit(True)
        run_task(work, on_done=done, on_failed=failed, guard=self)
