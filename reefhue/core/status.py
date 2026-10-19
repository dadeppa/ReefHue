"""Derived file status (SPEC §13). Status is never stored. No Qt."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from reefhue.pipeline.params import default_edit

UNEDITED = "unedited"
EDITED = "edited"
EXPORTED = "exported"
CHANGED_AFTER_EXPORT = "changed_after_export"
MISSING = "missing"
ALL_STATUSES = (UNEDITED, EDITED, EXPORTED, CHANGED_AFTER_EXPORT, MISSING)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _normalize_numbers(value: Any) -> Any:
    """Make ``1`` and ``1.0`` hash identically (JSON round-trips can change int/float)."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        as_float = float(value)
        return int(as_float) if as_float.is_integer() else round(as_float, 9)
    if isinstance(value, dict):
        return {k: _normalize_numbers(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_numbers(v) for v in value]
    return value


def settings_hash(entry: dict) -> str:
    """Hash over ``edit``, ``video.segment`` and ``export_options``."""
    payload = {
        "edit": entry.get("edit"),
        "segment": (entry.get("video") or {}).get("segment"),
        "export_options": entry.get("export_options"),
    }
    return hashlib.sha256(_canonical(_normalize_numbers(payload)).encode("utf-8")).hexdigest()


def is_unedited(entry: dict) -> bool:
    segment = (entry.get("video") or {}).get("segment")
    return segment is None and _normalize_numbers(entry.get("edit")) == _normalize_numbers(default_edit())


def last_successful_export(entry: dict) -> dict | None:
    for record in reversed(entry.get("export_history") or []):
        if isinstance(record, dict) and record.get("result") == "ok":
            return record
    return None


def derive_status(entry: dict, exists: bool = True) -> str:
    if not exists:
        return MISSING
    last = last_successful_export(entry)
    if last is not None:
        if last.get("settings_hash") == settings_hash(entry):
            return EXPORTED
        # After "overwrite original" the edit is reset to neutral (SPEC §14);
        # the overwritten file then counts as exported until it is edited again.
        if last.get("mode") == "overwrite" and is_unedited(entry):
            return EXPORTED
        return CHANGED_AFTER_EXPORT
    return UNEDITED if is_unedited(entry) else EDITED
