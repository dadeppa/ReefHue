"""Sidecar load/save: round trip, defaults, unknown fields, schema version, atomic write, .bak."""

from __future__ import annotations

import json

import pytest

from reefhue.core import sidecar
from reefhue.pipeline.params import default_edit


def test_missing_sidecar_gives_fresh_project_without_writing(tmp_path):
    result = sidecar.load(tmp_path)
    assert not result.exists and not result.read_only
    assert result.data["schema_version"] == sidecar.SCHEMA_VERSION
    assert result.data["project"]["hide_exported"] is True
    assert result.data["project"]["recursive"] is False
    assert "jpg" in result.data["project"]["extension_filter"]
    assert list(tmp_path.iterdir()) == []


def test_round_trip(tmp_path):
    data = sidecar.new_project()
    entry = sidecar.default_file_entry("Day1/GX010001.MP4")
    entry["capture"]["datetime_override"] = "2026-10-17T09:12:00"
    entry["edit"]["light"]["exposure_ev"] = 0.35
    data["files"].append(entry)
    sidecar.save(tmp_path, data)
    loaded = sidecar.load(tmp_path)
    assert loaded.exists and not loaded.read_only
    assert loaded.data == data


def test_unknown_fields_are_preserved_and_missing_filled(tmp_path):
    raw = {
        "schema_version": 1,
        "future_top": {"x": 1},
        "project": {"recursive": True, "future_setting": "keep me"},
        "files": [
            {"id": "a", "path": "IMG_1.JPG", "edit": {"light": {"exposure_ev": 1.0}, "future_stage": {"k": 2}},
             "custom": [1, 2]},
        ],
    }
    (tmp_path / sidecar.SIDECAR_NAME).write_text(json.dumps(raw), encoding="utf-8")
    data = sidecar.load(tmp_path).data
    assert data["future_top"] == {"x": 1}
    assert data["project"]["future_setting"] == "keep me"
    assert data["project"]["recursive"] is True
    assert data["project"]["hide_exported"] is True  # filled
    entry = data["files"][0]
    assert entry["custom"] == [1, 2]
    assert entry["edit"]["future_stage"] == {"k": 2}
    assert entry["edit"]["light"]["exposure_ev"] == 1.0
    assert entry["edit"]["light"]["contrast"] == 0  # filled from registry defaults
    assert entry["edit"]["levels"] == default_edit()["levels"]
    sidecar.save(tmp_path, data)
    again = json.loads((tmp_path / sidecar.SIDECAR_NAME).read_text(encoding="utf-8"))
    assert again["future_top"] == {"x": 1}
    assert again["files"][0]["custom"] == [1, 2]


def test_malformed_sections_are_repaired(tmp_path):
    raw = {"schema_version": 1, "project": "oops", "files": [{"path": "a.jpg", "capture": None}, "junk", {"no": "path"}]}
    (tmp_path / sidecar.SIDECAR_NAME).write_text(json.dumps(raw), encoding="utf-8")
    data = sidecar.load(tmp_path).data
    assert isinstance(data["project"], dict) and data["project"]["hide_exported"] is True
    assert len(data["files"]) == 1
    assert data["files"][0]["capture"]["datetime_override"] is None
    assert data["files"][0]["id"]


def test_missing_schema_version_is_treated_as_v1(tmp_path):
    (tmp_path / sidecar.SIDECAR_NAME).write_text('{"files": []}', encoding="utf-8")
    result = sidecar.load(tmp_path)
    assert not result.read_only
    assert result.data["schema_version"] == 1


def test_higher_schema_opens_read_only(tmp_path):
    (tmp_path / sidecar.SIDECAR_NAME).write_text('{"schema_version": 99, "files": []}', encoding="utf-8")
    result = sidecar.load(tmp_path)
    assert result.read_only
    assert "sidecar.warn.newer_schema" in result.warnings
    assert result.data["schema_version"] == 99  # never downgraded


def test_corrupt_sidecar_opens_read_only_and_is_untouched(tmp_path):
    path = tmp_path / sidecar.SIDECAR_NAME
    path.write_text("{ not json", encoding="utf-8")
    result = sidecar.load(tmp_path)
    assert result.read_only and "sidecar.warn.corrupt" in result.warnings
    assert path.read_text(encoding="utf-8") == "{ not json"


def test_migration_hook_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(sidecar, "SCHEMA_VERSION", 2)
    monkeypatch.setitem(sidecar.MIGRATIONS, 1, lambda d: {**d, "migrated": True})
    (tmp_path / sidecar.SIDECAR_NAME).write_text('{"schema_version": 1, "files": []}', encoding="utf-8")
    data = sidecar.load(tmp_path).data
    assert data["migrated"] is True
    assert data["schema_version"] == 2


def test_save_keeps_one_backup_and_leaves_no_temp(tmp_path):
    first = sidecar.new_project()
    first["project"]["recursive"] = False
    sidecar.save(tmp_path, first)
    assert not sidecar.backup_path(tmp_path / sidecar.SIDECAR_NAME).exists()
    second = json.loads(json.dumps(first))
    second["project"]["recursive"] = True
    sidecar.save(tmp_path, second)
    third = json.loads(json.dumps(second))
    third["project"]["hide_exported"] = False
    sidecar.save(tmp_path, third)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [sidecar.SIDECAR_NAME, sidecar.SIDECAR_NAME + ".bak"]
    bak = json.loads((tmp_path / (sidecar.SIDECAR_NAME + ".bak")).read_text(encoding="utf-8"))
    assert bak == second
    assert sidecar.load(tmp_path).data == third


def test_failed_write_keeps_previous_file(tmp_path, monkeypatch):
    data = sidecar.new_project()
    sidecar.save(tmp_path, data)
    before = (tmp_path / sidecar.SIDECAR_NAME).read_text(encoding="utf-8")

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(sidecar.os, "replace", broken_replace)
    changed = json.loads(before)
    changed["project"]["recursive"] = True
    with pytest.raises(OSError):
        sidecar.save(tmp_path, changed)
    assert (tmp_path / sidecar.SIDECAR_NAME).read_text(encoding="utf-8") == before
    assert not (tmp_path / (sidecar.SIDECAR_NAME + sidecar.TMP_SUFFIX)).exists()
