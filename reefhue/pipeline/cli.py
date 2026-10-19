"""Headless export CLI (SPEC §8): ``python -m reefhue.pipeline.cli IN OUT [--edit FILE]``.

``--edit`` accepts a JSON file containing either an ``edit`` object, a single
sidecar file entry, or a whole ``reefhue.project.json`` (the entry matching
IN's file name is used). Photos are exported with metadata (EXIF, GPS, ICC);
videos need ffmpeg/ffprobe (looked up like the app does). Never overwrites
an existing file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from reefhue.core import export_plan as ep
from reefhue.core import extensions, sidecar
from reefhue.core.timeline import effective_datetime, is_overridden


def _entry_from_json(data: dict, input_name: str) -> dict:
    if "files" in data:
        for entry in data["files"]:
            if Path(entry.get("path", "")).name == input_name:
                return sidecar.normalize_file_entry(dict(entry))
        raise SystemExit(f"no entry for {input_name} in project file")
    if "edit" in data:
        return sidecar.normalize_file_entry({"path": input_name, **data})
    return sidecar.normalize_file_entry({"path": input_name, "edit": data})


def main(argv: list[str] | None = None) -> int:
    from reefhue.io import export as ex
    from reefhue.io import ffmpeg as ffmpeg_io
    from reefhue.io import metadata
    from reefhue.core.scan import scan_folder

    parser = argparse.ArgumentParser(prog="reefhue.pipeline.cli", description=__doc__.splitlines()[0])
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--edit", type=Path, help="JSON with edit settings (see module doc)")
    parser.add_argument("--quality", type=int, default=95, help="JPEG quality (default 95)")
    parser.add_argument("--video-quality", choices=("draft", "high", "very_high"), default="high")
    args = parser.parse_args(argv)

    src, out = args.input.resolve(), args.output.resolve()
    if out == src or out.exists():
        print("refusing to overwrite an existing file", file=sys.stderr)
        return 2
    entry = sidecar.default_file_entry(src.name)
    if args.edit:
        entry = _entry_from_json(json.loads(args.edit.read_text(encoding="utf-8")), src.name)
        entry["path"] = src.name
    kind = extensions.kind_of(extensions.ext_of(src.name))
    status = ffmpeg_io.check_ffmpeg() if kind == "video" else None
    ffprobe = status.ffprobe.path if status and status.available else None
    scanned = next((f for f in scan_folder(src.parent, False) if f.rel_path == src.name), None)
    if scanned is not None:
        meta = metadata.extract(src.parent, scanned, ffprobe)
        entry["media"].update({k: v for k, v in meta.media.items() if v is not None})
        entry["capture"]["datetime_original"] = meta.datetime_original
    opts = ep.ExportOptions(jpeg_quality=args.quality, video_quality=args.video_quality)
    job = ep.make_job(entry, kind, effective_datetime(entry), is_overridden(entry), opts, out.name)
    part = ex.part_path(out)
    control = ex.Control()
    try:
        if kind == "photo":
            ex.export_photo(src, part, job, lambda f: None, control)
        elif kind == "video":
            if status is None or not status.available:
                print("video export needs ffmpeg and ffprobe", file=sys.stderr)
                return 3
            ex.export_video(src, part, job, status.ffmpeg.path, status.ffprobe.path, lambda f: None, control)
        else:
            print(f"unsupported file type: {src.suffix}", file=sys.stderr)
            return 2
        os.replace(part, out)
    except ex.ExportError as exc:
        part.unlink(missing_ok=True)
        print(f"export failed: {exc.key} {exc.detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
