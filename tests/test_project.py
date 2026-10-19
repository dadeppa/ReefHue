"""Project model: scanning, reconcile/rename matching, visibility, persistence (SPEC §5, §13)."""

from __future__ import annotations

from datetime import datetime

from reefhue.core import scan, sidecar
from reefhue.core.project import Project, ShiftGroup, plan_reconcile
from reefhue.io import metadata
from tests.media import make_jpeg, make_png, set_mtime


def _open_and_scan(folder, recursive=None):
    project = Project.open(folder)
    if recursive is not None:
        project.set_recursive(recursive)
    files = scan.scan_folder(folder, project.recursive)
    project.apply_reconcile(plan_reconcile(folder, project.persisted_entries(), files, project.recursive))
    for item in list(project.items.values()):
        if project.needs_metadata(item, ffprobe_available=False):
            r = metadata.extract(folder, item.scanned, None)
            project.apply_metadata(r.rel_path, r.size, r.mtime, r.media, r.datetime_original,
                                   r.datetime_source, r.fingerprint, r.error)
    return project


def _save(project):
    data = project.snapshot()
    sidecar.save(project.folder, data)
    project.mark_saved(project.save_revision)


def test_scan_skips_hidden_and_own_files_and_respects_recursion(tmp_path):
    make_jpeg(tmp_path / "a.jpg")
    make_jpeg(tmp_path / "sub" / "b.jpg")
    (tmp_path / ".hidden.jpg").write_bytes(b"x")
    (tmp_path / "._a.jpg").write_bytes(b"x")
    (tmp_path / sidecar.SIDECAR_NAME).write_text("{}")
    (tmp_path / "Lib.photoslibrary").mkdir()
    make_jpeg(tmp_path / "Lib.photoslibrary" / "c.jpg")
    flat = [f.rel_path for f in scan.scan_folder(tmp_path, recursive=False)]
    deep = [f.rel_path for f in scan.scan_folder(tmp_path, recursive=True)]
    assert flat == ["a.jpg"]
    assert deep == ["a.jpg", "sub/b.jpg"]


def test_opening_does_not_write_into_folder(tmp_path):
    make_jpeg(tmp_path / "a.jpg")
    before = sorted(p.name for p in tmp_path.iterdir())
    project = _open_and_scan(tmp_path)
    assert len(project.visible_items()) == 1
    assert not project.dirty
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_metadata_from_exif(tmp_path):
    make_jpeg(tmp_path / "a.jpg", dt="2024:05:01 10:11:12", orientation=6)
    item = _open_and_scan(tmp_path).item_by_path("a.jpg")
    assert item.entry["capture"]["datetime_original"] == "2024-05-01T10:11:12"
    assert item.entry["capture"]["datetime_source"] == "exif"
    assert item.entry["media"]["camera"] == "GoPro HERO12 Black"
    assert item.entry["media"]["rotation_meta"] == 90
    assert item.entry["fingerprint"]["value"]


def test_filesystem_date_fallback(tmp_path):
    make_png(tmp_path / "a.png")
    ts = datetime(2023, 7, 8, 9, 10, 11).timestamp()
    set_mtime(tmp_path / "a.png", ts)
    item = _open_and_scan(tmp_path).item_by_path("a.png")
    assert item.entry["capture"]["datetime_source"] == "filesystem"
    assert item.entry["capture"]["datetime_original"] == "2023-07-08T09:10:11"


def test_extension_filter_and_unsupported_listed(tmp_path):
    make_jpeg(tmp_path / "a.jpg")
    (tmp_path / "b.raw").write_bytes(b"raw")
    (tmp_path / "GX010001.LRV").write_bytes(b"lrv")
    project = _open_and_scan(tmp_path)
    assert [i.name for i in project.visible_items()] == ["a.jpg"]  # .lrv off by default
    project.set_extension_filter(["jpg", "raw", "lrv"])
    names = sorted(i.name for i in project.visible_items())
    assert names == ["GX010001.LRV", "a.jpg", "b.raw"]
    assert project.item_by_path("b.raw").kind == "other"


def test_reopen_restores_all_settings(tmp_path):
    make_jpeg(tmp_path / "a.jpg", dt="2016:01:01 00:00:00")
    make_jpeg(tmp_path / "b.jpg", dt="2016:01:01 00:10:00")
    make_jpeg(tmp_path / "sub" / "c.jpg")
    project = _open_and_scan(tmp_path, recursive=True)
    project.set_hide_exported(False)
    project.set_extension_filter(["jpg", "png"])
    a = project.item_by_path("a.jpg")
    b = project.item_by_path("b.jpg")
    project.apply_time_shift([ShiftGroup(a.id, datetime(2026, 10, 17, 9, 0, 0), [a.id, b.id])])
    c = project.item_by_path("sub/c.jpg")
    project.set_capture_override(c.id, datetime(2025, 1, 2, 3, 4, 5))
    assert project.dirty
    _save(project)
    assert not project.dirty

    again = _open_and_scan(tmp_path)
    assert again.recursive is True
    assert again.hide_exported is False
    assert again.extension_filter == ["jpg", "png"]
    a2, b2, c2 = (again.item_by_path(p) for p in ("a.jpg", "b.jpg", "sub/c.jpg"))
    assert a2.id == a.id
    assert a2.capture_datetime == datetime(2026, 10, 17, 9, 0, 0)
    assert b2.capture_datetime == datetime(2026, 10, 17, 9, 10, 0)
    assert a2.capture_overridden and b2.entry["capture"]["override_reason"] == "time_shift"
    group = again.settings["time_groups"][0]
    assert group["anchor_file_id"] == a.id and group["delta_seconds"] > 0
    assert a2.entry["capture"]["time_group_id"] == group["id"] == b2.entry["capture"]["time_group_id"]
    assert c2.capture_datetime == datetime(2025, 1, 2, 3, 4, 5)
    assert c2.entry["capture"]["datetime_original"] == "2024-05-01T10:11:12"  # original kept


def test_reset_capture_restores_original(tmp_path):
    make_jpeg(tmp_path / "a.jpg")
    project = _open_and_scan(tmp_path)
    a = project.item_by_path("a.jpg")
    project.set_capture_override(a.id, datetime(2000, 1, 1))
    _save(project)
    project.reset_capture([a.id])
    assert not a.capture_overridden
    assert a.capture_datetime == datetime(2024, 5, 1, 10, 11, 12)
    # still persisted (once written, never dropped), override cleared
    _save(project)
    files = sidecar.load(tmp_path).data["files"]
    assert len(files) == 1 and files[0]["capture"]["datetime_override"] is None


def test_only_significant_entries_are_written(tmp_path):
    for n in range(3):
        make_jpeg(tmp_path / f"{n}.jpg")
    project = _open_and_scan(tmp_path)
    project.set_hide_exported(False)
    _save(project)
    assert sidecar.load(tmp_path).data["files"] == []
    one = project.item_by_path("1.jpg")
    one.entry["edit"]["light"]["exposure_ev"] = 0.5
    project.mark_dirty()
    _save(project)
    files = sidecar.load(tmp_path).data["files"]
    assert [f["path"] for f in files] == ["1.jpg"]


def test_missing_file_stays_in_sidecar(tmp_path):
    make_jpeg(tmp_path / "a.jpg")
    project = _open_and_scan(tmp_path)
    a = project.item_by_path("a.jpg")
    project.set_capture_override(a.id, datetime(2020, 1, 1))
    _save(project)
    (tmp_path / "a.jpg").unlink()
    again = _open_and_scan(tmp_path)
    item = again.items[a.id]
    assert item.status == "missing"
    assert item in again.visible_items()
    _save(again)
    assert [f["id"] for f in sidecar.load(tmp_path).data["files"]] == [a.id]


def test_renamed_file_is_rematched_by_fingerprint(tmp_path):
    make_jpeg(tmp_path / "a.jpg")
    make_jpeg(tmp_path / "other.jpg", dt="2020:01:01 00:00:00")
    project = _open_and_scan(tmp_path, recursive=True)
    a = project.item_by_path("a.jpg")
    project.set_capture_override(a.id, datetime(2021, 6, 6, 6, 6, 6))
    _save(project)
    (tmp_path / "moved").mkdir()
    (tmp_path / "a.jpg").rename(tmp_path / "moved" / "renamed.jpg")
    again = _open_and_scan(tmp_path)
    item = again.items[a.id]
    assert item.rel_path == "moved/renamed.jpg"
    assert item.exists and item.status != "missing"
    assert item.capture_datetime == datetime(2021, 6, 6, 6, 6, 6)
    assert again.dirty  # the new path must be saved


def test_rename_without_fingerprint_uses_size_and_mtime(tmp_path):
    make_jpeg(tmp_path / "x.jpg")
    st = (tmp_path / "x.jpg").stat()
    entry = sidecar.default_file_entry("old.jpg")
    entry["size"] = st.st_size
    entry["mtime"] = datetime.fromtimestamp(st.st_mtime).replace(microsecond=0).isoformat()
    files = scan.scan_folder(tmp_path, False)
    plan = plan_reconcile(tmp_path, [entry], files, False, fingerprint_fn=lambda p, s: "unused")
    assert plan.renamed == {entry["id"]: "x.jpg"}


def test_ambiguous_rename_is_not_matched(tmp_path):
    make_jpeg(tmp_path / "x.jpg")
    (tmp_path / "y.jpg").write_bytes((tmp_path / "x.jpg").read_bytes())
    entry = sidecar.default_file_entry("gone.jpg")
    entry["size"] = (tmp_path / "x.jpg").stat().st_size
    entry["fingerprint"]["value"] = scan.fingerprint(tmp_path / "x.jpg")
    plan = plan_reconcile(tmp_path, [entry], scan.scan_folder(tmp_path, False), False)
    assert plan.missing == {entry["id"]}


def test_subfolder_entries_are_out_of_scope_when_not_recursive(tmp_path):
    make_jpeg(tmp_path / "sub" / "c.jpg")
    project = _open_and_scan(tmp_path, recursive=True)
    c = project.item_by_path("sub/c.jpg")
    project.set_capture_override(c.id, datetime(2020, 1, 1))
    project.set_recursive(False)
    _save(project)
    again = _open_and_scan(tmp_path)
    item = again.items[c.id]
    assert item.status == "missing" and not item.in_scope  # not shown, not reported missing
    assert again.visible_items() == []


def test_hide_exported_uses_derived_files(tmp_path):
    make_jpeg(tmp_path / "a.jpg")
    make_jpeg(tmp_path / "a_edited.jpg")
    project = _open_and_scan(tmp_path)
    project.data["derived_files"].append({"path": "a_edited.jpg", "source_id": "x", "created": ""})
    assert [i.name for i in project.visible_items()] == ["a.jpg"]
    project.set_hide_exported(False)
    assert sorted(i.name for i in project.visible_items()) == ["a.jpg", "a_edited.jpg"]


def test_read_only_project_never_gets_dirty(tmp_path):
    (tmp_path / sidecar.SIDECAR_NAME).write_text('{"schema_version": 7}', encoding="utf-8")
    make_jpeg(tmp_path / "a.jpg")
    project = _open_and_scan(tmp_path)
    assert project.read_only
    project.set_recursive(True)
    assert not project.dirty


def test_metadata_is_not_reextracted_when_unchanged(tmp_path):
    make_jpeg(tmp_path / "a.jpg")
    project = _open_and_scan(tmp_path)
    a = project.item_by_path("a.jpg")
    project.set_capture_override(a.id, datetime(2020, 1, 1))
    _save(project)
    again = Project.open(tmp_path)
    files = scan.scan_folder(tmp_path, False)
    again.apply_reconcile(plan_reconcile(tmp_path, again.persisted_entries(), files, False))
    assert not again.needs_metadata(again.items[a.id], ffprobe_available=False)
