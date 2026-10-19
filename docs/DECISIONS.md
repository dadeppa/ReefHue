# Decisions

Choices made where SPEC.md is ambiguous or silent. One short entry each, newest milestone last.

## Milestone 1 – Skeleton

1. **Pinned versions** – PySide6 6.11.2, numpy 2.4.6, opencv-python-headless 4.13.0.92, Pillow 12.2.0, piexif 1.1.3, av 18.1.0, Send2Trash 1.8.3, pytest 8.4.2. Each is one step behind the newest release. New majors (OpenCV 5, Send2Trash 2, pytest 9) are avoided. All verified to install on Python 3.12 / macOS arm64.
2. **Launcher Python choice** – order: `python3.12`, `python3.11`, `python3.13`, then `python3` (≥ 3.11). Apple's `/usr/bin/python3` stub is skipped. Very new Pythons (e.g. 3.15) often lack PySide6 wheels, so 3.12 is preferred.
3. **Requirements hash** – `python -m reefhue.bootstrap check|stamp` (stdlib only). Both launchers share it. The SHA-256 stamp lives inside `.venv`, so a fresh venv always installs.
4. **Launcher messages are English-only** – they run before the app and i18n exist. Every in-app string goes through `tr()`.
5. ~~**FFmpeg search**~~ – superseded by Addendum A (see A-1 below).
6. **FFmpeg states** – *missing* (a tool not found) vs *blocked* (found but not runnable). Each state has its own help text. The app never runs `chmod` on the binaries. It only clears quarantine (spec) and shows the `chmod +x` command.
7. **FFmpeg notice** – the startup dialog is non-modal and appears on every start while FFmpeg is unavailable. There is no "don't show again" option because the spec doesn't mention one. The banner stays in the preview area. In the empty state the banner sits above the Open-folder card. "Instead" in SPEC §4 is read as "in this area", so the Open button stays reachable.
8. **Empty state uses the whole right side** – the edit panel is hidden until a folder is open. After that, View ▸ Edit panel controls it (remembered).
9. **Open folder in M1** – chooser, drag-and-drop, and recent list are wired up. They only set the current folder: nothing is scanned and nothing is written into the folder. Listing comes in M2. A placeholder text says so.
10. **Recent folder that no longer exists** – shows a message and removes it from the recent list. This is app state only, not user data.
11. **Not-yet-implemented menu items** (Export, Undo/Redo, Copy/Paste, Reset, Histogram, Curves, Info overlay, Refresh) are present but disabled, so the menu layout is final.
12. **Settings storage** – QSettings (native: plist / registry). All values go through a headless schema (`core/app_settings.py`) that coerces types, because the registry returns strings. Invalid values fall back to defaults. "Overwrite" can never be the default export mode.
13. **Export defaults in Settings** – suffix and subfolder names are validated against characters that are forbidden on Windows. OK is disabled while a name is invalid.
14. **App folders** – data: `~/Library/Application Support/ReefHue` (`%APPDATA%\ReefHue`). Cache: `~/Library/Caches/ReefHue` (`%LOCALAPPDATA%\ReefHue\Cache`). Logs: `<data>/logs`, rotating 5 × 1 MB. `REEFHUE_DATA_DIR` / `REEFHUE_CACHE_DIR` override them (used by tests).
15. **Clear cache** – deletes only the contents of the app cache folder, refuses home or root as a safety net, and runs on a worker thread.
16. **Theme** – Qt Fusion style plus QPalette plus one QSS template with `@token` placeholders for the light and dark palettes. "Follow system" uses `QStyleHints.colorScheme()` and follows live OS changes (Qt ≥ 6.5). Accent colour is ocean blue.
17. **Icons** – own SVGs drawn in Lucide style (24 px grid, 2 px stroke, `currentColor`), recoloured per theme. They can be swapped for official Lucide files (ISC) with the same names.
18. **Language names** come from each JSON file's `_meta.native_name`, so adding a language is only adding a file. The menu uses cross-platform names ("Datei", "Bestand") rather than the macOS-only "Ablage" or "Archief". Dutch is marked as a first draft in `nl.json` `_meta`.
19. **Runtime language switch** – `AppContext.language_changed` signal. Each widget implements `retranslate_ui()`. Modal dialogs are rebuilt on open.
20. **Error handling** – a global `sys.excepthook` logs the error and shows an error dialog with "Copy details" and "Open log folder". Exceptions in worker threads are logged and passed to the caller's `on_failed`.
21. **Background callbacks** – `run_task(..., guard=widget)` skips callbacks if the widget was destroyed in the meantime (e.g. a dialog closed while the cache size was still being computed).
22. **cv2 + PyAV both bundle libavdevice** – on macOS, importing both logs an Objective-C "class implemented in both" warning. M1 imports neither. To be revisited in M4: keep cv2 and av in separate code paths/processes, or confirm the warning is harmless.
23. **`--quit-after-ms`** – a hidden developer flag that closes the app after N ms, used for launcher smoke tests. It is not shown in the UI.

## Milestone 2 – File pane + sidecar

24. **pillow-heif added** (1.7.0, pinned). HEIC/HEIF are default photo extensions and spec §14 exports HEIC as JPEG, but Pillow alone cannot read HEIC. If it fails to load, HEIC files show "cannot display" instead of crashing.
25. **Parameter registry pulled forward** – `pipeline/params.py` now holds the registry *data* (defaults, ranges, steps). Sidecar defaults and the "Unedited" status (§13) depend on it. Stepping/clamping logic stays in M3.
26. **Default extension filter** – §5's lists (jpg, jpeg, png, heic, heif, tif, tiff, webp / mp4, mov, m4v, avi, mkv) are used. The shorter list in the §13 schema example is treated as an example only.
27. **The sidecar is only written when there is something to keep** – opening a folder never creates `reefhue.project.json`. It is first written when a project setting or a file's state changes. Only *significant* entries (edits, date overrides, time groups, exports, source-profile override, non-default export options) go into `files`. Once written, an entry is never removed, so a deleted file stays "Missing" as §5 requires. Metadata for all other files lives in a per-folder cache in the app cache folder.
28. **Corrupt sidecar** – the folder opens read-only with a warning. The file is never overwritten or renamed.
29. **Folder lock** – kept in the app data folder (`locks/<sha1(path)>.lock`, holding pid and host), not in the user's folder. A second instance opens the folder read-only. Stale locks (dead pid) are taken over. This does not protect the same network folder opened from two machines.
30. **Rename matching** – an unmatched sidecar entry is matched to an unmatched file of the same size whose head-tail SHA-1 fingerprint is equal. If no fingerprint was stored, the mtime must agree within 2 s (FAT/exFAT resolution). The match must be unique, otherwise the entry stays Missing. The fingerprint is checked first because mtime strings are local wall-clock time and shift with the computer's time zone.
31. **Out-of-scope entries** – with "Include subfolders" off, sidecar entries in subfolders are neither shown nor reported as Missing.
32. **Filesystem capture date** = the earlier of mtime and creation time, because SD-card copies keep mtime but get a new creation time.
33. **Video creation_time** – Apple's `com.apple.quicktime.creationdate` (local time with offset) is preferred. Otherwise `creation_time` is used with its `Z`/offset *dropped, not converted* (§5 wall-clock rule; GoPro writes local time tagged as UTC). Pre-1971 dates are used only if nothing better exists.
34. **Wall-clock in Qt** – date editors work in UTC so Qt never applies DST corrections (02:30 on a changeover day stays 02:30).
35. **Shift timeline** – needs ≥ 2 selected files; "Change capture date…" needs exactly one. Shifting writes `datetime_override` (reason `time_shift`) plus one `time_groups` record per group. A manual override detaches the file from its group, and unreferenced time groups are pruned. If a new value equals the original, the override is cleared.
36. **GoPro validation** – names parsed: `GH/GX/GL` + chapter (2 digits or loop letters) + number, `GOPRnnnn` (chapter 1) and `GPccnnnn` (chapter cc+1). A split happens where time runs backwards or the date switches between plausible (≥ 2005) and implausible in recording-number order. Non-GoPro files join the group whose time range contains them, else the nearest one. Chapter derivation is an opt-in checkbox in the dialog and is applied as the base before shifting.
37. **Toolbar filter button** shows/hides the filter rows (extension field + popup button, type, status, name search). Its state is remembered. Unknown extensions entered in the text field appear as photo "additional extensions" in the popup.
38. **Sorting** – click = primary key (click again toggles); Shift+click adds or toggles up to 3 keys. Empty values always sort last. File name is the final tie-breaker. Keys, column layout and view mode are stored in QSettings, not in the project.
39. **List vs. grid size** – one slider (`thumbs/size`, 64–320 px). Grid uses it directly, list rows use half (24–160 px).
40. **Video thumbnails** use the user's `ffmpeg` (frame at min(1 s, 10 %)). Without FFmpeg, videos have no thumbnail and only filesystem dates. When FFmpeg becomes available (Check again), the folder is re-probed automatically.
41. **Auto-refresh** is optional and off by default (Settings). It uses QFileSystemWatcher on the folder (plus up to 256 subfolders when recursive) with a 1.5 s debounce. F5 always works.
42. **Viewer until M3** – the selected file shows its thumbnail and key metadata, or a "cannot display" / "missing" / "needs FFmpeg" notice. The real preview canvas comes in M3.
43. **Context menu items from later milestones** (copy/paste settings, reset, export) are shown disabled.

## Milestone 3 – Pipeline + edit panel (photos)

44. **Value grid** – numeric parameters are stored on a grid of step × 0.1, anchored at 0, so Alt-steps land on the grid. Stepping, typing and dragging all round to this grid and clamp to min/max, so there's no float drift and never "-0.0". Typed values accept a comma as decimal separator.
45. **Zero snap** – bipolar sliders snap to 0 within ±1.5 % of the range, and only while dragging.
46. **Undo** – each drag, key press (auto-repeat merges until the key is released), typed value, button or menu action is one step. History lives in memory, up to 200 steps per file, and survives switching files within a session.
47. **"Original"** (toggle, `\`, split view) = the same geometry and crop, without colour, tone or detail edits. That keeps the split view aligned.
48. **100 %** = one image pixel per *physical* screen pixel (Retina). When zoomed beyond the preview's resolution, the visible region is rendered at full resolution:
    - the full-resolution geometry result is cached while zoomed and freed when zooming out;
    - filter margins are included, and whole-frame stats (dehaze airlight) come from the preview;
    - the original side of the split view is also rendered at full resolution.
49. **Preview size** – Auto = the canvas's physical size rounded up to 256 px (1024–3072). 50 % / 25 % = of the full resolution. While dragging, drafts are rendered at ≤ 1100 px; the full preview follows on release.
50. **Prefix cache** – the result of the linear-light stages (denoise … to-display) is cached and reused while only later stages change.
51. **Depth prior** – gains are *not* normalised; brightness is left to Exposure. The cap is a soft tanh limit at 8×. K constants live in `pipeline/water_model.py` and are untuned starting values. `red_comp` ±100 = red ×2^±1.
52. **White balance model** – temperature ±100 = red/blue ±1 stop in opposite directions. Tint ±100 = green ∓0.5 stop. The eyedropper and Auto WB write only temperature/tint (clamped to ±100), no extra per-channel gains.
53. **Tone** – one luma-based curve, applied hue-preserving (ratio) and additively near black so Blacks can lift. Highlights also act above 1.0 so blown values can be recovered.
54. **Dehaze** – dark-channel prior: window radius 1 % of the short side, box-blurred transmission (no guided filter), minimum transmission 0.12. Negative values add haze.
55. **Denoise** – chroma Gaussian + luma bilateral, both light. **Clarity** – radius 0.8 % of the short side, weighted toward midtones. **Sharpening** – unsharp mask on luma, skipped below 0.25 px at the current preview scale, so 25 % previews don't fake sharpness.
56. **Curves** – monotone cubic (Fritsch–Carlson). Presets: linear, light/strong S, lift shadows, fade. Point rules per §7.
57. **Lens** – distortion uses one radial coefficient (±100 → ±0.3, radius normalised to the half-diagonal); pincushion correction zooms in to avoid empty corners. Vignette correction gives up to +1 stop at the corners (r²).
58. **Straighten** – always auto-crops to the largest same-aspect rectangle, in preview and export alike. Crop minimum is 32 px at full resolution (scaled in previews).
59. **Rotate/flip move the crop** so the same content stays selected. R / Shift+R turn the *view* clockwise / counter-clockwise even when exactly one flip is active.
60. **Aspect presets** fit the largest rectangle of that ratio, centred on the current crop. "Original" = the ratio of the uncropped frame. While the Geometry tab is open, the uncropped frame is shown with the crop overlay.
61. **Levels** live in the Curves tab, next to the curve editor.
62. **Auto buttons** – Light: median luma → 0.42 (display), contrast from the 5–95 % spread. Color: gray-world on the image entering the WB stage, excluding near-clipped pixels. Both are computed from the preview on a worker thread. The Underwater "magic" button stays disabled until M6.
63. **Eyedropper** – one click (one-shot), averaging an odd square of 1–31 *full-resolution* pixels (default 11, remembered).
64. **Copy settings** uses an internal clipboard, not the system clipboard. Paste, apply-preset and Reset act on the selection, or the current file when nothing is selected. Each file gets its own undo step.
65. **User presets** are JSON files in `<app data>/presets/` holding only the chosen sections. Saving asks for a name plus sections (Geometry unchecked by default).
66. **Colour management** – sources are assumed to be sRGB; ICC profiles are ignored in the preview. Export (M5) will carry the ICC profile over.
67. **Unit symbols** (EV, m, %, px, °) are shown untranslated.
68. **Clipping warning** – red where any channel reaches 255, blue where all channels are 0 (after 8-bit quantisation).
69. **Output quantisation** – export uses deterministic triangular dither (fixed seed); the preview is not dithered.
70. **Pipeline CLI** (`python -m reefhue.pipeline.cli`) writes JPEG/PNG/TIFF without metadata and refuses to overwrite its input. Full export is M5.
71. **Scope** – only photos are editable in M3; videos keep the info view until M4.

## Addendum A – FFmpeg and Python on Apple Silicon

A-1. **Pair lookup** – `ffmpeg` and `ffprobe` must come from the same directory. Order: `./ffmpeg/`, `./ffmpeg/bin/` (kept from M1 because Windows builds unpack into `bin/`; both count as the app folder), `/opt/homebrew/bin`, `/usr/local/bin` (macOS only), then PATH. If no directory has both, FFmpeg counts as missing; the tool that *was* found is still shown in the dialog.
A-2. **Architecture** is read from the Mach-O header (thin and universal binaries) instead of running `file`. Universal builds count as native. Apple Silicon is detected with `platform.machine()` and, as a fallback, `sysctl hw.optional.arm64`, which is correct even inside a Rosetta process.
A-3. **Rosetta** only gets a banner (non-blocking). Missing *required* encoders also open the non-modal help dialog at start, because video export is affected. If an Intel binary fails to start on Apple Silicon, the help shows `softwareupdate --install-rosetta` (with Homebrew as the better option) instead of the quarantine hint.
A-4. **Encoders** – export is disabled when libx264 or libx265 is missing. The hardware-encoder checkbox in Settings is disabled with a tooltip when the VideoToolbox encoders are missing. Video playback (PyAV) does not depend on these encoders.
A-5. **Python check** – `python -m reefhue.bootstrap probe` (stdlib only) returns 0 / 2 (too old) / 3 (Intel Python on Apple Silicon). The macOS launcher uses it for each candidate interpreter and for an existing `.venv`; a `.venv` made by an Intel Python is rebuilt with a native one. Windows checks only the version.

## Milestone 4 – Video preview + timeline

72. **Videos need FFmpeg** – they are editable only when FFmpeg is available and the file was probed (codec + duration known), as §3 requires ("video features disabled"). This holds even though PyAV ships its own decoder libraries.
73. **Decoding** – PyAV software decoding with frame threading. In a test, VideoToolbox through PyAV was slower than software decoding (p010 transfer). swscale downscales to the preview size and converts with BT.709 for sources ≥ 720 lines. Video frames are treated as sRGB-encoded, like photos.
74. **Playback size** – Auto = 960 px long side for > 40 fps, 1280 px otherwise, never above the canvas. 50 % / 25 % = of the source. Paused frames use the normal photo preview size.
75. **Clock** – audio via Qt Multimedia (`QMediaPlayer`, video track disabled) is the master clock, interpolated between its position updates. A stale audio position after seek/play is ignored; when trusted, the internal clock is re-anchored to it. Clips without audio, and reverse playback, use a monotonic clock.
76. **Starting playback** – the decoder first decodes from the keyframe to the start frame (preroll) and shows it. Only then do the clock and audio start, so long-GOP footage starts in sync.
77. **Frame dropping** – frames already late are skipped before conversion/rendering. Frames rendered too late are skipped before display. A catch-up seek happens only when more than 2 s behind, and is disabled for the current playback once a seek lands no further ahead (long GOP). Up to 4 render workers process frames in parallel; output stays in order. The actual fps is shown next to the volume.
78. **Shared stats** – whole-frame stats (dehaze airlight) are reused across video frames and refreshed every 30 frames.
79. **Speed-ups for preview and export alike** – (a) 8-bit input is linearised through an exact 256-entry table. (b) The dehaze dark channel is computed at a resolution where its window radius is ~3 px and then upsampled; the working scale follows the radius, so preview, export and region renders agree.
80. **Paused frames are "stills"** – a paused frame becomes a preview-engine source. All photo tools then work on video frames (eyedropper, auto buttons, crop overlay, split view, histogram, clipping, 100 % region rendering from a full-resolution decode).
81. **J / K / L** – L plays forward, pressing it again doubles the speed (up to 8×). J plays in reverse: silent, shown as stills about 8 times a second, because reverse decoding is expensive. K pauses. `,` / `.` step one frame. ←/→ jump ±5 s (Shift: ±1 s). ↑/↓ change the volume by 10 %.
82. **Space** – tapped on the canvas = play/pause; held + drag = pan (both spec meanings coexist). Transport buttons take no keyboard focus, so keys always reach the viewer.
83. **Segment** – stored as `video.segment`, or `null` when it covers the whole clip (keeps the file "Unedited"). Handles keep start < end − 1 frame. Segment changes are saved and autosaved but are **not** part of the undo history (only the `edit` section is).
84. **Snapshot** – full-resolution frame through the same pipeline (with the current crop/rotation), JPEG quality 95, written as `.part` then renamed. EXIF: capture date = video capture date + position, camera model, software. A name conflict gets `_2`, `_3` … (never overwrites). The file is added to `derived_files`, so "Hide exported files" (on by default) keeps it out of the list; a status message names the file.
85. **Source profile** – shown and changed in the viewer toolbar. Auto-detected only for HDR/HLG (PQ/HLG transfer); Flat/Log are user-set and override detection. In v1 the profile does not change processing; HDR/HLG shows a warning.
86. **Info overlay** (Cmd/Ctrl+I) – file name, camera, resolution, codec/bit depth/pixel format, transfer, source profile, duration/fps, capture date. Drawn on the canvas; the setting is remembered.
87. **Known noise** – OpenCV and PyAV each bundle `libavdevice`; macOS logs a duplicate Objective-C class warning when both load. Only AVFoundation *capture* classes are affected, which ReefHue never uses, so it is harmless (see #22).

## Milestone 5 – Export

88. **Default export values** – a new project starts from the app's Settings values; each export run stores its choices in the project's `export_defaults`. "Overwrite" is never stored or preselected. Read-only projects cannot export, because results could not be recorded.
89. **Conflicts** – checked when the run is planned, not while it runs. The first conflict asks: Skip / Replace / Keep both (auto-number `_2`, `_3` …), with "apply to all remaining". *Replace* moves the existing file to the Trash; it is never deleted hard. Targets that clash *within* one run are auto-numbered.
90. **Overwrite** – the result is written as `<name>.part` next to the original. Then the original goes to the Trash (send2trash) and the result is renamed into place; if moving to the Trash fails, nothing is replaced. The edit and segment are reset to neutral and the undo history of that file is dropped (undo would apply the edit twice). The status shows "Exported (overwritten)". HEIC overwritten as JPEG gets a new `.jpg` name; the HEIC goes to the Trash.
91. **Photos** – same format as the source. HEIC → JPEG by default ("Keep HEIC" in the dialog); PNG/TIFF/WebP stay. JPEG uses 4:4:4 chroma at quality ≥ 90 (colour work) and 4:2:0 below. EXIF:
    - everything kept (camera, GPS, …);
    - Orientation set to 1 (pixels are upright) and the stale thumbnail removed;
    - PixelX/YDimension updated, DateTimeOriginal/Digitized/DateTime set when the capture date was corrected;
    - a malformed MakerNote is dropped if EXIF can't be written otherwise.

    The ICC profile is copied as-is (the pipeline treats input as sRGB, see #66). Deterministic triangular dither before 8-bit quantisation.
92. **16-bit PNG/TIFF** stay 16-bit (written with OpenCV, which cannot carry EXIF; a note is shown). Detection now reads the raw tile mode, because Pillow opens 16-bit RGB as 8-bit "RGB". This fixed a bug that silently reduced such sources to 8-bit in the preview too.
93. **Video re-encode** – used when anything in `edit` differs from neutral. Frames are decoded (PyAV), run through the full-resolution pipeline on up to 4 parallel workers in order, and piped as rgb24/rgb48le to FFmpeg:
    - **Codec:** same family as the source (HEVC → libx265 tagged `hvc1`; H.264 and anything else → libx264). 10-bit sources stay 10-bit (`main10` / `high10`; VideoToolbox H.264 is 8-bit only).
    - **Colour:** BT.709 for ≥ 720p (BT.601 below), limited range, explicitly tagged.
    - **Quality:** CRF/preset — x264 28/20/16 and x265 30/22/18 with veryfast/medium/slow for Draft/High/Very high. VideoToolbox constant quality 45/65/80.
    - **Size:** odd output sizes lose one pixel row/column (yuv420).
94. **Lossless cut** – if nothing but the segment is set (or nothing at all), the export is a stream copy. It starts at the keyframe before the segment start, so it can be up to one GOP longer; verification tolerance 3 s.
95. **Audio, metadata, telemetry** – audio is copied (no re-encode) and trimmed with the segment (`-shortest`); "Remove audio" is stored per file in `export_options.keep_audio`. Global metadata comes from the source (`-map_metadata`, `use_metadata_tags`). A corrected capture date is written as `creation_time` (wall-clock time with `Z`, like GoPro), and `com.apple.quicktime.creationdate` is updated too if the source has it. GoPro GPMF streams (`gpmd`) are muxed in a second stream-copy step; if FFmpeg refuses, the file is kept without telemetry and a note is shown.
96. **Verification and safety** – ffprobe must find a video stream, and the duration must match the frames written (± max(0.5 s, 3 frames)). Free space is checked before each job (photos 3× the source size, videos ≈ 1.5× the segment's share, + 50 MB). `.part` files are removed on failure or cancel.
97. **Queue** – a dock panel at the bottom, shown when an export starts. Two photos run in parallel, videos one at a time. Pause stops new jobs and holds running ones at the next checkpoint (between frames). Cancel works per job or for all. The time estimate uses rough work units (12 MP photo ≈ 1). Failed jobs show their reason; "Copy error log" copies the details. Cancelled jobs are not recorded in `export_history`; failed ones are (result "failed"). The folder is rescanned at the end and a system notification appears (tray message; skipped where unavailable). Quitting while exporting asks first, then cancels and cleans up.
98. **Sleep** – macOS `caffeinate -i -w <pid>` while the queue runs (ends with the app even after a crash); Windows `SetThreadExecutionState`.
99. **CLI** – `python -m reefhue.pipeline.cli IN OUT [--edit …]` now uses the same export code (metadata, video via FFmpeg) and never overwrites an existing file.

## Findings with real GoPro files (HERO10: GX012809.MP4, GOPR3271.JPG)

100. **GoPro camera model** – GoPro videos carry no make/model in the container, so the model is derived from the firmware tag prefix (H19 = HERO8, H20 = HERO9, H21 = HERO10, H22 = HERO11, H23 = HERO12, H24 = HERO13; HD5–HD8 for older models).
101. **GoPro JPEGs are MPO** (multi-picture JPEG) according to Pillow; they are shown and handled as JPEG.
102. **Full-range video** (GoPro `yuvj420p`, range "pc") is decoded correctly (identical to FFmpeg's BT.709 full-range conversion). Re-encoded exports are written as standard limited-range BT.709 and tagged so.
103. **Duplicated container tags** – keeping custom tags (`use_metadata_tags`) made FFmpeg write standard keys twice, which readers see joined with ";". Brand/encoder keys are now cleared from the custom set. `creation_time` stays in both places (same value); ReefHue's reader takes the first of joined values.
104. **Telemetry verified** – GPMF (`gpmd`, "GoPro MET") is kept by both the lossless cut and the re-encode path with real HERO10 footage. The `tmcd` timecode track is kept by the lossless cut only.
105. **Optional real-media tests** (`tests/test_real_media.py`) use `GX*.MP4` / `GOPR*.JPG` from `REEFHUE_REAL_MEDIA` or the project folder and are skipped when none are present. Exports go to a temp folder with symlinks; the originals are only read.

## Milestone 6 – magic button, LUTs, presets

106. **Auto analysis input** – the source after geometry (crop/rotation), downscaled to ≤ 512 px, in linear light. Previous auto gains, levels and white balance are ignored during analysis, so pressing the button twice gives the same result (no feedback).
107. **Cast estimate** – gray world on the prior-corrected image with green as reference. Blue gain limited to 0.5–2.5×. Red is only partially compensated (`(g/r)^0.85`, at most 4×) and capped so that depth prior × residual red ≤ the soft gain limit (8×). Without the cap, a real GoPro clip got about 15× red, mostly noise.
108. **Texture weighting** – pixels count by local detail (Sobel magnitude, normalised to the 75th percentile, floor 0.15). Large areas of open water would otherwise dominate the gray-world average and push reef/sand towards red. A veiling-light subtraction was tried and dropped: it made the synthetic tests worse.
109. **Depth estimate** – only when `depth_m` is 0 or the previous value was itself estimated (`auto_result.depth_estimated`). Depth = ln(B/R) / (K_r − K_b) × 0.8 (the prior covers most of the cast, the residual the rest), divided by the lamp factor with artificial light. A user-entered depth is kept; changing the depth slider clears the "estimated" flag.
110. **Levels** – percentiles 0.5 / 99.5 per channel, measured on the image as it reaches the levels stage (each image runs through the real pipeline up to levels, then the pixels are pooled). Limited stretch: black ≤ 0.12, white ≥ 0.75. The master levels are not touched.
111. **White balance reset** – the magic button sets temperature/tint to 0 because the auto gains already neutralise the cast; other sliders (exposure, contrast, …) are left alone. The suggestion step for exposure/contrast stays with the existing Auto buttons (SPEC §9.4).
112. **Video** – 7 frames evenly spread over the segment (or the whole clip), avoiding the first/last frame; "Current frame only" uses the playback position. `auto_result.source` is "segment" or "frame".
113. **Undo / selection** – each analysed file is one undo step. "Apply to selection" analyses the files one after another on a worker thread, with progress in the Underwater tab; files that fail to decode are skipped.
114. **Synthetic acceptance test** – a textured chart (gray patches, colours, sand, an open-water band) submerged with per-channel attenuation plus veiling light (blue 8 m, green 5 m, coastal 6 m). The gray-patch cast must fall by ≥ 30 %; measured: blue 1.81 → 1.10, green 1.45 → 0.59.
115. **LUT folders** – bundled and imported LUTs live in `luts/` next to the app (SPEC §2). The per-user folder `<app data>/luts` is the fallback when the app folder is not writable. A name found in both folders resolves to the app folder. Sidecars store the file name only, never a path.
116. **LUT import** – the file is validated first (parsed fully) and copied atomically. An existing name gets `_2`, `_3`, …; nothing is overwritten.
117. **Missing / changed LUT** – a missing LUT is ignored by the pipeline (image unchanged) and the LUT tab shows a notice, with the name kept in the selector as "(missing)". A changed file (SHA-256 differs) is used as it is now, with a notice. Re-selecting the LUT stores the new hash.
118. **.cube details** – a plain 1D LUT uses DOMAIN_MIN/MAX as its input range; a shaper (1D + 3D) uses `LUT_1D_INPUT_RANGE` or 0–1. Input outside the domain is clamped. Trilinear interpolation. Unknown keywords are ignored; a wrong row count or size is an error.
119. **Bundled LUTs** – four own creative looks (33³, generated by `tools/make_starter_content.py`, free of third-party licences): Warm Reef, Clear Blue, Film Fade, Punchy. No camera Log → Rec.709 LUTs are bundled (vendor LUTs have their own licences); users can import them.
120. **Starter presets** – Shallow 5 m, Blue water 20 m, Green water / lake, With lamp; stored in `reefhue/resources/presets/` with translated names. They are listed first, cannot be deleted, and contain no geometry or LUT.

## Milestone 7 – polish

121. **Refresh = F5 everywhere** – on macOS Qt's standard "Refresh" is Cmd+R, so F5 (SPEC table) is added explicitly; Cmd+R stays as a second shortcut. Duplicate key sequences on one action are dropped. A test checks every global shortcut from the SPEC table and that no key is used twice.
122. **Tooltips** – every parameter has a tooltip (`param.<group>.<key>.tip`, all languages, enforced by a test) plus a line on resetting and step keys. Buttons get `<text key>_tip` automatically when such a key exists. The extension check boxes in the filter popup (labelled with the extension itself) have none.
123. **Folder access** – checked when a folder is opened, without writing anything into it (`os.access`). Unreadable (e.g. macOS privacy settings) → message with the System Settings path and the folder is not opened. Not writable → opened read-only with a notice (nothing saved, no export). A folder that disappears or becomes unreadable while open (seen at refresh/folder watch) → message, the folder is closed and a missing folder is removed from the recent list. An unreadable *project* folder now raises during the scan instead of making every file look deleted; unreadable subfolders are still skipped.
124. **Filter empty state** – when type/status/name filters hide every file, the list shows a hint with a "Clear filters" link. The extension list and "Hide exported files" are not reset by it, because they are deliberate folder settings.
125. **About dialog** – version, system, Python, Qt/PySide6, library versions, FFmpeg version/architecture/path and the app data folder, plus "Copy system info" for bug reports. Long values are shortened on screen; the copy contains them in full.
126. **Performance: parallel strips** – per-pixel stages (`to_linear`, `to_display`, `levels`, `tone`, `color`, `curves`) and the LUT run on horizontal strips in a shared thread pool (`pipeline/parallel.py`, ≤ 8 threads, only for images ≥ 256 k pixels). Results are identical to a single call. Stages that look at neighbouring pixels (geometry, denoise, dehaze, clarity, sharpen) are not split.
127. **Performance: LUT** – the 3D lookup gathers from a flat table (8 `take` calls) instead of fancy indexing with three index arrays. Same values (≤ 6e-8 difference). The LUT step went from 280 ms to 28 ms at 1600×1200 and from 4.1 s to 0.33 s at 23 MP.
128. **Measured** (MacBook Pro M-series, all edits on incl. LUT): preview render 542 → 140 ms at 1600×1200; full 23 MP render 7.0 → 2.4 s; 2.7K60 HEVC playback stays at ≈ 59 fps; start-up ≈ 0.6 s; a folder with 400 photos lists in 0.05 s with no UI stall above 40 ms.
129. **Version 0.9.0** – all v1 milestones are implemented; 1.0 is left for after user testing.
130. **README** – rewritten as a user guide: install and first start on macOS (Python, Gatekeeper "right-click → Open", first-start package install, privacy prompts), Windows, FFmpeg, a short tour, the full shortcut table, data locations and troubleshooting.

## Publishing on GitHub

131. **Sample files** – the demo files (GoPro HERO10 photo `GOPR2797.JPG`, 3 s clip `GX012993.MP4`) are committed in `samples/` as copies with the camera serial number overwritten by `X` (same length, nothing else changed; it was in the photo's EXIF and GoPro metadata and three times in the video). The GPS track has no position fix. Camera files in the project root stay private (ignored); the real-media tests now use `samples/` by default and pick the video frame for the FFmpeg comparison by frame number (a rounded `-ss` time could land on the next frame).
