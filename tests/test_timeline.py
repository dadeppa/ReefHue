"""Time shift, wall-clock parsing and GoPro validation (SPEC §5)."""

from __future__ import annotations

from datetime import datetime

import pytest

from reefhue.core import timeline
from reefhue.core.timeline import TimedFile
from reefhue.core.timeutil import format_wallclock, parse_wallclock


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2024:05:01 10:11:12", datetime(2024, 5, 1, 10, 11, 12)),
        ("2024-05-01T10:11:12", datetime(2024, 5, 1, 10, 11, 12)),
        ("2024-05-01T10:11:12.000000Z", datetime(2024, 5, 1, 10, 11, 12)),  # Z dropped, not converted
        ("2024-05-01T10:11:12+0200", datetime(2024, 5, 1, 10, 11, 12)),  # offset dropped
        ("2024-05-01 10:11", datetime(2024, 5, 1, 10, 11)),
        ("2024-05-01T10:11:12.5", datetime(2024, 5, 1, 10, 11, 12, 500000)),
        ("0000:00:00 00:00:00", None),
        ("", None),
        (None, None),
        ("garbage", None),
    ],
)
def test_parse_wallclock(text, expected):
    assert parse_wallclock(text) == expected


def test_format_wallclock():
    assert format_wallclock(datetime(2024, 5, 1, 10, 0, 0)) == "2024-05-01T10:00:00"
    assert format_wallclock(None) is None


def test_shift_plan_keeps_relative_spacing():
    current = {
        "a": datetime(2016, 1, 1, 0, 0, 0),
        "b": datetime(2016, 1, 1, 0, 10, 30),
        "c": datetime(2016, 1, 1, 1, 0, 0),
    }
    plan = timeline.shift_plan(current, "b", datetime(2026, 10, 17, 9, 12, 0))
    assert plan["b"] == datetime(2026, 10, 17, 9, 12, 0)
    assert plan["a"] == datetime(2026, 10, 17, 9, 1, 30)
    assert plan["c"] == datetime(2026, 10, 17, 10, 1, 30)
    assert plan["c"] - plan["a"] == current["c"] - current["a"]


def test_shift_plan_backwards():
    current = {"a": datetime(2026, 1, 1, 12), "b": datetime(2026, 1, 1, 13)}
    plan = timeline.shift_plan(current, "a", datetime(2026, 1, 1, 11))
    assert plan == {"a": datetime(2026, 1, 1, 11), "b": datetime(2026, 1, 1, 12)}


@pytest.mark.parametrize(
    "name, number, chapter, enc",
    [
        ("GX010001.MP4", 1, 1, "X"),
        ("GX020001.mp4", 1, 2, "X"),
        ("GH011234.MP4", 1234, 1, "H"),
        ("GL010001.LRV", 1, 1, "L"),
        ("GOPR0042.MP4", 42, 1, None),
        ("GOPR0042.JPG", 42, 1, None),
        ("GP010042.MP4", 42, 2, None),
        ("GXAA0001.MP4", 1, 1, "X"),
        ("GXAB0001.MP4", 1, 2, "X"),
    ],
)
def test_parse_gopro_name(name, number, chapter, enc):
    parsed = timeline.parse_gopro_name(name)
    assert parsed == timeline.GoProName(number, chapter, enc)


@pytest.mark.parametrize("name", ["IMG_0001.JPG", "GX01001.MP4", "DJI_0001.MP4", "G0010001.JPG", "gopro.mp4"])
def test_non_gopro_names(name):
    assert timeline.parse_gopro_name(name) is None


def _f(i, name, dt, dur=None):
    return TimedFile(i, name, dt, dur)


def test_order_conflict_detected():
    files = [
        _f("1", "GX010001.MP4", datetime(2026, 5, 1, 10)),
        _f("2", "GX010002.MP4", datetime(1970, 1, 1, 0, 5)),  # clock reset after battery change
        _f("3", "GX010003.MP4", datetime(1970, 1, 1, 0, 20)),
    ]
    conflicts = timeline.gopro_order_conflicts(files)
    assert [(c.earlier.id, c.later.id) for c in conflicts] == [("1", "2")]


def test_no_conflict_when_ordered():
    files = [_f(str(i), f"GX01000{i}.MP4", datetime(2026, 5, 1, 10, i)) for i in range(1, 5)]
    assert timeline.gopro_order_conflicts(files) == []


def test_propose_groups_splits_at_clock_reset():
    files = [
        _f("1", "GX010001.MP4", datetime(2026, 5, 1, 10)),
        _f("2", "GX010002.MP4", datetime(2026, 5, 1, 11)),
        _f("3", "GX010003.MP4", datetime(1970, 1, 1, 0, 5)),
        _f("4", "GX010004.MP4", datetime(1970, 1, 1, 0, 30)),
        _f("p", "IMG_0001.JPG", datetime(2026, 5, 1, 10, 30)),  # non-GoPro joins by time
    ]
    groups = timeline.propose_groups(files)
    assert [sorted(g.file_ids) for g in groups] == [["1", "2", "p"], ["3", "4"]]
    assert groups[1].reason == "time_backwards"


def test_propose_groups_split_on_implausible_forward_jump():
    files = [
        _f("1", "GX010001.MP4", datetime(2000, 1, 1, 0, 1)),
        _f("2", "GX010002.MP4", datetime(2026, 5, 1, 9)),
    ]
    groups = timeline.propose_groups(files)
    assert [g.file_ids for g in groups] == [["1"], ["2"]]
    assert groups[1].reason == "implausible_date"


def test_propose_groups_single_group_without_gopro():
    files = [_f("a", "IMG_1.JPG", datetime(2026, 1, 1)), _f("b", "IMG_2.JPG", datetime(2025, 1, 1))]
    assert [g.file_ids for g in timeline.propose_groups(files)] == [["a", "b"]]


def test_derive_chapter_starts():
    start = datetime(2026, 5, 1, 10, 0, 0)
    files = [
        _f("c1", "GX010007.MP4", start, 511.5),
        _f("c2", "GX020007.MP4", start, 511.5),  # all chapters carry the recording start
        _f("c3", "GX030007.MP4", start, 100.0),
        _f("o", "GX010008.MP4", datetime(2026, 5, 1, 11), 10.0),
    ]
    derived = timeline.derive_chapter_starts(files)
    assert derived == {
        "c2": datetime(2026, 5, 1, 10, 8, 31),
        "c3": datetime(2026, 5, 1, 10, 17, 3),
    }


def test_derive_chapter_starts_skips_incomplete_recordings():
    files = [_f("c2", "GX020007.MP4", datetime(2026, 5, 1), 10.0)]  # chapter 1 not selected
    assert timeline.derive_chapter_starts(files) == {}
