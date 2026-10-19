"""Background preview rendering for photos (SPEC §6, §8).

* One render thread; requests coalesce ("latest wins"), stale results are dropped.
* Caches (touched only on the render thread): decoded preview source, a smaller
  draft copy for live dragging, the geometry result, the "original" view, the
  linear-stage prefix, global stats and – for the 100 % view – the full-
  resolution geometry result.
* Preview, region renders and export use the same pipeline functions.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QObject, QThreadPool, Signal
from PySide6.QtGui import QImage

from reefhue.io.decode import load_photo
from reefhue.pipeline import geometry as geo
from reefhue.pipeline import pipeline as pl
from reefhue.pipeline.analysis import clipping_overlay, histogram
from reefhue.pipeline.context import RenderContext
from reefhue.pipeline.stages import dehaze as dehaze_stage
from reefhue.pipeline.stages import details as details_stage
from reefhue.ui.workers import run_task

log = logging.getLogger(__name__)

DRAFT_MAX_SIDE = 1100
PRELOAD_SLOTS = 2
REGION_MARGIN_MIN = 32


def to_qimage(u8: np.ndarray) -> QImage:
    h, w = u8.shape[:2]
    data = np.ascontiguousarray(u8)
    if data.ndim == 3 and data.shape[2] == 4:
        return QImage(data.data, w, h, 4 * w, QImage.Format_RGBA8888).copy()
    return QImage(data.data, w, h, 3 * w, QImage.Format_RGB888).copy()


@dataclass
class RenderResult:
    generation: int
    request_id: int
    draft: bool
    image: QImage
    original: QImage
    overlay: QImage
    hist: dict
    full_size: tuple[int, int]     # full-resolution output size (uncropped in crop mode)
    scale: float                   # preview pixels per full-resolution pixel
    crop_mode: bool
    stats: dict = field(default_factory=dict)
    geom: np.ndarray | None = None  # linear geometry output at preview scale (eyedropper)
    edit: dict | None = None
    seconds: float = 0.0


@dataclass
class RegionResult:
    generation: int
    request_id: int
    image: QImage
    rect: tuple[int, int, int, int]  # in full-resolution output pixels
    original: QImage | None = None   # same region without colour edits (split view / original)


class _State:
    """Render-thread caches for the current source."""

    def __init__(self) -> None:
        self.reset(-1, None, 0)

    def reset(self, generation: int, path: Path | None, target: int) -> None:
        self.generation = generation
        self.path = path
        self.target = target
        self.src: np.ndarray | None = None
        self.full_size: tuple[int, int] | None = None
        self.draft: np.ndarray | None = None
        self.geom_key: dict[bool, str] = {}
        self.geom: dict[bool, np.ndarray] = {}
        self.original: dict[bool, QImage] = {}
        self.prefix: dict[bool, dict] = {True: {}, False: {}}
        self.stats_key: str | None = None
        self.stats: dict = {}
        self.full_geom_key: str | None = None
        self.full_geom: np.ndarray | None = None


def _geometry_key(edit: dict, crop_mode: bool) -> str:
    lut = edit.get("lut") or {}
    return json.dumps([edit["geometry"], crop_mode, lut if lut.get("position") == "start" else None],
                      sort_keys=True, default=str)


def _stats_key(edit: dict) -> str:
    return json.dumps([edit["underwater"], edit["white_balance"], edit["light"].get("exposure_ev"),
                       edit["details"].get("dehaze"), edit["geometry"]], sort_keys=True, default=str)


def region_margin(edit: dict, ctx: RenderContext) -> int:
    """Pixels of context around a region so neighbourhood filters see the same data."""
    d = edit["details"]
    margin = REGION_MARGIN_MIN
    if d.get("clarity"):
        margin += int(3 * ctx.rel(details_stage.CLARITY_RADIUS_REL))
    if d.get("dehaze"):
        margin += int(3 * ctx.rel(dehaze_stage.RADIUS_REL)) + 2
    if d.get("sharpen_amount"):
        margin += int(4 * ctx.px(float(d.get("sharpen_radius", 1.0))))
    if d.get("denoise"):
        margin += int(4 * ctx.px(3.0))
    return margin


class PreviewEngine(QObject):
    rendered = Signal(object)        # RenderResult
    region_rendered = Signal(object)  # RegionResult
    failed = Signal(str)             # i18n key

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._preload_pool = QThreadPool(self)
        self._preload_pool.setMaxThreadCount(1)
        self._state = _State()  # render thread only
        self._preloaded: OrderedDict[tuple, tuple[np.ndarray, tuple[int, int]]] = OrderedDict()
        self._generation = 0
        self._path: Path | None = None
        self._mtime = 0.0
        self._loader = None  # callable(max_side | None) -> (float RGB, full size); None = photo file
        self._key = None
        self._target = 1600
        self._busy = False
        self._pending: dict | None = None
        self._pending_region: dict | None = None
        self._request_id = 0
        self.last_final: RenderResult | None = None

    # ------------------------------------------------------------------ configuration
    @property
    def generation(self) -> int:
        return self._generation

    def set_source(self, path: Path | None, mtime: float = 0.0, loader=None, key=None) -> None:
        """A photo file, or any image ``loader`` (e.g. a paused video frame) identified by ``key``."""
        if path == self._path and mtime == self._mtime and key == self._key:
            return
        self._path, self._mtime, self._loader, self._key = path, mtime, loader, key
        self._generation += 1
        self._pending = None
        self._pending_region = None
        self.last_final = None

    def set_target_size(self, max_side: int) -> None:
        max_side = int(max(512, min(4096, max_side)))
        if abs(max_side - self._target) / self._target > 0.2:
            self._target = max_side
            self._generation += 1  # forces a re-decode at the new size
            self._pending_region = None

    @property
    def target_size(self) -> int:
        return self._target

    # ------------------------------------------------------------------ requests
    def request(self, edit: dict, interactive: bool = False, crop_mode: bool = False) -> int:
        if self._path is None:
            return -1
        self._request_id += 1
        self._pending = {"edit": copy.deepcopy(edit), "draft": interactive, "crop_mode": crop_mode,
                         "id": self._request_id}
        self._kick()
        return self._request_id

    def request_region(self, edit: dict, rect: tuple[int, int, int, int], crop_mode: bool, stats: dict) -> int:
        if self._path is None:
            return -1
        self._request_id += 1
        self._pending_region = {"edit": copy.deepcopy(edit), "rect": rect, "crop_mode": crop_mode,
                                "stats": stats, "id": self._request_id}
        self._kick()
        return self._request_id

    def _kick(self) -> None:
        if self._busy:
            return
        if self._pending is not None:
            req, self._pending = self._pending, None
            self._start(self._render_job, req, self._on_rendered)
        elif self._pending_region is not None:
            req, self._pending_region = self._pending_region, None
            self._start(self._region_job, req, self._on_region)

    def _start(self, job, req: dict, on_done) -> None:
        self._busy = True
        req = {**req, "generation": self._generation, "path": self._path, "target": self._target,
               "loader": self._loader}
        preloaded = None if self._loader else self._preloaded.get((self._path, self._mtime, self._target))

        def work():
            return job(req, preloaded)

        def done(result) -> None:
            self._busy = False
            if result is not None and result.generation == self._generation:
                on_done(result)
            self._kick()

        def failed(details: str) -> None:
            self._busy = False
            log.error("preview render failed: %s", details.strip().splitlines()[-1] if details else "")
            if req["generation"] == self._generation:
                self.failed.emit("viewer.unreadable")
            self._kick()

        run_task(work, on_done=done, on_failed=failed, pool=self._pool)

    def _on_rendered(self, result: RenderResult) -> None:
        if not result.draft:
            self.last_final = result
        self.rendered.emit(result)

    def _on_region(self, result: RegionResult) -> None:
        self.region_rendered.emit(result)

    # ------------------------------------------------------------------ render thread
    def _ensure_source(self, req: dict, preloaded) -> _State:
        st = self._state
        if st.generation != req["generation"] or st.path != req["path"]:
            st.reset(req["generation"], req["path"], req["target"])
        if st.src is None:
            if preloaded is not None:
                st.src, st.full_size = preloaded
            elif req.get("loader") is not None:
                st.src, st.full_size = req["loader"](req["target"])
            else:
                st.src, st.full_size = load_photo(req["path"], max_side=req["target"])
            h, w = st.src.shape[:2]
            if max(h, w) > DRAFT_MAX_SIDE:
                f = DRAFT_MAX_SIDE / max(h, w)
                st.draft = cv2.resize(st.src, (round(w * f), round(h * f)), interpolation=cv2.INTER_AREA)
            else:
                st.draft = st.src
        return st

    def _render_job(self, req: dict, preloaded) -> RenderResult:
        t0 = time.perf_counter()
        st = self._ensure_source(req, preloaded)
        edit, crop_mode, draft = req["edit"], req["crop_mode"], req["draft"]
        src = st.draft if draft else st.src
        full_w, full_h = st.full_size
        proc = (src.shape[1], src.shape[0])
        key = _geometry_key(edit, crop_mode)
        if st.geom_key.get(draft) != key:
            ctx = pl.make_context(st.full_size, proc, edit, apply_crop=not crop_mode)
            st.geom[draft] = pl.process_geometry(src, edit, ctx)
            st.geom_key[draft] = key
            st.original[draft] = to_qimage(pl.to_uint8(pl.render_original(st.geom[draft])))
            st.prefix[draft] = {}
        geom = st.geom[draft]
        if not draft and st.stats_key != _stats_key(edit):
            st.stats = pl.global_stats(geom, edit)
            st.stats_key = _stats_key(edit)
        stats = st.stats if st.stats_key == _stats_key(edit) else {}
        ctx = pl.make_context(st.full_size, proc, edit, apply_crop=not crop_mode, stats=stats)
        out = pl.process_color(geom, edit, ctx, cache=st.prefix[draft])
        u8 = pl.to_uint8(out)
        result = RenderResult(
            generation=req["generation"],
            request_id=req["id"],
            draft=draft,
            image=to_qimage(u8),
            original=st.original[draft],
            overlay=to_qimage(clipping_overlay(u8)),
            hist=histogram(u8),
            full_size=geo.output_size(full_w, full_h, edit["geometry"], apply_crop=not crop_mode),
            scale=ctx.scale,
            crop_mode=crop_mode,
            stats=dict(ctx.stats),
            geom=geom,
            edit=edit,
        )
        result.seconds = time.perf_counter() - t0
        return result

    def _region_job(self, req: dict, preloaded) -> RegionResult | None:
        st = self._ensure_source(req, preloaded)
        edit, crop_mode = req["edit"], req["crop_mode"]
        key = _geometry_key(edit, crop_mode)
        if st.full_geom_key != key:
            st.full_geom = None  # free memory before decoding again
            loader = req.get("loader")
            full_src, full_size = loader(None) if loader is not None else load_photo(req["path"])
            ctx = pl.make_context(full_size, full_size, edit, apply_crop=not crop_mode)
            st.full_geom = pl.process_geometry(full_src, edit, ctx)
            st.full_geom_key = key
            del full_src
        geom = st.full_geom
        h, w = geom.shape[:2]
        x, y, rw, rh = req["rect"]
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(w, int(math.ceil(x + rw))), min(h, int(math.ceil(y + rh)))
        if x1 <= x0 or y1 <= y0:
            return None
        ctx = pl.make_context(st.full_size, st.full_size, edit, apply_crop=not crop_mode, stats=req["stats"])
        m = region_margin(edit, ctx)
        mx0, my0, mx1, my1 = max(0, x0 - m), max(0, y0 - m), min(w, x1 + m), min(h, y1 + m)
        out = pl.process_color(geom[my0:my1, mx0:mx1], edit, ctx)
        out = out[y0 - my0 : y0 - my0 + (y1 - y0), x0 - mx0 : x0 - mx0 + (x1 - x0)]
        original = pl.render_original(geom[y0:y1, x0:x1])
        return RegionResult(req["generation"], req["id"], to_qimage(pl.to_uint8(out)), (x0, y0, x1 - x0, y1 - y0),
                            to_qimage(pl.to_uint8(original)))

    def drop_full_resolution(self) -> None:
        """Free the full-resolution cache (leaving the 100 % view)."""
        def work():
            self._state.full_geom = None
            self._state.full_geom_key = None

        run_task(work, pool=self._pool)

    # ------------------------------------------------------------------ preload
    def preload(self, path: Path | None, mtime: float = 0.0) -> None:
        """Decode the next file in the background so switching to it is instant."""
        if path is None:
            return
        key = (path, mtime, self._target)
        if key in self._preloaded:
            return
        target = self._target

        def work():
            return load_photo(path, max_side=target)

        def done(result) -> None:
            self._preloaded[key] = result
            while len(self._preloaded) > PRELOAD_SLOTS:
                self._preloaded.popitem(last=False)

        run_task(work, on_done=done, pool=self._preload_pool)

    def wait(self, timeout_ms: int = 10000) -> None:
        self._pool.waitForDone(timeout_ms)
        self._preload_pool.waitForDone(timeout_ms)

    @property
    def busy(self) -> bool:
        return self._busy or self._pending is not None or self._pending_region is not None
