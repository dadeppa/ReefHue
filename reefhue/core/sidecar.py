"""Sidecar project file ``reefhue.project.json`` (SPEC §13). No Qt.

* Missing fields are filled from defaults; unknown fields are preserved.
* A file with a higher ``schema_version`` is opened read-only.
* Saving is atomic (temp file + rename) and keeps one ``.bak`` of the previous version.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reefhue import APP_NAME, __version__
from reefhue.core import extensions
from reefhue.core.timeutil import now_wallclock
from reefhue.pipeline.params import default_edit

log = logging.getLogger(__name__)

SIDECAR_NAME = "reefhue.project.json"
BACKUP_SUFFIX = ".bak"
TMP_SUFFIX = ".tmp"
SCHEMA_VERSION = 1

# Migrations from version N to N+1: {N: callable(data) -> data}. None yet.
MIGRATIONS: dict[int, Any] = {}


def sidecar_path(folder: Path) -> Path:
    return folder / SIDECAR_NAME


def backup_path(path: Path) -> Path:
    return path.with_name(path.name + BACKUP_SUFFIX)


def new_id() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- defaults
def default_export_defaults() -> dict[str, Any]:
    return {
        "mode": "suffix",
        "suffix": "_edited",
        "subfolder": "edited",
        "jpeg_quality": 95,
        "video_quality": "high",
        "hw_encoder": False,
        "keep_telemetry": True,
    }


def default_project_section() -> dict[str, Any]:
    now = now_wallclock()
    return {
        "created": now,
        "modified": now,
        "recursive": False,
        "hide_exported": True,
        "extension_filter": list(extensions.DEFAULT_FILTER),
        "export_defaults": default_export_defaults(),
        "time_groups": [],
    }


def default_media() -> dict[str, Any]:
    return {
        "type": None,
        "codec": None,
        "pix_fmt": None,
        "bit_depth": None,
        "width": None,
        "height": None,
        "fps": None,
        "duration_s": None,
        "color_transfer": None,
        "color_primaries": None,
        "rotation_meta": 0,
        "camera": None,
        "has_audio": False,
        "has_telemetry": False,
    }


def default_file_entry(path: str = "") -> dict[str, Any]:
    return {
        "id": new_id(),
        "path": path,
        "size": 0,
        "mtime": "",
        "fingerprint": {"algo": "head-tail-sha1", "value": ""},
        "media": default_media(),
        "source_profile": {"value": "standard", "auto_detected": True},
        "capture": {
            "datetime_original": None,
            "datetime_source": None,
            "datetime_override": None,
            "override_reason": None,
            "time_group_id": None,
        },
        "edit": default_edit(),
        "video": {"segment": None},
        "export_options": {"keep_audio": True, "keep_telemetry": True},
        "export_history": [],
    }


def new_project() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "app": {"name": APP_NAME, "version": __version__},
        "project": default_project_section(),
        "files": [],
        "derived_files": [],
    }


# --------------------------------------------------------------------------- merging
def fill_defaults(target: dict, defaults: dict) -> dict:
    """Add keys missing in ``target`` from ``defaults`` (recursively). Never removes keys.

    A value of the wrong container type (e.g. a string where a dict is expected)
    is replaced by the default, so later code can rely on the structure.
    """
    for key, default in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(default)
        elif isinstance(default, dict):
            if isinstance(target[key], dict):
                fill_defaults(target[key], default)
            else:
                log.warning("sidecar: replacing malformed field %r", key)
                target[key] = copy.deepcopy(default)
    return target


def normalize_file_entry(entry: dict) -> dict:
    if not entry.get("id"):
        entry["id"] = new_id()
    template = default_file_entry(entry.get("path", ""))
    template.pop("id")
    fill_defaults(entry, template)
    if not isinstance(entry.get("export_history"), list):
        entry["export_history"] = []
    return entry


def normalize_project(data: dict) -> dict:
    fill_defaults(data, new_project())
    if not isinstance(data.get("files"), list):
        data["files"] = []
    if not isinstance(data.get("derived_files"), list):
        data["derived_files"] = []
    data["files"] = [normalize_file_entry(f) for f in data["files"] if isinstance(f, dict) and f.get("path")]
    data["derived_files"] = [d for d in data["derived_files"] if isinstance(d, dict) and d.get("path")]
    if not isinstance(data["project"].get("time_groups"), list):
        data["project"]["time_groups"] = []
    return data


def migrate(data: dict) -> dict:
    version = int(data.get("schema_version") or 1)
    while version < SCHEMA_VERSION:
        data = MIGRATIONS[version](data)
        version += 1
    data["schema_version"] = max(version, int(data.get("schema_version") or 1))
    return data


# --------------------------------------------------------------------------- load / save
@dataclass
class LoadResult:
    data: dict
    exists: bool
    read_only: bool = False
    warnings: list[str] = field(default_factory=list)  # i18n keys


def load(folder: Path) -> LoadResult:
    """Load the sidecar of ``folder``; a fresh in-memory project if there is none.

    Warnings are i18n keys: ``sidecar.warn.newer_schema``, ``sidecar.warn.corrupt``.
    """
    path = sidecar_path(folder)
    if not path.exists():
        return LoadResult(new_project(), exists=False)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("top level is not an object")
    except (OSError, ValueError) as exc:
        log.error("cannot read sidecar %s: %s", path, exc)
        # Never overwrite a file we could not understand: open read-only.
        return LoadResult(new_project(), exists=True, read_only=True, warnings=["sidecar.warn.corrupt"])
    version = raw.get("schema_version", 1)
    try:
        version = int(version)
    except (TypeError, ValueError):
        version = 1
    if version > SCHEMA_VERSION:
        log.warning("sidecar %s has schema %s > %s: read-only", path, version, SCHEMA_VERSION)
        return LoadResult(normalize_project(raw), exists=True, read_only=True, warnings=["sidecar.warn.newer_schema"])
    return LoadResult(normalize_project(migrate(raw)), exists=True)


def dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def save(folder: Path, data: dict) -> Path:
    """Atomically write the sidecar; the previous version is kept as ``.bak``."""
    path = sidecar_path(folder)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    payload = dumps(data)
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        if path.exists():
            bak = backup_path(path)
            bak_tmp = bak.with_name(bak.name + TMP_SUFFIX)
            bak_tmp.write_bytes(path.read_bytes())
            os.replace(bak_tmp, bak)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path
