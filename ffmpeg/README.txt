FFmpeg for ReefHue
==================

ReefHue does not include FFmpeg. Photo editing works without it; videos need
ffmpeg and ffprobe. ReefHue looks for both tools together, in this order:

  1. this folder (or a "bin" subfolder),
  2. Homebrew: /opt/homebrew/bin, then /usr/local/bin  (macOS),
  3. the system PATH.

macOS (Apple Silicon)
  Recommended:  brew install ffmpeg
                (native arm64 build, found automatically - nothing to copy here)
  Alternative:  put a STATICALLY linked arm64 build of ffmpeg and ffprobe here.
                Never copy Homebrew's ffmpeg here - it needs other Homebrew files.
  Fallback:     an Intel static build runs through Rosetta 2 (slower, not recommended).
                If Rosetta is missing:  softwareupdate --install-rosetta

Windows
  ffmpeg.exe and ffprobe.exe (64-bit) in this folder, or FFmpeg on the PATH.

Video export needs the encoders libx264 and libx265. Check with:

  ffmpeg -hide_banner -encoders | grep -E "libx264|libx265|videotoolbox"

Download: https://ffmpeg.org/download.html

macOS may block programs downloaded from the internet ("quarantine").
ReefHue tries to remove this automatically at startup. If that does not work,
right-click ffmpeg and ffprobe in Finder, choose "Open" and confirm once, or run:

  xattr -dr com.apple.quarantine /path/to/ReefHue/ffmpeg
