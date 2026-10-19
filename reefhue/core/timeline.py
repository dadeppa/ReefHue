"""Capture-date logic: overrides, timeline shift, GoPro validation (SPEC §5). No Qt."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import PurePath

from reefhue.core.timeutil import parse_wallclock

PLAUSIBLE_FROM_YEAR = 2005  # earlier dates are treated as a reset camera clock (e.g. 1970, 2000)

REASON_MANUAL = "manual"
REASON_TIME_SHIFT = "time_shift"


# --------------------------------------------------------------------------- effective date
def effective_datetime(entry: dict) -> datetime | None:
    capture = entry.get("capture") or {}
    return parse_wallclock(capture.get("datetime_override")) or parse_wallclock(capture.get("datetime_original"))


def is_overridden(entry: dict) -> bool:
    return bool((entry.get("capture") or {}).get("datetime_override"))


def is_plausible(dt: datetime | None) -> bool:
    return dt is not None and dt.year >= PLAUSIBLE_FROM_YEAR


# --------------------------------------------------------------------------- shift
def shift_plan(current: dict[str, datetime], anchor_id: str, new_anchor: datetime) -> dict[str, datetime]:
    """Move every file by the same delta so that the anchor gets ``new_anchor``.

    Relative spacing is kept exactly. ``current`` maps file id → effective datetime.
    """
    delta = new_anchor - current[anchor_id]
    return {file_id: dt + delta for file_id, dt in current.items()}


def delta_seconds(current: datetime, new: datetime) -> float:
    return (new - current).total_seconds()


# --------------------------------------------------------------------------- GoPro names
_NEW_SCHEME = re.compile(r"^G([HXL])(\d{2}|[A-Z]{2})(\d{4})$", re.IGNORECASE)  # GX010001
_OLD_FIRST = re.compile(r"^GOPR(\d{4})$", re.IGNORECASE)  # GOPR0001 (first chapter)
_OLD_CHAPTER = re.compile(r"^GP(\d{2})(\d{4})$", re.IGNORECASE)  # GP010001 (2nd chapter …)


@dataclass(frozen=True)
class GoProName:
    number: int   # recording number, increases over time
    chapter: int  # 1-based chapter index within the recording
    encoding: str | None  # "H" (AVC), "X" (HEVC), "L" (low-res proxy) or None (old scheme)


def parse_gopro_name(filename: str) -> GoProName | None:
    """``GX010001.MP4`` → recording 1, chapter 1, HEVC. ``None`` for non-GoPro names."""
    stem = PurePath(filename).stem
    match = _NEW_SCHEME.match(stem)
    if match:
        enc, chap, num = match.groups()
        if chap.isdigit():
            chapter = int(chap)
        else:  # looping mode uses letter pairs: AA, AB, …
            a, b = chap.upper()
            chapter = (ord(a) - 65) * 26 + (ord(b) - 65) + 1
        return GoProName(int(num), chapter, enc.upper())
    match = _OLD_FIRST.match(stem)
    if match:
        return GoProName(int(match.group(1)), 1, None)
    match = _OLD_CHAPTER.match(stem)
    if match:
        return GoProName(int(match.group(2)), int(match.group(1)) + 1, None)
    return None


@dataclass(frozen=True)
class TimedFile:
    id: str
    name: str
    dt: datetime | None
    duration_s: float | None = None

    @property
    def gopro(self) -> GoProName | None:
        return parse_gopro_name(self.name)


def _gopro_sorted(files: list[TimedFile]) -> list[TimedFile]:
    gp = [f for f in files if f.gopro is not None]
    return sorted(gp, key=lambda f: (f.gopro.number, f.gopro.chapter, f.name))


@dataclass(frozen=True)
class OrderConflict:
    earlier: TimedFile  # lower recording number …
    later: TimedFile    # … but an earlier capture time


def gopro_order_conflicts(files: list[TimedFile]) -> list[OrderConflict]:
    """Pairs where ordering by recording number contradicts ordering by time."""
    ordered = [f for f in _gopro_sorted(files) if f.dt is not None]
    return [
        OrderConflict(a, b) for a, b in zip(ordered, ordered[1:]) if b.dt < a.dt  # type: ignore[operator]
    ]


@dataclass
class GroupProposal:
    file_ids: list[str] = field(default_factory=list)
    reason: str | None = None  # "time_backwards" | "implausible_date" for groups after a split


def propose_groups(files: list[TimedFile]) -> list[GroupProposal]:
    """Split a selection at time jumps (SPEC §5 GoPro validation).

    GoPro files are walked in recording-number order; a new group starts where
    the time goes backwards or the date switches between plausible and
    implausible (clock reset to 1970 after a battery change). Other files join
    the group whose time range contains them, else the nearest one.
    """
    ordered = [f for f in _gopro_sorted(files) if f.dt is not None]
    groups: list[GroupProposal] = []
    previous: TimedFile | None = None
    for f in ordered:
        reason = None
        if previous is not None:
            if f.dt < previous.dt:  # type: ignore[operator]
                reason = "time_backwards"
            elif is_plausible(f.dt) != is_plausible(previous.dt):
                reason = "implausible_date"
        if previous is None or reason is not None:
            groups.append(GroupProposal(reason=reason))
        groups[-1].file_ids.append(f.id)
        previous = f
    if not groups:
        return [GroupProposal([f.id for f in files])]
    by_id = {f.id: f for f in files}
    ranges = []
    for g in groups:
        times = [by_id[i].dt for i in g.file_ids]
        ranges.append((min(times), max(times)))  # type: ignore[type-var]
    placed = set(fid for g in groups for fid in g.file_ids)
    for f in files:
        if f.id in placed:
            continue
        if f.dt is None:
            groups[0].file_ids.append(f.id)
            continue

        def distance(r: tuple[datetime, datetime]) -> float:
            lo, hi = r
            if lo <= f.dt <= hi:  # type: ignore[operator]
                return 0.0
            return min(abs((f.dt - lo).total_seconds()), abs((f.dt - hi).total_seconds()))  # type: ignore[operator]

        best = min(range(len(groups)), key=lambda i: distance(ranges[i]))
        groups[best].file_ids.append(f.id)
    return groups


def derive_chapter_starts(files: list[TimedFile]) -> dict[str, datetime]:
    """Chapter k of a recording starts at chapter 1's start + durations of chapters 1..k-1.

    Returns new start times for chapters ≥ 2 whose current time differs.
    Recordings with a missing chapter or duration are skipped.
    """
    recordings: dict[int, list[TimedFile]] = {}
    for f in _gopro_sorted(files):
        recordings.setdefault(f.gopro.number, []).append(f)  # type: ignore[union-attr]
    result: dict[str, datetime] = {}
    for chapters in recordings.values():
        first = chapters[0]
        if first.gopro.chapter != 1 or first.dt is None:  # type: ignore[union-attr]
            continue
        expected = [c.gopro.chapter for c in chapters]  # type: ignore[union-attr]
        if expected != list(range(1, len(chapters) + 1)):
            continue
        start = first.dt
        offset = 0.0
        for prev, chap in zip(chapters, chapters[1:]):
            if not prev.duration_s:
                break
            offset += prev.duration_s
            new = (start + timedelta(seconds=offset)).replace(microsecond=0)
            if chap.dt != new:
                result[chap.id] = new
    return result
