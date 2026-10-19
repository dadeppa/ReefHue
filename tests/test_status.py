"""Derived status (SPEC §13)."""

from __future__ import annotations

import json

from reefhue.core import sidecar, status


def _entry():
    return sidecar.default_file_entry("a.jpg")


def _export(entry, mode="suffix", result="ok"):
    entry["export_history"].append(
        {"id": sidecar.new_id(), "mode": mode, "settings_hash": status.settings_hash(entry), "result": result}
    )


def test_unedited_and_edited():
    e = _entry()
    assert status.derive_status(e) == status.UNEDITED
    e["edit"]["color"]["saturation"] = 10
    assert status.derive_status(e) == status.EDITED


def test_segment_counts_as_edit():
    e = _entry()
    e["video"]["segment"] = {"start_s": 1.0, "end_s": 5.0}
    assert status.derive_status(e) == status.EDITED


def test_missing_wins():
    e = _entry()
    _export(e)
    assert status.derive_status(e, exists=False) == status.MISSING


def test_exported_then_changed_then_back():
    e = _entry()
    e["edit"]["light"]["exposure_ev"] = 0.5
    _export(e)
    assert status.derive_status(e) == status.EXPORTED
    e["edit"]["light"]["exposure_ev"] = 0.6
    assert status.derive_status(e) == status.CHANGED_AFTER_EXPORT
    e["edit"]["light"]["exposure_ev"] = 0.5
    assert status.derive_status(e) == status.EXPORTED


def test_export_options_are_part_of_hash():
    e = _entry()
    _export(e)
    e["export_options"]["keep_audio"] = False
    assert status.derive_status(e) == status.CHANGED_AFTER_EXPORT


def test_failed_export_is_ignored():
    e = _entry()
    e["edit"]["light"]["contrast"] = 5
    _export(e, result="failed")
    assert status.derive_status(e) == status.EDITED


def test_last_successful_export_counts():
    e = _entry()
    e["edit"]["light"]["contrast"] = 5
    _export(e)
    e["edit"]["light"]["contrast"] = 7
    _export(e, result="failed")
    assert status.derive_status(e) == status.CHANGED_AFTER_EXPORT


def test_overwrite_export_with_reset_edit_is_exported():
    e = _entry()
    e["edit"]["light"]["contrast"] = 5
    _export(e, mode="overwrite")
    e["edit"] = sidecar.default_file_entry()["edit"]  # reset after overwrite (SPEC §14)
    assert status.derive_status(e) == status.EXPORTED
    e["edit"]["light"]["contrast"] = 1
    assert status.derive_status(e) == status.CHANGED_AFTER_EXPORT


def test_hash_is_stable_across_json_round_trip_and_int_float():
    e = _entry()
    e["edit"]["details"]["sharpen_radius"] = 1.0
    h = status.settings_hash(e)
    again = json.loads(json.dumps(e))
    again["edit"]["details"]["sharpen_radius"] = 1
    assert status.settings_hash(again) == h
    assert status.is_unedited(again)


def test_hash_ignores_metadata_and_capture():
    e = _entry()
    h = status.settings_hash(e)
    e["capture"]["datetime_override"] = "2020-01-01T00:00:00"
    e["media"]["width"] = 100
    assert status.settings_hash(e) == h
