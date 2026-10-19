"""Local wall-clock datetimes (SPEC §5: no timezone conversion). No Qt."""

from __future__ import annotations

import re
from datetime import datetime

_EXIF = re.compile(r"^\s*(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
_ISO = re.compile(
    r"^\s*(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,9}))?)?"
    r"\s*(?:Z|[+-]\d{2}:?\d{2})?\s*$"
)


def parse_wallclock(value: str | None) -> datetime | None:
    """Parse EXIF (``2024:05:01 10:00:00``) or ISO-8601 text into a *naive* datetime.

    A trailing ``Z`` or UTC offset is dropped, not applied: cameras write their
    local clock and the spec treats every time as wall-clock time.
    """
    if not value:
        return None
    text = str(value).strip().strip("\x00")
    match = _EXIF.match(text)
    try:
        if match:
            parts = [int(p) for p in match.groups()]
            return datetime(*parts)
        match = _ISO.match(text)
        if match:
            y, mo, d, h, mi, s, frac = match.groups()
            micro = int((frac or "0")[:6].ljust(6, "0"))
            return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), micro)
    except ValueError:  # e.g. "0000:00:00 00:00:00"
        return None
    return None


def format_wallclock(value: datetime | None) -> str | None:
    if value is None:
        return None
    value = value.replace(tzinfo=None)
    return value.isoformat(timespec="microseconds" if value.microsecond else "seconds")


def now_wallclock() -> str:
    return format_wallclock(datetime.now().replace(microsecond=0)) or ""


def from_timestamp(ts: float) -> datetime:
    """Filesystem timestamp → local wall-clock time."""
    return datetime.fromtimestamp(ts).replace(microsecond=0)
