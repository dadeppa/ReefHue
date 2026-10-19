# ReefHue

Colour correction for underwater photos and videos – a desktop app for macOS (Apple Silicon) and Windows.

> Version 0.9.0 – all features of version 1 are in place; testing and feedback before 1.0.

ReefHue brings back the colours that water swallows: open a dive folder, press the magic button
(or move the sliders yourself), and export corrected copies. Your originals stay untouched.

## Download and install

**New to this? Follow the step-by-step guide: [INSTALL.md](INSTALL.md)** (Mac and Windows, no
experience needed).

In short:

1. Install **Python 3.12** from <https://www.python.org/downloads/release/python-31210/>
   (Mac: *macOS 64-bit universal2 installer*; Windows: *Windows installer (64-bit)*, tick
   *Add python.exe to PATH*).
2. Download **ReefHue-….zip** from the [latest release](https://github.com/dadeppa/ReefHue/releases/latest)
   (under *Assets*) and unpack it.
3. Start **`start.command`** (Mac) or **`start.bat`** (Windows). The first start installs the Python
   packages into a `.venv` folder inside ReefHue (internet needed, a few minutes, about 1.5 GB).
   - Mac: the app is not signed by Apple. When macOS says it could not verify `start.command`, open
     *System Settings → Privacy & Security* and click **Open Anyway** (once).
   - Windows: if SmartScreen warns, choose *More info → Run anyway*.
4. Optional, for videos: install FFmpeg (see below).

Requirements: macOS on Apple Silicon or Windows 10/11 (64-bit), Python 3.11–3.13 (3.12 recommended).
Packages are re-installed only when `requirements.txt` changes; if the environment is ever broken,
delete the `.venv` folder and start again.

## FFmpeg

ReefHue does not include FFmpeg. Photo editing works without it; video playback, video
metadata and video export need `ffmpeg` and `ffprobe`. ReefHue looks for both tools **together**
(from the same place) in this order:

1. the `ffmpeg/` folder next to `start.command`,
2. Homebrew: `/opt/homebrew/bin` (Apple Silicon), then `/usr/local/bin` (Intel),
3. the system `PATH`.

**macOS (Apple Silicon)**

- **Recommended:** `brew install ffmpeg` – a native arm64 build, easy to update, found automatically.
- **Alternative:** put a **statically linked** arm64 build of `ffmpeg` and `ffprobe` into the
  `ffmpeg/` folder. ReefHue tries to remove the macOS quarantine flag at start; if that fails, run
  `xattr -dr com.apple.quarantine /path/to/ReefHue/ffmpeg`.
  Do **not** copy Homebrew's `ffmpeg` there – it is dynamically linked and needs other Homebrew files.
- **Fallback:** an Intel static build (e.g. from evermeet.cx) runs through Rosetta 2. It works but is
  not recommended; ReefHue shows a notice. If Rosetta is missing: `softwareupdate --install-rosetta`.

**Windows:** copy `ffmpeg.exe` and `ffprobe.exe` (64-bit) into the `ffmpeg\` folder or install FFmpeg on the `PATH`.

Video export needs the encoders `libx264` and `libx265`; the optional hardware encoder uses
`h264_videotoolbox` / `hevc_videotoolbox`. Check what your FFmpeg has:

```sh
ffmpeg -hide_banner -encoders | grep -E "libx264|libx265|videotoolbox"
```

Downloads: <https://ffmpeg.org/download.html>. *Help → FFmpeg help…* shows which binary, version and
architecture is in use, and has a "Check again" button.

**Python on Apple Silicon:** use the native (arm64) Python 3.12 from python.org or Homebrew. The
launcher refuses an Intel Python running through Rosetta, because it would install Intel packages.

## Using ReefHue

**Open a folder** (*File → Open folder…*, the folder icon, or drag a folder onto the window). The
left side lists the photos (JPEG, PNG, HEIC, TIFF, WebP) and videos (MP4, MOV, M4V, AVI, MKV) with
capture date and status; *Include subfolders*, the type/status filters and the name search narrow
the list. ReefHue never opens the last folder automatically – use *File → Open recent*.

**Edit** the selected file in the tabs below the preview: *Underwater, Light, Color, Curves,
Details, Geometry, LUT & Presets*. Every slider has a tooltip; double-click a slider to reset it,
use the arrow keys or +/− to step (Shift = 10×, Alt/Option = 0.1×). *Original* and *Split view* compare
with the unedited picture; hold `\` for a quick look at the original.

**Magic button** – *Underwater → Auto (magic)* analyses the picture (for videos 7 frames across the
segment, or only the current frame) and writes ordinary, editable values: depth (estimated if you
left it at 0), a residual colour correction and per-channel levels. Each run is one undo step;
*Apply to selection* analyses every selected file on its own.

**Videos** play with sound; the timeline has two handles for the part you want to keep (`I` / `O`).
`S` or the camera button saves the current frame, corrected, as a JPEG next to the video.

**LUTs and presets** – LUTs (`.cube`) live in the `luts/` folder next to the app; *LUT & Presets →
Import…* copies a file there (or into your app data folder if that is read-only). Four looks are
included. If a LUT used by a file goes missing or changes, the LUT tab tells you. Four built-in presets
(shallow, blue water, green water, with lamp) are a starting point; *Save current…* adds your own.
*Edit → Copy/Paste settings* transfers settings between files (geometry is off by default).

**Export** – *File → Export…* (or right-click → Export…) writes edited copies: next to the original
with a suffix (`_edited`), into a subfolder, or, after an extra confirmation, over the original
(the original then goes to the **Trash**, never deleted). *File → Export all edited…* exports
everything that was edited or changed since its last export. Exports run in the background (queue
at the bottom); you can keep editing. Photos keep EXIF/GPS and their colour profile; videos keep
their codec, 10-bit, audio, metadata and GoPro telemetry, and a segment-only cut without
corrections is copied losslessly.

### Keyboard shortcuts

On Windows use Ctrl instead of Cmd.

| Where | Key | Action |
|---|---|---|
| Everywhere | Cmd+O | Open folder |
| | Cmd+Z / Cmd+Shift+Z | Undo / redo (per file) |
| | Cmd+Shift+C / Cmd+Shift+V | Copy / paste settings |
| | Cmd+I | Info overlay |
| | F5 (Mac also Cmd+R) | Refresh the folder |
| File list | ↑ / ↓ | Previous / next file |
| | Cmd+A | Select all |
| Preview | F / 1 | Fit / 100 % |
| | R / Shift+R | Rotate 90° clockwise / counter-clockwise |
| | `\` (hold) | Show the original |
| | Space + drag | Pan |
| Video | Space | Play / pause |
| | ← / → | 5 s back / forward (Shift: 1 s) |
| | ↑ / ↓ | Volume |
| | `,` / `.` | One frame back / forward |
| | J / K / L | Reverse / pause / forward (press again = faster) |
| | I / O | Set segment start / end |
| | S | Save snapshot |
| Sliders | ↑ ↓ ← → or + − | Step (Shift ×10, Alt/Option ×0.1); double-click resets |

## Where ReefHue keeps its data

| What | macOS | Windows |
|---|---|---|
| Settings | `~/Library/Preferences/com.reefhue.ReefHue.plist` | Registry `HKCU\Software\ReefHue` |
| App data, presets, logs | `~/Library/Application Support/ReefHue/` | `%APPDATA%\ReefHue\` |
| Cache (thumbnails) | `~/Library/Caches/ReefHue/` | `%LOCALAPPDATA%\ReefHue\Cache\` |

Your photos and videos are never changed except when you explicitly export.

### The project file

When you change something in a folder (a capture date, a filter, your edits), ReefHue writes
`reefhue.project.json` into that folder, with a backup `reefhue.project.json.bak`. It uses relative
paths, so you can move or copy the whole folder to another computer. Opening a folder without
changing anything writes nothing. A write-protected folder opens read-only (a note says so).

## Troubleshooting

- **"start.command" Not Opened / "Apple could not verify …"** – open *System Settings → Privacy &
  Security*, scroll to *Security* and click **Open Anyway** (once).
- **"ReefHue is not allowed to read the folder"** – macOS blocked access. Open *System Settings →
  Privacy & Security → Files and Folders* (or *Full Disk Access*), allow *Terminal*, then open the
  folder again.
- **Videos show "Video features need FFmpeg"** – install FFmpeg (see above), then *Help → FFmpeg
  help… → Check again*.
- **The first start fails while installing** – check the internet connection, delete the `.venv`
  folder and start again.
- **Something else** – *Help → Open log folder* contains `reefhue.log`. *Help → About → Copy system
  info* copies the versions in use; please include both in a bug report.

## Sample files

`samples/` contains a GoPro HERO10 photo and a short 2.7K clip to try ReefHue with (open the folder
in the app). They are also used by the optional real-media tests.

## Development

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m reefhue          # run
.venv/bin/python -m pytest -q        # tests (Qt runs headless)
.venv/bin/python -m reefhue.pipeline.cli IN.jpg OUT.jpg --edit reefhue.project.json   # headless render
.venv/bin/python tools/make_starter_content.py                                        # rebuild bundled LUTs/presets
```

See `docs/SPEC.md` (specification), `docs/DECISIONS.md` (decisions) and `CLAUDE.md` (conventions for
AI-assisted development). `tools/make_release.py` builds the release ZIPs.

## License

MIT – see [LICENSE](LICENSE). The bundled LUTs, presets and sample files are covered by the same
licence. FFmpeg is not part of ReefHue and has its own licence.
