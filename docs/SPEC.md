# ReefHue – Product & Technical Specification (v1)

Working title: **ReefHue**. A desktop app (Python) for correcting the colors of underwater (scuba/snorkel) photos and videos, with a modern, polished UI. This document is the single source of truth. Build it in the milestones of section 17. If something is ambiguous, pick the most conservative reasonable option, note it in `docs/DECISIONS.md`, and continue.

---

## 1. Goals

- Non-destructive editing of dive photos and videos. Originals are never touched until the user explicitly exports.
- Manual mode (Lightroom-style basic sliders) plus an automatic mode (the "magic button") that sets sensible start values which the user can then fine-tune.
- Folder-based workflow: open a project folder, see its media, edit, export.
- **Output must match the on-screen preview.** Preview and export use the same processing pipeline.
- Target users: the author and other divers. GoPro footage is a first-class use case.

## 2. Platform and stack

- OS: **macOS (Apple Silicon only) primary**, Windows also supported. Keep code cross-platform.
- Python **3.12** (3.11 minimum). UI: **PySide6** (Qt 6).
- Libraries: `numpy`, `opencv-python-headless`, `Pillow`, `piexif`, `PyAV`, `send2trash`, `pytest`. Pin versions in `requirements.txt`.
- **No QFluentWidgets** (license). Custom QSS theme, own icon set (Lucide or Phosphor, freely licensed).
- External binaries: `ffmpeg` and `ffprobe`, supplied by the user in `./ffmpeg/` (see section 3).
- Processing uses float32 internally. Prefer vectorized numpy/OpenCV. GPU acceleration is out of scope for v1 but keep the pipeline stage-based so it could be added.
- Heavy work (decoding, analysis, export, thumbnails) never runs on the UI thread. Use worker threads/processes and signals.

## 3. Distribution (Variant B: source ZIP + start script)

The app is distributed as a ZIP that the user unpacks and starts. The user installs Python themselves.

```
ReefHue/
  start.command        # macOS launcher (chmod +x)
  start.bat            # Windows launcher
  requirements.txt
  ffmpeg/              # EMPTY folder; user places ffmpeg + ffprobe here (README.txt inside)
  luts/                # bundled/user .cube LUTs (see section 11)
  reefhue/             # the python package
  README.md
```

- Launchers: check that Python >= 3.11 exists (clear error message with download link if not), create `.venv` on first run, `pip install -r requirements.txt`, then start the app. Later starts only start the app (re-install if `requirements.txt` changed, detect via hash).
- **FFmpeg check at startup**: look for `ffmpeg` and `ffprobe` in `./ffmpeg/` first, then in the system PATH. If missing, show a non-blocking dialog and a banner in the preview area with an explanation and a link to https://ffmpeg.org/download.html. Photo editing must still work without FFmpeg; video features are disabled with a clear message.
- macOS: the user-supplied binaries may be quarantined. On startup, try to clear the quarantine attribute for the `ffmpeg/` folder (`xattr -dr com.apple.quarantine`). If it still fails, show a short help text. The launcher scripts also strip quarantine from the app folder. The app is unsigned; README explains "right click > Open" for the first start.
- Use `Apple Silicon` assumptions in README (arm64 ffmpeg build).

## 4. Application layout

Main window, horizontal split **1/3 : 2/3** (resizable splitter, sizes remembered).

- **Left (1/3): File pane** – toolbar + list/grid of the folder contents (section 5).
- **Right (2/3): Viewer pane** – top: preview canvas; below it: video transport + timeline (videos only); below that: **tabbed edit panel** (section 7). The edit panel is collapsible and its height adjustable via splitter.
- **Empty state** (no folder open): the right side shows a friendly area with an "Open folder" button and a drag-and-drop hint. If FFmpeg is missing, show the FFmpeg message here instead.
- **Menu bar**: File, Edit, View, Help.
  - File: Open folder (Cmd/Ctrl+O), Open recent, Export…, Export all edited…, Quit.
  - Edit: Undo/Redo (per file), Copy settings, Paste settings, Reset.
  - View: Theme (Light / Dark / System), Language, Toggle edit panel, Toggle histogram, Toggle curves, Info overlay.
  - Help: About, Open log folder, FFmpeg help.
- **Settings dialog**: language, theme, clear cache, preview quality (Auto / 50% / 25%), default export values, hardware encoder checkbox, thumbnail size.
- Theme: **Light / Dark / Follow OS**, switchable at runtime. Consistent spacing, accent color, rounded controls.
- Persist window geometry, splitter sizes, column layout, last-used settings (QSettings). **Do not** auto-open the last folder at start; offer a "Recent folders" list.

### Languages (i18n)

- JSON string tables, one file per language: `reefhue/i18n/de.json`, `en.json`, `nl.json`. Keys are stable identifiers, never the English text. Adding a language = adding a file.
- Languages for v1: **German, English, Dutch**. Default = OS language, fallback English. Switchable at runtime without restart.
- All user-visible strings go through `tr(key)`. Slider labels come from the parameter registry (section 7). Provide the Dutch translation as a first draft (to be reviewed by a native speaker).

## 5. File pane

**Toolbar icons** (also in menu): Open folder, Refresh, checkbox "Include subfolders", checkbox "Hide exported files", view toggle (list/grid) with size slider, filter button, and a **disabled placeholder button** for "Validate filenames / Batch rename" (greyed out, tooltip "coming soon"; no function in v1).

**Opening a folder**: menu, toolbar icon, drag-and-drop of a folder onto the window, "Open recent". On open: scan, look for `reefhue.project.json` and load it, then show the list.

**Recursion**: optional checkbox. Files listed in the sidecar's `derived_files` (exports) are hidden when "Hide exported files" is on (default on), also when recursive.

**Columns** (show/hide, reorder, resize; remembered): Thumbnail, Name, Extension, Type (photo/video), Capture date (italic + marker when manually corrected), Created date, Size, Duration, Resolution, Status, Subfolder (only when recursive).

**Sorting**: click header; Shift+click = secondary sort. Default: capture date ascending. **No grouping.**

**Thumbnails**: list/grid toggle with size slider. Thumbnails are generated in a background thread and stored in an **app cache** folder (not in the project folder). Cache is clearable in settings.

**Status badges** (derived, see section 13): Unedited, Edited, Exported, Changed after export; also "Missing" for sidecar entries whose file is gone.

**Filter**:
- A text field with comma-separated extensions (e.g. `jpg, jpeg, png, heic, mp4, mov`), applied on Enter.
- A button next to it opens a popup with two groups (Photos, Videos): checkboxes for the standard types, "all on/off" per group, and a text box for additional arbitrary extensions. Text field and popup stay in sync; selection is remembered.
- Default photo extensions: jpg, jpeg, png, heic, heif, tif, tiff, webp. Default video extensions: mp4, mov, m4v, avi, mkv. `.lrv` and `.thm` (GoPro proxy/thumbnail files) are **off by default**.
- Additional filters: type (photo/video), status, free-text name search.
- Unsupported extensions are listed but the viewer shows a clear "cannot display" message.

**Selection**: standard multi-select (Cmd/Ctrl, Shift), Cmd/Ctrl+A. The viewer shows the last-clicked file.

**Context menu**: Copy settings, Paste settings, Reset, Export…, Change capture date…, Shift timeline…, Reveal in Finder/Explorer.

**Folder changes**: Refresh (F5) rescans; optional auto-refresh. Files present in the sidecar but missing on disk stay as "Missing" (never deleted from the sidecar). Renamed files are re-matched using size + mtime + fingerprint.

**Keyboard**: Up/Down changes the file **only when the file pane has focus**. The next file is preloaded in the background.

### Capture date

- Source priority: EXIF `DateTimeOriginal` (photos) / container `creation_time` via ffprobe (videos) / filesystem date. The source is stored and shown.
- Times are treated as local "wall-clock" time, **no timezone conversion**.
- Manual correction per file (context menu "Change capture date…") is stored in the sidecar as `datetime_override`; original metadata is untouched; reset-to-original available.
- **"Shift timeline…" dialog**: select several files, mark one **anchor file**, enter its correct date/time; all other files in the selection are shifted by the same delta, keeping their relative spacing. Show old → new side by side before applying.
- **GoPro validation**: parse GoPro names (e.g. `GX010001.MP4`: chapter `01`, recording number `0001`). Recording numbers increase over time; warn if ordering by number conflicts with ordering by time. Detect time jumps (e.g. camera clock reset to 1970 after a battery change) and propose splitting the selection into groups, each with its own anchor (`time_groups` in the sidecar). Chapter files of one recording can derive their start from start + cumulative duration of previous chapters.
- Merging GoPro chapters into one video: **not in v1** (idea for later).

## 6. Viewer pane

**Preview canvas**: photos and videos, processed by the pipeline at reduced resolution for speed (quality setting: Auto / 50% / 25%). Fit-to-window default. Zoom with wheel/trackpad, pan with Space+drag. **F** = fit, **1** = 100%. At 100% / zoomed, re-render the visible region at full resolution so sharpness and noise can be judged (detail filters scale their radii with preview scale otherwise).

**Original / edited**:
- On/Off toggle button for the whole edit (view state only, not saved).
- Hold **`\`** to temporarily show the original.
- **Split view** (toggle on/off): a vertical divider line that can be dragged left/right; left = original, right = edited. (Meant as "horizontal slider".) No other compare modes.

**Overlays** (each toggleable): histogram (RGB + luminance, always visible in the edit panel's left area), clipping warning (marks blown/crushed areas in the image), crop overlay with rule-of-thirds grid while the Geometry tab is active, **Info overlay** (**Cmd/Ctrl+I**): camera, resolution, codec, bit depth, source profile, duration, capture date.

### Video transport and timeline

- Play/Pause button, timecode + frame number, a timeline bar with **two handles for Start and End** of the kept segment (one segment per video in v1). Outside the segment the bar is dimmed. **I / O** set start/end at the playhead. Toggles: "Play segment only", "Loop". **No filmstrip.**
- **Snapshot button (camera icon)** next to Play and key **S**: exports the current frame **with corrections applied** as JPEG into the same folder, named `<VideoName>_<hh-mm-ss>.jpg` (e.g. `GX010001_00-01-23.jpg`). Capture date = video capture date + position. The new file immediately appears in the list, flagged as exported/derived (add to `derived_files`).
- Volume control and mute.
- Playback: **audio is the clock**. If processing can't keep up at the chosen preview size, drop frames instead of slowing down. Show the actual frame rate in a small readout. Audio playback via Qt Multimedia, video decoding via PyAV, frames processed by the pipeline.
- One setting per video clip (no keyframes in v1), but the data model must leave room for keyframes later (see section 12).

### Keyboard shortcuts (focus dependent)

| Context | Key | Action |
|---|---|---|
| Global | Cmd/Ctrl+O | Open folder |
| Global | Cmd/Ctrl+Z / Shift+Z | Undo / redo (per file, in memory only) |
| Global | Cmd/Ctrl+Shift+C / V | Copy / paste settings |
| Global | Cmd/Ctrl+I | Info overlay |
| Global | F5 | Refresh |
| File pane focus | Up / Down | Previous / next file |
| File pane focus | Cmd/Ctrl+A | Select all |
| Viewer focus | F / 1 | Fit / 100% |
| Viewer focus | R / Shift+R | Rotate 90° clockwise / counter-clockwise |
| Viewer focus | `\` (hold) | Show original |
| Video focus | Space | Play/Pause |
| Video focus | Left / Right | ∓5 s (Shift: ∓1 s) |
| Video focus | Up / Down | Volume up/down |
| Video focus | `,` and `.` | Frame back / forward |
| Video focus | J / K / L | Reverse / pause / forward (repeat = faster) |
| Video focus | I / O | Set segment start / end |
| Video focus | S | Save snapshot |

## 7. Edit panel (tab panel below the preview)

Tabs: **Underwater | Light | Color | Curves | Details | Geometry | LUT & Presets**. The panel can be collapsed and resized. Layout: 2–3 columns of controls per tab.

**Histogram** sits permanently at the left of the panel (RGB + luminance), with a toggle for the clipping warning.

### Slider behavior (all sliders)

- Slider + number field. Typed values are clamped to min/max.
- **+ / − keys and arrow keys** change by the parameter's step; **Shift = 10×**, **Alt/Option = 0.1×**. Stepping must correctly handle the range around zero and stop at min/max without overshoot; round to the step grid (avoid floating-point drift).
- Bipolar sliders snap slightly at 0. **Double-click** slider or label resets to default. A reset button per tab and a global reset.
- Optional **Auto** button per tab where it makes sense (Light: auto exposure/contrast; Color: auto white balance).
- Every change is a single undo step (coalesce drag events).

### Parameter registry (single source for UI, defaults, validation, i18n keys)

Implement as one data structure (e.g. `reefhue/pipeline/params.py`) with key, group, min, max, default, step, unit, label key. Shift/Alt multipliers apply to `step`.

| Group | Key | Range | Default | Step |
|---|---|---|---|---|
| underwater | `auto_strength` | 0–100 % | 100 | 1 |
| underwater | `depth_m` | 0–40 m | 0 | 0.5 |
| underwater | `water_type` | blue / coastal / green | coastal | – |
| underwater | `red_comp` | −100…100 | 0 | 1 |
| underwater | `artificial_light` | bool | false | – |
| white_balance | `temperature`, `tint` | −100…100 | 0 | 1 |
| light | `exposure_ev` | −5…5 | 0 | 0.05 |
| light | `contrast`, `highlights`, `shadows`, `whites`, `blacks` | −100…100 | 0 | 1 |
| color | `vibrance`, `saturation` | −100…100 | 0 | 1 |
| levels (master, r, g, b) | `black` | 0…0.95 | 0 | 0.005 |
| levels | `white` | 0.05…1 | 1 | 0.005 |
| levels | `gamma` | 0.2…5 | 1.0 | 0.01 |
| details | `dehaze`, `clarity` | −100…100 | 0 | 1 |
| details | `sharpen_amount` | 0–100 | 0 | 1 |
| details | `sharpen_radius` | 0.5–3.0 px | 1.0 | 0.1 |
| details | `denoise` | 0–100 | 0 | 1 |
| geometry | `straighten_deg` | −45…45 | 0 | 0.1 |
| geometry | `rotate_90` | 0–3 | 0 | – |
| geometry | `flip_h`, `flip_v` | bool | false | – |
| geometry | `distortion` | −100…100 | 0 | 1 |
| geometry | `vignette_corr` | 0–100 | 0 | 1 |
| lut | `intensity` | 0–100 % | 100 | 1 |
| lut | `position` | start / end | end | – |

**Coupling rules**: levels `white − black ≥ 0.02` (each handle limited by the other). Curves: max 16 points, strictly increasing x with min spacing 0.01, endpoints movable only in y. Crop min size 32 px. Video segment: `start < end − 1 frame`.

### Tabs in detail

- **Underwater**: Magic button ("Auto"), `auto_strength`, `depth_m`, `water_type`, `red_comp`, `artificial_light` (with tooltip: artificial light compensates red loss at close range; auto then corrects less).
- **Light**: exposure, contrast, highlights, shadows, whites, blacks (+ Auto).
- **Color**: temperature, tint, vibrance, saturation, **white-balance eyedropper** (click on a gray/white area; averages an area, **default 11×11 px, configurable** between 1×1 and 31×31, setting remembered; writes temperature/tint, and may also adjust per-channel levels/gains).
- **Curves**: widget with channel selector (RGB/R/G/B), add/move/delete points, a few presets (e.g. light S-curve). Curves **and** brightness curve can be shown/hidden (the section is collapsible and each channel curve has an enable flag).
- **Levels** (placed in Color or Curves tab): per-channel black/white/gamma for Master/R/G/B (see registry).
- **Details**: dehaze, clarity, sharpen amount/radius, denoise (spatial, light, in v1). A "100% view" toggle for judging detail. Radii scale with preview resolution so preview ≈ export.
- **Geometry**: crop with overlay (thirds grid) and aspect presets (free, original, 16:9, 4:3, 3:2, 1:1, 9:16), straighten (±45°), rotate 90° (**R**), flip H/V, manual lens correction (`distortion`, `vignette_corr`). Applies to photos **and** videos. Lens/camera profiles (e.g. Lensfun) are later.
- **LUT & Presets**: see section 11 and 12.

## 8. Processing pipeline

One shared module used by preview, export and analysis. Stages are declared in one ordered list. Work in float32; convert from 8/10-bit with correct normalization.

1. **Decode** to float RGB. Optional **input LUT** first if `lut.position == "start"` (for Flat/Log sources). Convert to linear light.
2. **Geometry & lens**: distortion, vignette correction, straighten, rotate 90°, flip, crop (crop early to save time). Crop/rotation coordinates are normalized (0–1) relative to the image **after** distortion/straighten/rotate/flip.
3. **Denoise** (spatial, light), before channel amplification.
4. **Underwater** (linear): channel gains = depth prior × auto residual^(auto_strength/100) × red offset (section 9).
5. **White balance & exposure** (linear).
6. **Dehaze**.
7. **Convert to display space** (gamma/sRGB-like transfer).
8. **Levels**: master, then R, G, B.
9. **Tone**: contrast, highlights, shadows, whites, blacks.
10. **Color**: vibrance, saturation.
11. **Curves**: master, R, G, B.
12. **Details**: clarity, then sharpening last.
13. **Output LUT** (default position) with intensity.
14. **Output**: clip, quantize to 8/10 bit with dithering, tag color space.

Rules: preview and export call exactly the same stage functions; only resolution/region differ. The pipeline must be usable **without the GUI** (also via a small CLI for export) so it can be unit-tested. Original-vs-edited toggle is a view state, not stored.

## 9. Automatic mode ("magic button", v1)

Runs **only on click** (never automatically). Writes concrete, editable values; the user then adjusts manually. It is not a black-box filter.

Algorithm v1 (analysis at ~512 px, linear light, exclude near-clipped pixels < 0.02 and > 0.98; with `artificial_light` also down-weight very bright regions):

1. **Depth prior**: `depth_m` and `water_type` give per-channel gains: `gain_c = exp((K_c − K_ref) · depth)` where `K_c` are approximate attenuation coefficients per channel and water type (stored as tunable constants in one config file, `K_ref` = the lowest of the channels so gains are ≥ 1), capped by a soft limit (e.g. 8×) to limit noise. With `artificial_light` reduce the effective depth (start at 0.5×). Starting values are rough and **must be tuned visually**; document them. If `depth_m == 0` the prior is neutral.
2. **Analyze the prior-corrected image**: estimate the residual cast with a Gray-World-style estimate on green/blue plus red-channel compensation (red filled/scaled from green with an over-compensation guard), storing the result as `auto_result.gains_rgb`.
3. **Per-channel levels**: set black/white points per channel from robust percentiles (≈0.5 % / 99.5 %) with a limited stretch.
4. Optionally suggest exposure/contrast via the Auto buttons.
5. If `depth_m` was 0, **estimate** a depth from the R/B balance and write it into `depth_m` (editable, flagged as estimated).
6. **Video**: analyze several frames averaged across the segment (option: current frame only). One setting per clip.
7. Store everything in the sidecar (`auto_result`). **Export never re-runs the analysis**, so the result matches the preview.
8. "Apply to selection" runs the analysis per selected file.

Later (not v1): UDCP-style dehazing improvements, depth-map-based methods (Sea-thru-like) and ML methods. Keep the interface (`auto_result`, `algo_version`) open for that.

## 10. Source profile and metadata detection

Per file read codec, pixel format (bit depth), color transfer, primaries, camera model via ffprobe/EXIF. Show them in the Info overlay. A per-file **Source profile** field (Standard | Flat | Log | HDR/HLG) allows manual override; Flat/Log/HDR cannot be detected reliably from metadata, so it is user-set. **HLG/HDR input shows a warning in v1** (not fully supported). Keep 10-bit where possible in export.

## 11. LUTs

- Standard format **`.cube`** (3D; also read 1D and 1D shaper + 3D combinations; `LUT_3D_SIZE`, `LUT_1D_SIZE`, `DOMAIN_MIN/MAX`). Write an own parser; no heavy dependency.
- Folder `./luts/` next to the app (plus a per-user data folder as fallback, because app folders can be read-only). An **Import** button copies files into the folder.
- Controls: LUT selector, `intensity` (0–100 % blend), `position` (end by default; "apply first" for technical LUTs like Log → Rec.709).
- Trilinear (or tetrahedral) interpolation. Store LUT file name + SHA-256 in the sidecar and warn if a LUT is missing or changed.

## 12. Presets, copy/paste settings, undo

- **User presets** stored in the app data folder (not in the project). They contain only the `edit` sections. Bundled starter presets: "Shallow 5 m", "Blue water 20 m", "Green water / lake", "With lamp".
- **Copy/paste settings** (Cmd/Ctrl+Shift+C/V) and "apply preset" open a small dialog with checkboxes per section: Underwater, White balance, Light, Color, Levels, Curves, Details, LUT, Geometry. **Geometry is unchecked by default.** Works on multi-selection.
- **Undo/redo per file** in memory (not persisted).

## 13. Sidecar project file

One file per project folder: **`reefhue.project.json`** (visible, not hidden), in the root folder; files are referenced by **relative paths**. Settings contain concrete values (no references), so the project works on another machine.

Rules:
- Autosave shortly after each change: write to a temp file, then atomic rename; keep one `.bak` of the previous version.
- `schema_version`; unknown fields are preserved on save; missing fields are filled from registry defaults; if the file has a **higher** `schema_version`, open read-only and warn. Lock against the same folder opened twice.
- **Status is derived, not stored**: *Unedited* = `edit`/segment equal registry defaults; *Exported* = `settings_hash` equals the hash of the last successful export; *Changed after export* = exported before but hash differs; *Edited* = otherwise. The hash covers `edit`, `video.segment`, `export_options`.
- `derived_files` lists exports and snapshots (used to hide them).
- The data model reserves room for future keyframes (e.g. each numeric parameter may later be a value or a keyframe list); v1 writes plain values.

Schema (v1):

```json
{
  "schema_version": 1,
  "app": { "name": "ReefHue", "version": "0.1.0" },
  "project": {
    "created": "", "modified": "",
    "recursive": false, "hide_exported": true,
    "extension_filter": ["jpg","jpeg","png","heic","mp4","mov"],
    "export_defaults": {
      "mode": "suffix", "suffix": "_edited", "subfolder": "edited",
      "jpeg_quality": 95, "video_quality": "high", "hw_encoder": false,
      "keep_telemetry": true
    },
    "time_groups": [
      { "id": "uuid", "anchor_file_id": "uuid",
        "anchor_datetime": "2026-10-17T09:12:00", "delta_seconds": 0 }
    ]
  },
  "files": [
    {
      "id": "uuid",
      "path": "Day1/GX010001.MP4",
      "size": 0, "mtime": "",
      "fingerprint": { "algo": "head-tail-sha1", "value": "" },
      "media": {
        "type": "video", "codec": "hevc", "pix_fmt": "yuv420p10le",
        "bit_depth": 10, "width": 3840, "height": 2160, "fps": 59.94,
        "duration_s": 0, "color_transfer": "bt709", "color_primaries": "bt709",
        "rotation_meta": 0, "camera": "", "has_audio": true, "has_telemetry": true
      },
      "source_profile": { "value": "standard", "auto_detected": true },
      "capture": {
        "datetime_original": "", "datetime_source": "exif|ffprobe|filesystem",
        "datetime_override": null, "override_reason": null, "time_group_id": null
      },
      "edit": {
        "pipeline_version": 1,
        "underwater": {
          "auto_strength": 100, "depth_m": 0, "water_type": "coastal",
          "red_comp": 0, "artificial_light": false,
          "auto_result": { "analyzed_at": null, "source": "frame|segment",
                           "gains_rgb": [1,1,1], "levels": null, "algo_version": 1,
                           "depth_estimated": false }
        },
        "white_balance": { "temperature": 0, "tint": 0 },
        "light": { "exposure_ev": 0, "contrast": 0, "highlights": 0, "shadows": 0, "whites": 0, "blacks": 0 },
        "color": { "vibrance": 0, "saturation": 0 },
        "levels": {
          "master": { "black": 0, "white": 1, "gamma": 1 },
          "r": { "black": 0, "white": 1, "gamma": 1 },
          "g": { "black": 0, "white": 1, "gamma": 1 },
          "b": { "black": 0, "white": 1, "gamma": 1 }
        },
        "curves": {
          "master": { "enabled": true, "points": [[0,0],[1,1]] },
          "r": { "enabled": true, "points": [[0,0],[1,1]] },
          "g": { "enabled": true, "points": [[0,0],[1,1]] },
          "b": { "enabled": true, "points": [[0,0],[1,1]] }
        },
        "details": { "dehaze": 0, "clarity": 0, "sharpen_amount": 0, "sharpen_radius": 1.0, "denoise": 0 },
        "geometry": {
          "distortion": 0, "vignette_corr": 0, "straighten_deg": 0,
          "rotate_90": 0, "flip_h": false, "flip_v": false,
          "crop": { "x": 0, "y": 0, "w": 1, "h": 1, "aspect": "free" }
        },
        "lut": { "file": null, "sha256": null, "intensity": 100, "position": "end" }
      },
      "video": { "segment": null },
      "export_options": { "keep_audio": true, "keep_telemetry": true },
      "export_history": [
        { "id": "uuid", "timestamp": "", "mode": "suffix", "target_path": "",
          "settings_hash": "", "pipeline_version": 1, "result": "ok",
          "original_trashed": false,
          "encoder": { "codec": "hevc", "quality": "high", "hw": false } }
      ]
    }
  ],
  "derived_files": [ { "path": "", "source_id": "uuid", "created": "" } ]
}
```

`video.segment` is `{ "start_s": 0.0, "end_s": 0.0 }` or `null`. All timestamps are local wall-clock strings.

## 14. Export

**Dialog** (single file or batch). Three modes:
1. **Copy with suffix** (default, editable, default `_edited`) in the same folder.
2. **Copy into subfolder** (editable name, default `edited`).
3. **Overwrite original** – never preselected. Requires an extra confirmation. The original goes to the **system trash** (via `send2trash`) rather than being replaced hard. After a successful overwrite: reset that file's `edit` to neutral (otherwise it would be applied twice), set status to "exported (overwritten)", keep the previous state in `export_history`.

**Name conflicts**: ask once per export run – skip / replace / auto-number (`_edited_2`), with "apply to all".

**Batch**: "Export selection" and "Export all edited but not yet exported" (includes *Changed after export*). A **queue panel** with per-file and total progress, remaining time, cancel (single/all) and pause. Runs in the background; the app stays usable and editing continues (export uses the state at its start). Videos run sequentially, photos in parallel. System notification at the end; failed files listed with an error log. Prevent system sleep while exporting (macOS: `caffeinate`-style, Windows: execution state).

**Safe writing**: write to `<name>.part`, then rename on success. Afterwards verify via `ffprobe` (duration/frame count plausible). Check free disk space beforehand. Clean up on cancel/failure.

**Photos**: keep the original format; default format **JPEG** (quality default 95; setting). HEIC input is exported as JPEG (choice in dialog). PNG/TIFF stay as they are. Keep EXIF, GPS, and ICC profile; set the orientation correctly; write a corrected capture date into EXIF when the user changed it. Pillow + piexif (no external exiftool).

**Videos**:
- Re-encode whenever color/crop/rotation are applied. Codec **same as the source** (H.264→H.264, HEVC→HEVC), container as in the original (MP4/MOV), keep 10-bit where possible.
- Quality levels: *Draft | High (default) | Very high*, mapped to CRF/presets for x264/x265. Checkbox "Use hardware encoder" (VideoToolbox on macOS, default off because quality/size is worse).
- Frames come from the same Python pipeline as the preview and are piped to FFmpeg, so **output equals the preview**. Expect slower export than pure FFmpeg filters.
- If only the segment is cut and nothing else is edited, **cut losslessly** (stream copy).
- Audio: keep by default; per-file option **"remove audio"** (`export_options.keep_audio`). Audio is cut to the segment.
- Metadata: keep camera metadata, GPS, and `creation_time` (write the corrected capture date if the user changed it).
- **GoPro telemetry**: preserve **best effort** if possible (trimmed to the segment; crop/rotation do not change it). Can be turned off in the dialog (`keep_telemetry`). On failure export without it and show a notice.
- Rotation: when rotated by 90°, width/height swap; otherwise keep the original resolution.
- **Straighten/crop without empty corners**: automatically crop to the largest valid rectangle (default on). No upscaling; the resolution may shrink.

## 15. Error handling and logging

Clear, human-readable messages; no crashes on unreadable files. Rotating log file in the app data folder, "Open log folder" in the Help menu, error dialog with "Copy details". Missing FFmpeg, missing LUT, higher sidecar schema, low disk space, locked folders, and unsupported formats all have defined, friendly messages (i18n).

## 16. Project structure

```
reefhue/
  __main__.py
  app.py                 # QApplication setup, theme, i18n, ffmpeg check
  ui/                    # widgets, windows, dialogs, theme (QSS), icons
    main_window.py  file_pane.py  viewer.py  timeline.py  edit_panel/ ...
    export_dialog.py  queue_panel.py  settings_dialog.py  time_shift_dialog.py
  core/                  # models, sidecar, project, status, scanning, time logic
  pipeline/              # params.py (registry), stages/, lut.py, auto.py, pipeline.py, cli.py
  io/                    # ffmpeg.py, probe.py, decode.py (PyAV), exif.py, export.py, thumbs.py
  i18n/                  # de.json en.json nl.json  + loader
  resources/             # icons, themes, bundled LUTs, starter presets
tests/
docs/DECISIONS.md
```

Keep UI thin; core and pipeline must not import Qt (so they are testable headless).

## 17. Milestones (each ends with a runnable app and passing tests)

1. **Skeleton**: package, launchers (`start.command`, `start.bat`), venv bootstrap, main window with 1/3–2/3 split, theme (light/dark/system), i18n (de/en/nl), menu, settings dialog, FFmpeg check with message and link, empty state, logging. *Accept*: app starts on macOS (and Windows), language/theme switch at runtime, missing FFmpeg handled gracefully.
2. **File pane + sidecar**: folder open (menu/icon/drag-drop/recent), scan with optional recursion, columns/sorting/filters/extension popup, thumbnails in cache, metadata (EXIF/ffprobe), capture-date override and "Shift timeline…" with GoPro validation, sidecar load/save (atomic, `.bak`, schema version), status derivation, hide exported. *Accept*: re-opening a folder restores all settings; unit tests for sidecar, status, time shift.
3. **Pipeline + edit panel (photos)**: parameter registry, all stages, histogram, clipping warning, all tabs and sliders (behavior per section 7), curves, levels, eyedropper, crop/rotate/flip, original/edited toggle and split view, zoom/pan, undo/redo, presets, copy/paste settings dialog. *Accept*: preview of a photo reacts live; stepping/clamping tests pass; pipeline works headless.
4. **Video preview + timeline**: PyAV decoding, audio clock via Qt Multimedia, frame dropping, segment handles, I/O, J/K/L, shortcuts, snapshot (S + button), info overlay, source-profile override. *Accept*: smooth 4K playback at reduced preview size, snapshot writes a corrected JPEG.
5. **Export**: dialog, three modes, conflict handling, queue panel, batch ("edited not yet exported"), safe writing, photo export with metadata, video re-encode via piped frames, lossless cut, audio option, telemetry best effort, auto-crop for straighten. *Accept*: export matches preview (automated pixel-difference test within tolerance); overwrite goes to trash.
6. **Magic button, LUTs, presets**: auto algorithm (section 9) incl. video multi-frame analysis, `.cube` parser/LUT stage, LUT import, bundled starter presets and LUTs. *Accept*: auto improves synthetic blue/green test images measurably; LUT tests vs. known results.
7. **Polish**: shortcuts audit, tooltips, empty/error states, performance pass, README (install, FFmpeg, first start on macOS), about dialog, final tests.

## 18. Testing

Use `pytest`. Generate synthetic test images/videos (FFmpeg `testsrc`, numpy gradients with simulated blue/green casts). Cover: parameter registry limits and stepping around zero/min/max, coupling rules, sidecar round-trip and schema migration, status derivation, time shift and GoPro validation, LUT parsing/application, pipeline stage order, export-equals-preview tolerance, safe-write behavior. After each milestone run the full test suite and fix failures before reporting.

## 19. Out of scope for v1 (do not build, but keep the design open)

RAW formats, keyframes/varying depth within a clip, multiple video segments, Sea-thru/depth-map/ML correction, dive-log (Subsurface) import, filename validation and batch renaming (only the greyed-out button), merging GoPro chapters, lens/camera profile database (Lensfun), star ratings, spot/particle removal, GPU acceleration, temporal denoise, HDR/HLG processing, code signing/notarization.

---

## Addendum A – FFmpeg and Python on Apple Silicon (supersedes parts of section 3)

Background: the common static macOS FFmpeg builds (evermeet.cx) are **Intel-only** and run on Apple Silicon only through Rosetta 2. Native arm64 FFmpeg is available via Homebrew (`brew install ffmpeg`, installs to `/opt/homebrew/bin`) or via arm64 static builds from other providers. The target machine (MacBook Pro, M-series) uses Homebrew's native build. This addendum replaces the statement "arm64 ffmpeg build" in section 3 and extends the startup checks.

**A1. FFmpeg lookup order** (for both `ffmpeg` and `ffprobe`, resolved together from the same location):
1. `./ffmpeg/` next to the app,
2. Homebrew locations: `/opt/homebrew/bin` (Apple Silicon), then `/usr/local/bin` (Intel),
3. the system `PATH`.
Launcher scripts (`start.command`) are often started without Homebrew in `PATH`, so the explicit locations in step 2 are required. Show in the Info/Help dialog which binary and version is in use. Never copy dynamically linked binaries (e.g. from Homebrew) into `./ffmpeg/`; only statically linked builds belong there.

**A2. Architecture check (macOS)**: determine the architecture of the chosen FFmpeg binary (e.g. via `file` / Mach-O header). If the host is arm64 and the binary is x86_64, still use it, but show a friendly, non-blocking notice: "This FFmpeg is an Intel build running through Rosetta 2. A native arm64 build is recommended (e.g. `brew install ffmpeg`)". If Rosetta is not installed and the Intel binary fails to start, show how to install it (`softwareupdate --install-rosetta`). Do not crash.

**A3. Capability check at startup**: run `ffmpeg -hide_banner -encoders` and verify the encoders required by the export spec (section 14): `libx264` and `libx265` are required. `hevc_videotoolbox` and `h264_videotoolbox` are only needed for the optional hardware-encoder checkbox; disable that checkbox with a tooltip if they are missing. If a required encoder is missing, show a clear message explaining which features are unavailable and how to fix it (use Homebrew's build or a static build that includes x264/x265). Photo editing must keep working. Also verify that `ffprobe` is present and executable.

**A4. Python architecture check**: both launchers must verify that the Python interpreter matches the machine. On macOS Apple Silicon, `platform.machine()` must be `arm64`. If an Intel (x86_64) Python is running under Rosetta, stop with a clear message ("Please install the native Apple Silicon Python 3.12 from python.org or Homebrew"), because it would create a virtual environment with x86_64 wheels (wrong, slow, and mismatched with the arm64 FFmpeg). Also check the Python version (>= 3.11, recommended 3.12) as before. On Windows only check the version.

**A5. README and Help dialog**: document both ways to get FFmpeg on macOS:
- Recommended: `brew install ffmpeg` (native arm64, easy updates; found automatically).
- Alternative: place a **statically linked** arm64 build (`ffmpeg` and `ffprobe`) into `./ffmpeg/`; the app tries to clear the macOS quarantine attribute for that folder at startup (as in section 3).
- Fallback: Intel static build via Rosetta 2 (slower to set up, supported but not recommended).
Include the verification command `ffmpeg -hide_banner -encoders | grep -E "libx264|libx265|videotoolbox"` and link to https://ffmpeg.org/download.html. All new messages go through the i18n tables (de, en, nl).

**A6. Tests**: unit-test the lookup order with fake directory layouts, the architecture/encoder parsing with captured sample outputs (arm64 binary, x86_64 binary, missing x265), and the Python-architecture check with mocked `platform.machine()`.