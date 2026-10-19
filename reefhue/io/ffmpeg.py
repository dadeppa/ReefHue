"""Locate and verify ``ffmpeg`` / ``ffprobe`` (SPEC §3 + Addendum A).

* Lookup (A1): both tools are resolved **together from the same location**:
  ``<app>/ffmpeg/`` (or its ``bin/``), then Homebrew (``/opt/homebrew/bin``,
  ``/usr/local/bin``) on macOS, then the system PATH. Nothing is downloaded or
  copied.
* Architecture (A2): the Mach-O header tells whether an Intel build runs
  through Rosetta 2 on Apple Silicon (non-blocking notice).
* Capabilities (A3): ``ffmpeg -hide_banner -encoders``; libx264/libx265 are
  required for video export, VideoToolbox encoders only for the optional
  hardware-encoder checkbox.

Runs subprocesses – call from a worker thread. No Qt here.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from reefhue.bootstrap import host_is_apple_silicon
from reefhue.core import paths

log = logging.getLogger(__name__)

DOWNLOAD_URL = "https://ffmpeg.org/download.html"
BREW_COMMAND = "brew install ffmpeg"
ROSETTA_COMMAND = "softwareupdate --install-rosetta"
VERIFY_COMMAND = 'ffmpeg -hide_banner -encoders | grep -E "libx264|libx265|videotoolbox"'
TOOLS = ("ffmpeg", "ffprobe")
RUN_TIMEOUT_S = 15
HOMEBREW_DIRS = (Path("/opt/homebrew/bin"), Path("/usr/local/bin"))
REQUIRED_ENCODERS = ("libx264", "libx265")
HW_ENCODERS = ("h264_videotoolbox", "hevc_videotoolbox")

# State values
OK = "ok"
MISSING = "missing"
BLOCKED = "blocked"  # found but cannot be executed

# Problem codes (mapped to i18n texts in the UI)
NOT_FOUND = "not_found"
NOT_EXECUTABLE = "not_executable"
FAILED_TO_RUN = "failed_to_run"

# Sources
SOURCE_FOLDER = "folder"
SOURCE_HOMEBREW = "homebrew"
SOURCE_PATH = "path"


@dataclass(frozen=True)
class ToolStatus:
    name: str
    path: Path | None
    source: str | None  # "folder" | "homebrew" | "path" | None
    version: str | None
    problem: str | None
    archs: frozenset[str] = frozenset()

    @property
    def ok(self) -> bool:
        return self.problem is None and self.path is not None


@dataclass(frozen=True)
class FfmpegStatus:
    ffmpeg: ToolStatus
    ffprobe: ToolStatus
    folder: Path
    quarantine_cleared: bool
    encoders: frozenset[str] | None = None  # None = could not be checked
    host_arm64: bool = False
    platform: str = field(default=sys.platform)

    @property
    def available(self) -> bool:
        return self.ffmpeg.ok and self.ffprobe.ok

    @property
    def state(self) -> str:
        if self.available:
            return OK
        if any(t.problem == NOT_FOUND for t in (self.ffmpeg, self.ffprobe)):
            return MISSING
        return BLOCKED

    @property
    def intel_on_apple_silicon(self) -> bool:
        archs = self.ffmpeg.archs
        return self.host_arm64 and bool(archs) and "arm64" not in archs and "x86_64" in archs

    @property
    def rosetta(self) -> bool:
        """An Intel build that runs through Rosetta 2 (works, but not recommended)."""
        return self.available and self.intel_on_apple_silicon

    @property
    def needs_rosetta_install(self) -> bool:
        return self.ffmpeg.problem == FAILED_TO_RUN and self.intel_on_apple_silicon

    @property
    def missing_encoders(self) -> tuple[str, ...]:
        if not self.available or self.encoders is None:
            return ()
        return tuple(e for e in REQUIRED_ENCODERS if e not in self.encoders)

    @property
    def export_available(self) -> bool:
        return self.available and self.encoders is not None and not self.missing_encoders

    @property
    def hw_encoders_available(self) -> bool:
        return self.available and self.encoders is not None and all(e in self.encoders for e in HW_ENCODERS)

    @property
    def warnings(self) -> list[str]:
        """Non-blocking notices: ``"rosetta"``, ``"missing_encoders"``."""
        result = []
        if self.rosetta:
            result.append("rosetta")
        if self.missing_encoders:
            result.append("missing_encoders")
        return result


def executable_name(tool: str, platform: str = sys.platform) -> str:
    return f"{tool}.exe" if platform.startswith("win") else tool


# --------------------------------------------------------------------------- lookup (A1)
def candidate_locations(
    folder: Path,
    path_env: str | None = None,
    platform: str = sys.platform,
    homebrew_dirs: tuple[Path, ...] = HOMEBREW_DIRS,
) -> list[tuple[Path, str]]:
    """Directories to search, in order, with their source label."""
    locations: list[tuple[Path, str]] = [(folder, SOURCE_FOLDER), (folder / "bin", SOURCE_FOLDER)]
    if platform == "darwin":
        locations += [(d, SOURCE_HOMEBREW) for d in homebrew_dirs]
    env = os.environ.get("PATH", "") if path_env is None else path_env
    for entry in env.split(os.pathsep):
        if entry:
            locations.append((Path(entry), SOURCE_PATH))
    seen: set[Path] = set()
    unique = []
    for directory, source in locations:
        if directory not in seen:
            seen.add(directory)
            unique.append((directory, source))
    return unique


def find_pair(
    folder: Path,
    path_env: str | None = None,
    platform: str = sys.platform,
    homebrew_dirs: tuple[Path, ...] = HOMEBREW_DIRS,
) -> tuple[dict[str, Path | None], str | None]:
    """First location that contains **both** tools → ``({tool: path}, source)``.

    If no location has both, each tool's first single occurrence is returned
    (for messages) together with ``None`` as source.
    """
    locations = candidate_locations(folder, path_env, platform, homebrew_dirs)
    names = {tool: executable_name(tool, platform) for tool in TOOLS}
    for directory, source in locations:
        found = {tool: directory / name for tool, name in names.items()}
        if all(p.is_file() for p in found.values()):
            return found, source
    singles: dict[str, Path | None] = {tool: None for tool in TOOLS}
    for directory, _source in locations:
        for tool, name in names.items():
            if singles[tool] is None and (directory / name).is_file():
                singles[tool] = directory / name
    return singles, None


# --------------------------------------------------------------------------- architecture (A2)
_CPU_TYPES = {7: "i386", 0x01000007: "x86_64", 12: "arm", 0x0100000C: "arm64"}


def macho_archs(header: bytes) -> frozenset[str]:
    """Architectures of a Mach-O (thin or universal) binary from its first bytes."""
    if len(header) < 8:
        return frozenset()
    magic = header[:4]
    if magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):  # thin, little endian
        cpu = int.from_bytes(header[4:8], "little")
        return frozenset({_CPU_TYPES.get(cpu, f"cpu{cpu:#x}")})
    if magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):  # universal ("fat"), big endian
        count = int.from_bytes(header[4:8], "big")
        entry = 20 if magic == b"\xca\xfe\xba\xbe" else 32
        if not 0 < count < 32:  # e.g. a Java class file, which shares the magic
            return frozenset()
        archs = set()
        for i in range(count):
            offset = 8 + i * entry
            if offset + 4 > len(header):
                break
            cpu = int.from_bytes(header[offset : offset + 4], "big")
            archs.add(_CPU_TYPES.get(cpu, f"cpu{cpu:#x}"))
        return frozenset(archs)
    return frozenset()


def binary_archs(path: Path) -> frozenset[str]:
    try:
        with open(path, "rb") as fh:
            return macho_archs(fh.read(4096))
    except OSError:
        return frozenset()


# --------------------------------------------------------------------------- capabilities (A3)
def parse_encoders(text: str) -> frozenset[str]:
    """Encoder names from ``ffmpeg -hide_banner -encoders`` output."""
    names = set()
    started = False
    for line in text.splitlines():
        stripped = line.strip()
        if not started:
            started = stripped.startswith("------")
            continue
        parts = stripped.split()
        if len(parts) >= 2 and len(parts[0]) == 6:
            names.add(parts[1])
    return frozenset(names)


def _run(binary: Path, args: list[str]) -> tuple[subprocess.CompletedProcess | None, str | None]:
    try:
        result = subprocess.run(
            [str(binary), *args],
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_S,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except PermissionError:
        return None, NOT_EXECUTABLE
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("%s %s failed: %s", binary, " ".join(args), exc)
        return None, FAILED_TO_RUN
    if result.returncode != 0:
        log.warning("%s %s exited with %s: %s", binary, " ".join(args), result.returncode, result.stderr[:500])
        return None, FAILED_TO_RUN
    return result, None


def _run_version(binary: Path) -> tuple[str | None, str | None]:
    """Run ``<binary> -version``. Returns ``(first_line, problem)``."""
    result, problem = _run(binary, ["-version"])
    if result is None:
        return None, problem
    first = (result.stdout or "").strip().splitlines()
    return (first[0] if first else ""), None


def check_encoders(ffmpeg: Path) -> frozenset[str] | None:
    result, _problem = _run(ffmpeg, ["-hide_banner", "-encoders"])
    return parse_encoders(result.stdout) if result is not None else None


def clear_quarantine(folder: Path, platform: str = sys.platform) -> bool:
    """macOS only: ``xattr -dr com.apple.quarantine <folder>``. True on success."""
    if platform != "darwin" or not folder.is_dir():
        return False
    xattr = shutil.which("xattr") or "/usr/bin/xattr"
    try:
        result = subprocess.run(
            [xattr, "-dr", "com.apple.quarantine", str(folder)],
            capture_output=True,
            timeout=RUN_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("could not clear quarantine on %s: %s", folder, exc)
        return False
    # xattr returns non-zero when the attribute is absent on some files; that is fine.
    return result.returncode == 0


def _tool_status(tool: str, path: Path | None, source: str | None) -> ToolStatus:
    if path is None:
        return ToolStatus(tool, None, None, None, NOT_FOUND)
    archs = binary_archs(path) if sys.platform == "darwin" else frozenset()
    if source is None:  # found, but its partner is not in the same location
        return ToolStatus(tool, path, None, None, NOT_FOUND, archs)
    version, problem = _run_version(path)
    return ToolStatus(tool, path, source, version, problem, archs)


def unavailable_status(folder: Path | None = None) -> FfmpegStatus:
    """Status used when the check itself crashed: treat FFmpeg as unusable."""
    folder = paths.ffmpeg_dir() if folder is None else folder
    return FfmpegStatus(
        ToolStatus("ffmpeg", None, None, None, FAILED_TO_RUN),
        ToolStatus("ffprobe", None, None, None, FAILED_TO_RUN),
        folder,
        False,
    )


def check_ffmpeg(
    folder: Path | None = None,
    path_env: str | None = None,
    homebrew_dirs: tuple[Path, ...] = HOMEBREW_DIRS,
) -> FfmpegStatus:
    """Full startup check. Blocking (spawns processes) – call from a worker thread."""
    folder = paths.ffmpeg_dir() if folder is None else folder
    cleared = clear_quarantine(folder)
    found, source = find_pair(folder, path_env, homebrew_dirs=homebrew_dirs)
    ffmpeg = _tool_status("ffmpeg", found["ffmpeg"], source)
    ffprobe = _tool_status("ffprobe", found["ffprobe"], source)
    encoders = check_encoders(ffmpeg.path) if ffmpeg.ok and ffprobe.ok else None
    status = FfmpegStatus(ffmpeg, ffprobe, folder, cleared, encoders, host_is_apple_silicon())
    log.info(
        "ffmpeg check: state=%s source=%s ffmpeg=%s %s archs=%s ffprobe=%s encoders_missing=%s hw=%s",
        status.state, source, ffmpeg.path, ffmpeg.version, sorted(ffmpeg.archs), ffprobe.path,
        status.missing_encoders, status.hw_encoders_available,
    )
    return status
