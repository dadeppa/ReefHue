from __future__ import annotations

import pytest

from reefhue.core import app_settings as s


@pytest.mark.parametrize(
    "key, raw, expected",
    [
        ("export/hw_encoder", "true", True),
        ("export/hw_encoder", "false", False),
        ("export/hw_encoder", "1", True),
        ("export/hw_encoder", "garbage", False),
        ("export/hw_encoder", None, False),
        ("export/keep_telemetry", 0, False),
        ("export/jpeg_quality", "90", 90),
        ("export/jpeg_quality", 250, 100),
        ("export/jpeg_quality", -3, 1),
        ("export/jpeg_quality", "abc", 95),
        ("export/jpeg_quality", True, 95),
        ("thumbs/size", "12", 64),
        ("ui/theme", "dark", "dark"),
        ("ui/theme", "purple", "system"),
        ("preview/quality", "50", "50"),
        ("preview/quality", "75", "auto"),
        ("export/mode", "overwrite", "suffix"),  # overwrite can never be a default
        ("export/suffix", "_v2", "_v2"),
        ("export/suffix", "a/b", "_edited"),
        ("export/suffix", "", "_edited"),
        ("export/subfolder", "..", "edited"),
        ("export/subfolder", "out:put", "edited"),
        ("export/video_quality", "very_high", "very_high"),
    ],
)
def test_coerce(key, raw, expected):
    assert s.coerce(key, raw) == expected


def test_defaults_are_valid():
    for key, spec in s.SETTINGS.items():
        assert s.coerce(key, spec.default) == spec.default


@pytest.mark.parametrize(
    "value, ok",
    [("_edited", True), ("edited", True), ("", False), (" x", False), ("a\\b", False), ("a?b", False), (".", False)],
)
def test_is_valid_name_part(value, ok):
    assert s.is_valid_name_part(value) is ok


def test_push_recent_dedupes_and_caps():
    recent = [f"/f{i}" for i in range(s.RECENT_LIMIT)]
    updated = s.push_recent(recent, "/f3")
    assert updated[0] == "/f3"
    assert updated.count("/f3") == 1
    assert len(updated) == s.RECENT_LIMIT
    updated = s.push_recent(recent, "/new")
    assert updated[0] == "/new"
    assert len(updated) == s.RECENT_LIMIT
    assert "/f9" not in updated


@pytest.mark.parametrize(
    "raw, expected",
    [(None, []), ("", []), ("/a", ["/a"]), (["/a", "/b", "/a", ""], ["/a", "/b"]), (5, [])],
)
def test_coerce_recent(raw, expected):
    assert s.coerce_recent(raw) == expected
