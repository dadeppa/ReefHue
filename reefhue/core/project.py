"""In-memory project: sidecar data + the files found on disk (SPEC §5, §13). No Qt.

* Every scanned file gets an in-memory item. Only *significant* entries (user
  state: edits, date overrides, exports, …) are written to the sidecar, and an
  entry that was written once is never removed (missing files stay "Missing").
* Renamed/moved files are re-matched via size + fingerprint (or size + mtime
  when no fingerprint was stored).
* All mutation happens on one thread (the UI thread); slow parts (scanning,
  fingerprints, metadata) are prepared by pure functions that run in workers.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from reefhue import __version__
from reefhue.core import extensions, scan, sidecar, status, timeline
from reefhue.core.scan import ScannedFile
from reefhue.core.timeutil import format_wallclock, from_timestamp, now_wallclock, parse_wallclock
from reefhue.pipeline.params import default_edit

MTIME_TOLERANCE = timedelta(seconds=2)  # FAT/exFAT (SD cards) store mtime with 2 s resolution
SOURCE_PROFILES = ("standard", "flat", "log", "hdr_hlg")
HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}  # HLG, PQ


def detect_source_profile(media: dict) -> str:
    """Only HDR/HLG can be recognised from metadata; Flat/Log are user-set (SPEC §10)."""
    return "hdr_hlg" if (media.get("color_transfer") or "") in HDR_TRANSFERS else "standard"


def format_mtime(mtime: float) -> str:
    return format_wallclock(from_timestamp(mtime)) or ""


@dataclass
class FileItem:
    entry: dict
    exists: bool = False
    in_scope: bool = True
    scanned: ScannedFile | None = None
    persisted: bool = False
    meta_ready: bool = False
    meta_error: str | None = None

    @property
    def id(self) -> str:
        return self.entry["id"]

    @property
    def rel_path(self) -> str:
        return self.entry["path"]

    @property
    def name(self) -> str:
        return self.rel_path.rsplit("/", 1)[-1]

    @property
    def subfolder(self) -> str:
        return self.rel_path.rsplit("/", 1)[0] if "/" in self.rel_path else ""

    @property
    def ext(self) -> str:
        return extensions.ext_of(self.rel_path)

    @property
    def kind(self) -> str:
        return extensions.kind_of(self.ext)

    @property
    def status(self) -> str:
        return status.derive_status(self.entry, self.exists)

    @property
    def capture_datetime(self) -> datetime | None:
        return timeline.effective_datetime(self.entry)

    @property
    def capture_overridden(self) -> bool:
        return timeline.is_overridden(self.entry)

    def timed(self) -> timeline.TimedFile:
        return timeline.TimedFile(self.id, self.name, self.capture_datetime, self.entry["media"].get("duration_s"))


# --------------------------------------------------------------------------- reconcile (worker side)
@dataclass
class ReconcilePlan:
    scanned: list[ScannedFile]
    recursive: bool
    matches: dict[str, ScannedFile] = field(default_factory=dict)  # persisted id -> file on disk
    renamed: dict[str, str] = field(default_factory=dict)          # persisted id -> new rel path
    missing: set[str] = field(default_factory=set)
    out_of_scope: set[str] = field(default_factory=set)
    new_files: list[ScannedFile] = field(default_factory=list)


def plan_reconcile(
    folder: Path,
    persisted: list[dict],
    scanned: list[ScannedFile],
    recursive: bool,
    fingerprint_fn: Callable[[Path, int], str] = scan.fingerprint,
) -> ReconcilePlan:
    """Match sidecar entries to files on disk. Pure apart from reading fingerprints."""
    plan = ReconcilePlan(scanned, recursive)
    by_path = {f.rel_path: f for f in scanned}
    taken: set[str] = set()
    orphans: list[dict] = []
    for entry in persisted:
        path = entry["path"]
        if path in by_path and path not in taken:
            plan.matches[entry["id"]] = by_path[path]
            taken.add(path)
        elif not recursive and "/" in path:
            plan.out_of_scope.add(entry["id"])
        else:
            orphans.append(entry)

    by_size: dict[int, list[ScannedFile]] = defaultdict(list)
    for f in scanned:
        if f.rel_path not in taken:
            by_size[f.size].append(f)
    fp_cache: dict[str, str | None] = {}

    def fp(f: ScannedFile) -> str | None:
        if f.rel_path not in fp_cache:
            try:
                fp_cache[f.rel_path] = fingerprint_fn(folder / f.rel_path, f.size)
            except OSError:
                fp_cache[f.rel_path] = None
        return fp_cache[f.rel_path]

    for entry in orphans:
        candidates = [f for f in by_size.get(int(entry.get("size") or -1), []) if f.rel_path not in taken]
        stored = ((entry.get("fingerprint") or {}).get("value")) or ""
        if stored:
            hits = [f for f in candidates if fp(f) == stored]
        else:
            old = parse_wallclock(entry.get("mtime"))
            hits = [f for f in candidates if old and abs(from_timestamp(f.mtime) - old) <= MTIME_TOLERANCE]
        if len(hits) == 1:
            plan.matches[entry["id"]] = hits[0]
            plan.renamed[entry["id"]] = hits[0].rel_path
            taken.add(hits[0].rel_path)
        else:
            plan.missing.add(entry["id"])
    plan.new_files = [f for f in scanned if f.rel_path not in taken]
    return plan


# --------------------------------------------------------------------------- project
@dataclass
class ShiftGroup:
    anchor_id: str
    new_anchor: datetime
    file_ids: list[str]


def is_significant(entry: dict) -> bool:
    capture = entry.get("capture") or {}
    return bool(
        not status.is_unedited(entry)
        or capture.get("datetime_override")
        or capture.get("time_group_id")
        or entry.get("export_history")
        or not (entry.get("source_profile") or {}).get("auto_detected", True)
        or entry.get("export_options") != {"keep_audio": True, "keep_telemetry": True}
    )


class Project:
    def __init__(self, folder: Path, loaded: sidecar.LoadResult) -> None:
        self.folder = folder
        self.data = loaded.data
        self.sidecar_exists = loaded.exists
        self.read_only = loaded.read_only
        self.warnings: list[str] = list(loaded.warnings)
        self.dirty = False
        self.revision = 0  # bumps on every visible change (UI refresh)
        self.save_revision = 0  # bumps only on changes that must be saved
        self.items: dict[str, FileItem] = {}
        self._by_path: dict[str, str] = {}
        for entry in self.data["files"]:
            if entry["id"] in self.items:
                entry["id"] = sidecar.new_id()  # repair duplicate ids
            self._add(FileItem(entry, persisted=True))
        self.data["project"]["extension_filter"] = extensions.normalize_filter(
            self.data["project"].get("extension_filter")
        )

    @classmethod
    def open(cls, folder: Path) -> "Project":
        return cls(folder, sidecar.load(folder))

    # ---- bookkeeping -------------------------------------------------------------
    def _add(self, item: FileItem) -> None:
        self.items[item.id] = item
        self._by_path[item.rel_path] = item.id

    def _reindex(self) -> None:
        self._by_path = {item.rel_path: item.id for item in self.items.values()}

    def item_by_path(self, rel_path: str) -> FileItem | None:
        item_id = self._by_path.get(rel_path)
        return self.items.get(item_id) if item_id else None

    def mark_dirty(self) -> None:
        self.revision += 1
        if self.read_only:
            return
        self.save_revision += 1
        self.dirty = True
        self.data["project"]["modified"] = now_wallclock()

    # ---- project settings --------------------------------------------------------
    @property
    def settings(self) -> dict:
        return self.data["project"]

    @property
    def recursive(self) -> bool:
        return bool(self.settings.get("recursive"))

    def set_recursive(self, value: bool) -> None:
        if bool(value) != self.recursive:
            self.settings["recursive"] = bool(value)
            self.mark_dirty()

    @property
    def hide_exported(self) -> bool:
        return bool(self.settings.get("hide_exported", True))

    def set_hide_exported(self, value: bool) -> None:
        if bool(value) != self.hide_exported:
            self.settings["hide_exported"] = bool(value)
            self.mark_dirty()

    @property
    def extension_filter(self) -> list[str]:
        return list(self.settings["extension_filter"])

    def set_extension_filter(self, exts: list[str]) -> None:
        exts = extensions.normalize_filter(exts)
        if exts != self.settings["extension_filter"]:
            self.settings["extension_filter"] = exts
            self.mark_dirty()

    @property
    def derived_paths(self) -> set[str]:
        return {d["path"] for d in self.data["derived_files"]}

    # ---- scanning ----------------------------------------------------------------
    def persisted_entries(self) -> list[dict]:
        return [copy.deepcopy(i.entry) for i in self.items.values() if i.persisted]

    def apply_reconcile(self, plan: ReconcilePlan) -> None:
        renamed_any = False
        for item_id, item in list(self.items.items()):
            if not item.persisted:
                continue
            if item_id in plan.matches:
                if item_id in plan.renamed:
                    item.entry["path"] = plan.renamed[item_id]
                    renamed_any = True
                item.exists, item.in_scope, item.scanned = True, True, plan.matches[item_id]
            elif item_id in plan.out_of_scope:
                item.exists, item.in_scope, item.scanned = False, False, None
            else:
                item.exists, item.in_scope, item.scanned = False, True, None
        # Transient items: keep the ones still on disk (keeps metadata), drop the rest.
        new_paths = {f.rel_path: f for f in plan.new_files}
        for item_id, item in list(self.items.items()):
            if not item.persisted and item.rel_path not in new_paths:
                del self.items[item_id]
        self._reindex()
        for f in plan.new_files:
            existing = self.item_by_path(f.rel_path)
            if existing is not None and not existing.persisted:
                existing.exists, existing.in_scope, existing.scanned = True, True, f
            elif existing is None:
                entry = sidecar.default_file_entry(f.rel_path)
                self._add(FileItem(entry, exists=True, scanned=f))
        self._reindex()
        if renamed_any:
            self.mark_dirty()
        else:
            self.revision += 1

    def needs_metadata(self, item: FileItem, ffprobe_available: bool) -> bool:
        if item.scanned is None:
            return False
        e = item.entry
        if not item.meta_ready:
            fresh = (
                e.get("size") == item.scanned.size
                and e.get("mtime") == format_mtime(item.scanned.mtime)
                and (e.get("capture") or {}).get("datetime_source")
            )
            if not fresh:
                return True
            item.meta_ready = True
        if ffprobe_available and item.kind == extensions.VIDEO and not e["media"].get("codec"):
            return True  # FFmpeg became available after the first look
        return False

    def apply_metadata(
        self,
        rel_path: str,
        size: int,
        mtime: float,
        media: dict,
        datetime_original: str | None,
        datetime_source: str | None,
        fingerprint: str | None,
        error: str | None = None,
    ) -> FileItem | None:
        item = self.item_by_path(rel_path)
        if item is None:
            return None
        e = item.entry
        before = copy.deepcopy((e["size"], e["mtime"], e["fingerprint"], e["media"], e["capture"], e["source_profile"]))
        e["size"] = size
        e["mtime"] = format_mtime(mtime)
        if fingerprint:
            e["fingerprint"] = {"algo": scan.FINGERPRINT_ALGO, "value": fingerprint}
        merged = sidecar.default_media()
        merged.update({k: v for k, v in (media or {}).items() if k in merged})
        if merged["type"] is None and item.kind != extensions.OTHER:
            merged["type"] = item.kind
        e["media"] = merged
        e["capture"]["datetime_original"] = datetime_original
        e["capture"]["datetime_source"] = datetime_source
        profile = e["source_profile"]
        if profile.get("auto_detected", True):
            profile["value"] = detect_source_profile(merged)
            profile["auto_detected"] = True
        item.meta_ready = True
        item.meta_error = error
        after = (e["size"], e["mtime"], e["fingerprint"], e["media"], e["capture"], e["source_profile"])
        if item.persisted and before != after:
            self.mark_dirty()
        else:
            self.revision += 1
        return item

    def meta_cache_records(self) -> dict[str, dict]:
        records = {}
        for item in self.items.values():
            if item.meta_ready and item.scanned is not None and not item.meta_error:
                e = item.entry
                records[item.rel_path] = {
                    "size": item.scanned.size,
                    "mtime": item.scanned.mtime,
                    "media": e["media"],
                    "datetime_original": e["capture"]["datetime_original"],
                    "datetime_source": e["capture"]["datetime_source"],
                    "fingerprint": e["fingerprint"].get("value"),
                }
        return records

    # ---- visibility ----------------------------------------------------------------
    def is_visible(self, item: FileItem) -> bool:
        if not item.in_scope or (not self.recursive and "/" in item.rel_path):
            return False
        if not item.exists and not item.persisted:
            return False
        if item.ext not in self.settings["extension_filter"]:
            return False
        if self.hide_exported and item.rel_path in self.derived_paths:
            return False
        return True

    def visible_items(self) -> list[FileItem]:
        return [i for i in self.items.values() if self.is_visible(i)]

    # ---- capture date ----------------------------------------------------------------
    def set_capture_override(self, item_id: str, value: datetime, reason: str = timeline.REASON_MANUAL) -> None:
        capture = self.items[item_id].entry["capture"]
        text = format_wallclock(value.replace(microsecond=0))
        if text == capture.get("datetime_original"):
            self.reset_capture([item_id])
            return
        capture["datetime_override"] = text
        capture["override_reason"] = reason
        capture["time_group_id"] = None
        self._prune_time_groups()
        self.mark_dirty()

    def reset_capture(self, item_ids: list[str]) -> None:
        for item_id in item_ids:
            capture = self.items[item_id].entry["capture"]
            capture["datetime_override"] = None
            capture["override_reason"] = None
            capture["time_group_id"] = None
        self._prune_time_groups()
        self.mark_dirty()

    def apply_time_shift(self, groups: list[ShiftGroup], base: dict[str, datetime] | None = None) -> None:
        """Apply one or more shift groups; each becomes a ``time_groups`` record (SPEC §5).

        ``base`` optionally replaces the current time of some files before shifting
        (e.g. GoPro chapter starts derived from durations).
        """
        base = base or {}
        for group in groups:
            current = {i: base.get(i) or self.items[i].capture_datetime for i in group.file_ids}
            current = {i: dt for i, dt in current.items() if dt is not None}
            if group.anchor_id not in current:
                continue
            plan = timeline.shift_plan(current, group.anchor_id, group.new_anchor)
            delta = timeline.delta_seconds(current[group.anchor_id], group.new_anchor)
            group_id = sidecar.new_id()
            self.settings["time_groups"].append(
                {
                    "id": group_id,
                    "anchor_file_id": group.anchor_id,
                    "anchor_datetime": format_wallclock(group.new_anchor),
                    "delta_seconds": delta,
                }
            )
            for item_id, new_dt in plan.items():
                capture = self.items[item_id].entry["capture"]
                text = format_wallclock(new_dt)
                capture["datetime_override"] = None if text == capture.get("datetime_original") else text
                capture["override_reason"] = timeline.REASON_TIME_SHIFT if capture["datetime_override"] else None
                capture["time_group_id"] = group_id
        self._prune_time_groups()
        self.mark_dirty()

    def _prune_time_groups(self) -> None:
        used = {i.entry["capture"].get("time_group_id") for i in self.items.values()}
        self.settings["time_groups"] = [g for g in self.settings["time_groups"] if g.get("id") in used]

    # ---- video segment, source profile, derived files --------------------------------------
    def set_segment(self, item_id: str, segment: dict | None) -> None:
        video = self.items[item_id].entry["video"]
        if video.get("segment") != segment:
            video["segment"] = copy.deepcopy(segment)
            self.mark_dirty()

    def set_source_profile(self, item_id: str, value: str) -> None:
        if value not in SOURCE_PROFILES:
            raise ValueError(value)
        profile = self.items[item_id].entry["source_profile"]
        if profile.get("value") != value or profile.get("auto_detected", True):
            profile["value"] = value
            profile["auto_detected"] = False
            self.mark_dirty()

    def add_derived(self, rel_path: str, source_id: str) -> None:
        """Record an export/snapshot (hidden by "Hide exported files")."""
        self.data["derived_files"].append({"path": rel_path, "source_id": source_id, "created": now_wallclock()})
        source = self.items.get(source_id)
        if source is not None:
            source.persisted = True  # keep the reference valid across sessions
        self.mark_dirty()

    def record_export(self, item_id: str, record: dict, target_rel: str | None) -> None:
        """Store an export result (SPEC §13/§14). ``target_rel`` is None for failed exports.

        Overwrite mode: the file itself now carries the edit, so ``edit`` and the segment
        are reset to neutral (the previous state stays in ``export_history``).
        """
        item = self.items.get(item_id)
        if item is None:
            return
        entry = item.entry
        entry["export_history"].append(copy.deepcopy(record))
        item.persisted = True
        if record.get("result") == "ok" and target_rel is not None:
            if record.get("mode") == "overwrite":
                entry["edit"] = default_edit()
                entry["video"]["segment"] = None
            elif target_rel not in self.derived_paths:
                self.data["derived_files"].append({"path": target_rel, "source_id": item_id, "created": now_wallclock()})
        self.mark_dirty()

    def last_export(self, item_id: str) -> dict | None:
        item = self.items.get(item_id)
        return status.last_successful_export(item.entry) if item else None

    # ---- saving ------------------------------------------------------------------------
    def snapshot(self) -> dict:
        """Data to write: persisted entries plus newly significant ones (which become persisted)."""
        for item in self.items.values():
            if not item.persisted and item.exists and is_significant(item.entry):
                item.persisted = True
        data = copy.deepcopy({k: v for k, v in self.data.items() if k != "files"})
        persisted = [i.entry for i in self.items.values() if i.persisted]
        data["files"] = copy.deepcopy(persisted)
        data["app"]["version"] = __version__
        return data

    def mark_saved(self, save_revision: int) -> None:
        """Call after a successful write of the snapshot taken at ``save_revision``."""
        self.sidecar_exists = True
        if save_revision == self.save_revision:
            self.dirty = False
