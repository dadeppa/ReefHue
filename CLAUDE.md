# ReefHue – conventions for Claude

`docs/SPEC.md` is the authoritative spec ("SPEC §N" in code comments refers to it). Ambiguities → pick the most conservative option, add one short entry to `docs/DECISIONS.md`, continue. Build milestone by milestone (SPEC §17); never start the next one until the user says "continue with milestone N".

## Stack
- Python 3.12 (3.11 min), PySide6 (Qt 6), numpy, opencv-python-headless, Pillow, piexif, PyAV (`av`), Send2Trash, pytest. Versions pinned in `requirements.txt`.
- No QFluentWidgets. Own QSS theme (`reefhue/resources/themes/`), own SVG icons (`reefhue/resources/icons/`, `currentColor` is recoloured per theme).
- macOS Apple Silicon first, Windows must keep working (no POSIX-only calls without a guard).
- FFmpeg/ffprobe are **user-supplied** in `./ffmpeg/` (fallback: PATH). Never download/bundle them. Photo features must never require FFmpeg.

## Structure (SPEC §16)
```
reefhue/  __main__.py  app.py
  ui/        Qt widgets, dialogs, theme, icons  (the ONLY place that imports Qt)
  core/      models, sidecar, settings schema, paths, logging, time logic
  pipeline/  params registry, stages, lut, auto, pipeline, cli
  io/        ffmpeg, probe, decode, exif, export, thumbs
  i18n/      de.json en.json nl.json + loader (tr)
  resources/ icons, themes, LUTs, presets
tests/   docs/DECISIONS.md
```

## Hard rules
- `core/`, `pipeline/`, `io/`, `i18n/` must **not import Qt** (headless-testable; a test enforces this).
- Preview and export call **the same pipeline stage functions**; only resolution/region differ.
- Every user-visible string goes through `tr("stable.key")` from `reefhue.i18n`; add the key to **de, en and nl** JSON (a test checks key parity). Never hard-code UI text. Slider labels come from the param registry's label keys.
- Widgets that show text implement `retranslate_ui()` and connect to `AppContext.language_changed` (runtime switch, no restart).
- Long work (decode, analysis, export, thumbnails, subprocess calls) never on the UI thread → `ui/workers.py` (QThreadPool + signals).
- Video: `ui/video_player.py` decodes (PyAV) + renders on its own threads, audio (Qt Multimedia) is the clock; paused frames go to `PreviewEngine` as "stills". Never decode on the UI thread.
- `core.project.Project` is mutated only on the UI thread; workers compute pure results (scan/reconcile plans, metadata) that `ui/project_controller.py` applies. The sidecar is written only for significant entries (see DECISIONS #27).
- Never modify/delete user media except in the explicit export flow (SPEC §14). Overwrite → `send2trash`.
- Float32 processing, vectorised numpy/OpenCV. Small tested functions in `core/` and `pipeline/`.
- Per-pixel pipeline stages are marked `pointwise=True` in `STAGES` and run in parallel strips (`pipeline/parallel.py`); never mark a stage that reads neighbouring pixels or whole-image statistics.

## Run / test
- App: `./start.command` (macOS) / `start.bat` (Windows), or `.venv/bin/python -m reefhue`.
- Tests: `.venv/bin/python -m pytest -q` (Qt tests run with `QT_QPA_PLATFORM=offscreen`, set in `tests/conftest.py`).
- After each milestone: full test suite green, then report (built / how to try / own decisions / limitations).
