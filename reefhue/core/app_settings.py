"""Schema of the app-wide user settings (stored via QSettings in the UI layer).

This module defines keys, defaults and validation only – no Qt. Values coming
back from QSettings can be strings on some platforms (e.g. the Windows
registry), so every read goes through :func:`coerce`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LANGUAGE_SYSTEM = "system"
THEME_CHOICES = ("system", "light", "dark")
PREVIEW_QUALITY_CHOICES = ("auto", "50", "25")
EXPORT_MODE_CHOICES = ("suffix", "subfolder")  # "overwrite" is never a default (SPEC §14)
VIDEO_QUALITY_CHOICES = ("draft", "high", "very_high")
VIEW_MODE_CHOICES = ("list", "grid")
RECENT_LIMIT = 10

# Characters not allowed in a suffix / subfolder name (Windows is the strictest).
_FORBIDDEN_NAME_CHARS = set('<>:"/\\|?*') | {chr(c) for c in range(32)}


@dataclass(frozen=True)
class Setting:
    key: str
    default: Any
    kind: type
    choices: tuple | None = None
    minimum: int | None = None
    maximum: int | None = None
    validator: Any = field(default=None, compare=False)


def is_valid_name_part(value: str) -> bool:
    """True if ``value`` can be used as a file-name suffix or a single folder name."""
    if not value or value.strip() != value:
        return False
    if value in {".", ".."}:
        return False
    return not any(ch in _FORBIDDEN_NAME_CHARS for ch in value)


SETTINGS: dict[str, Setting] = {
    s.key: s
    for s in (
        Setting("ui/language", LANGUAGE_SYSTEM, str),
        Setting("ui/theme", "system", str, choices=THEME_CHOICES),
        Setting("preview/quality", "auto", str, choices=PREVIEW_QUALITY_CHOICES),
        Setting("export/mode", "suffix", str, choices=EXPORT_MODE_CHOICES),
        Setting("export/suffix", "_edited", str, validator=is_valid_name_part),
        Setting("export/subfolder", "edited", str, validator=is_valid_name_part),
        Setting("export/jpeg_quality", 95, int, minimum=1, maximum=100),
        Setting("export/video_quality", "high", str, choices=VIDEO_QUALITY_CHOICES),
        Setting("export/hw_encoder", False, bool),
        Setting("export/keep_telemetry", True, bool),
        Setting("thumbs/size", 128, int, minimum=64, maximum=320),
        Setting("ui/edit_panel_visible", True, bool),
        Setting("ui/histogram_visible", True, bool),
        Setting("ui/curves_visible", True, bool),
        Setting("ui/info_overlay", False, bool),
        Setting("files/view_mode", "list", str, choices=VIEW_MODE_CHOICES),
        Setting("files/filters_visible", True, bool),
        Setting("files/auto_refresh", False, bool),
        Setting("files/sort", "", str),
    )
}


def _to_bool(raw: Any) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in {"true", "1", "yes", "on"}:
            return True
        if low in {"false", "0", "no", "off"}:
            return False
    return None


def coerce(key: str, raw: Any) -> Any:
    """Return a valid value for ``key`` from ``raw``; fall back to the default."""
    spec = SETTINGS[key]
    if raw is None:
        return spec.default
    value: Any
    try:
        if spec.kind is bool:
            value = _to_bool(raw)
        elif spec.kind is int:
            if isinstance(raw, bool):
                value = None
            else:
                value = int(float(raw))
        else:
            value = str(raw)
    except (TypeError, ValueError):
        value = None
    if value is None:
        return spec.default
    if spec.choices is not None and value not in spec.choices:
        return spec.default
    if spec.kind is int:
        if spec.minimum is not None:
            value = max(spec.minimum, value)
        if spec.maximum is not None:
            value = min(spec.maximum, value)
    if spec.validator is not None and not spec.validator(value):
        return spec.default
    return value


def default(key: str) -> Any:
    return SETTINGS[key].default


def push_recent(recent: list[str], folder: str, limit: int = RECENT_LIMIT) -> list[str]:
    """Return a new recent-folders list with ``folder`` first, de-duplicated, capped."""
    result = [folder] + [f for f in recent if f != folder]
    return result[:limit]


def coerce_recent(raw: Any) -> list[str]:
    """QSettings may return None, a single string, or a list for string lists."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    try:
        items = [str(x) for x in raw if x]
    except TypeError:
        return []
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen[:RECENT_LIMIT]
