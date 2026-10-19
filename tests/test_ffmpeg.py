"""FFmpeg discovery, architecture and capability checks (SPEC §3 + Addendum A, A6)."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from reefhue import bootstrap
from reefhue.io import ffmpeg

posix_only = pytest.mark.skipif(sys.platform.startswith("win"), reason="uses shell-script fakes")

ENCODERS_FULL = """Encoders:
 V..... = Video
 A..... = Audio
 S..... = Subtitle
 .F.... = Frame-level multithreading
 ..S... = Slice-level multithreading
 ...X.. = Codec is experimental
 ....B. = Supports draw_horiz_band
 .....D = Supports direct rendering method 1
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V....D libx264rgb           libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 RGB (codec h264)
 V....D h264_videotoolbox    VideoToolbox H.264 Encoder (codec h264)
 V....D libx265              libx265 H.265 / HEVC (codec hevc)
 V....D hevc_videotoolbox    VideoToolbox H.265 Encoder (codec hevc)
 A....D aac                  AAC (Advanced Audio Coding)
"""
ENCODERS_NO_X265 = "\n".join(line for line in ENCODERS_FULL.splitlines()
                             if "libx265" not in line and "videotoolbox" not in line)


def _mach(cpu: int) -> bytes:
    return b"\xcf\xfa\xed\xfe" + cpu.to_bytes(4, "little") + bytes(24)


ARM64 = _mach(0x0100000C)
X86_64 = _mach(0x01000007)
UNIVERSAL = (b"\xca\xfe\xba\xbe" + (2).to_bytes(4, "big")
             + (0x01000007).to_bytes(4, "big") + bytes(16)
             + (0x0100000C).to_bytes(4, "big") + bytes(16))
JAVA_CLASS = b"\xca\xfe\xba\xbe\x00\x00\x00\x34" + bytes(16)


def _fake_tool(folder: Path, name: str, body: str = 'echo "{name} version 9.9-test"', executable=True) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ffmpeg.executable_name(name)
    if name == "ffmpeg" and "encoders" not in body:
        body = ('if [ "$2" = "-encoders" ]; then cat <<EOF\n' + ENCODERS_FULL + "EOF\nelse " + body + "; fi")
    path.write_text("#!/bin/sh\n" + body.format(name=name) + "\n")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _check(tmp_path, folder, path_dirs=(), brew=()):
    path_env = os.pathsep.join(str(p) for p in path_dirs) or str(tmp_path / "none")
    return ffmpeg.check_ffmpeg(folder, path_env=path_env, homebrew_dirs=tuple(brew))


def test_executable_name():
    assert ffmpeg.executable_name("ffmpeg", "win32") == "ffmpeg.exe"
    assert ffmpeg.executable_name("ffmpeg", "darwin") == "ffmpeg"


# ---------------------------------------------------------------- A1 lookup order
def test_candidate_order(tmp_path):
    folder = tmp_path / "ffmpeg"
    locs = ffmpeg.candidate_locations(folder, path_env=f"/x{os.pathsep}/y", platform="darwin",
                                      homebrew_dirs=(Path("/opt/homebrew/bin"), Path("/usr/local/bin")))
    assert locs == [
        (folder, "folder"), (folder / "bin", "folder"),
        (Path("/opt/homebrew/bin"), "homebrew"), (Path("/usr/local/bin"), "homebrew"),
        (Path("/x"), "path"), (Path("/y"), "path"),
    ]
    win = ffmpeg.candidate_locations(folder, path_env="/x", platform="win32")
    assert all(source != "homebrew" for _d, source in win)


def test_missing_everywhere(tmp_path):
    status = _check(tmp_path, tmp_path / "ffmpeg")
    assert not status.available and status.state == ffmpeg.MISSING
    assert status.ffmpeg.problem == ffmpeg.NOT_FOUND and status.ffprobe.problem == ffmpeg.NOT_FOUND


@posix_only
def test_app_folder_wins_over_homebrew_and_path(tmp_path):
    folder, brew, on_path = tmp_path / "ffmpeg", tmp_path / "brew", tmp_path / "pathdir"
    for d in (folder, brew, on_path):
        _fake_tool(d, "ffmpeg")
        _fake_tool(d, "ffprobe")
    status = _check(tmp_path, folder, [on_path], [brew])
    assert status.available and status.ffmpeg.source == "folder"
    assert status.ffmpeg.path == folder / "ffmpeg" and status.ffprobe.path == folder / "ffprobe"
    assert status.ffmpeg.version == "ffmpeg version 9.9-test"


@posix_only
def test_homebrew_wins_over_path(tmp_path):
    brew1, brew2, on_path = tmp_path / "opt_homebrew", tmp_path / "usr_local", tmp_path / "pathdir"
    for d in (brew2, on_path):
        _fake_tool(d, "ffmpeg")
        _fake_tool(d, "ffprobe")
    status = _check(tmp_path, tmp_path / "ffmpeg", [on_path], [brew1, brew2])
    assert status.ffmpeg.source == "homebrew" and status.ffmpeg.path == brew2 / "ffmpeg"
    _fake_tool(brew1, "ffmpeg")
    _fake_tool(brew1, "ffprobe")
    status = _check(tmp_path, tmp_path / "ffmpeg", [on_path], [brew1, brew2])
    assert status.ffmpeg.path == brew1 / "ffmpeg"  # /opt/homebrew before /usr/local


@posix_only
def test_tools_are_resolved_together_from_one_location(tmp_path):
    folder, brew = tmp_path / "ffmpeg", tmp_path / "brew"
    _fake_tool(folder, "ffmpeg")  # app folder only has ffmpeg …
    _fake_tool(brew, "ffmpeg")
    _fake_tool(brew, "ffprobe")  # … so the complete Homebrew pair is used
    status = _check(tmp_path, folder, brew=[brew])
    assert status.ffmpeg.path == brew / "ffmpeg" and status.ffprobe.path == brew / "ffprobe"


@posix_only
def test_split_pair_is_reported_missing(tmp_path):
    folder, on_path = tmp_path / "ffmpeg", tmp_path / "pathdir"
    _fake_tool(folder / "bin", "ffmpeg")
    _fake_tool(on_path, "ffprobe")
    status = _check(tmp_path, folder, [on_path])
    assert not status.available and status.state == ffmpeg.MISSING
    assert status.ffmpeg.path == folder / "bin" / "ffmpeg"  # still shown for the message


@posix_only
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root can execute anything")
def test_not_executable_is_blocked(tmp_path):
    folder = tmp_path / "ffmpeg"
    _fake_tool(folder, "ffmpeg", executable=False)
    _fake_tool(folder, "ffprobe")
    status = _check(tmp_path, folder)
    assert status.state == ffmpeg.BLOCKED and status.ffmpeg.problem == ffmpeg.NOT_EXECUTABLE


@posix_only
def test_crashing_binary_is_blocked(tmp_path):
    folder = tmp_path / "ffmpeg"
    _fake_tool(folder, "ffmpeg", body="exit 3")
    _fake_tool(folder, "ffprobe")
    status = _check(tmp_path, folder)
    assert status.state == ffmpeg.BLOCKED and status.ffmpeg.problem == ffmpeg.FAILED_TO_RUN


# ---------------------------------------------------------------- A2 architecture
@pytest.mark.parametrize(
    "header, expected",
    [
        (ARM64, {"arm64"}),
        (X86_64, {"x86_64"}),
        (UNIVERSAL, {"x86_64", "arm64"}),
        (JAVA_CLASS, set()),
        (b"#!/bin/sh\n", set()),
        (b"", set()),
    ],
)
def test_macho_archs(header, expected):
    assert ffmpeg.macho_archs(header) == frozenset(expected)


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("ffmpeg"), reason="needs a real macOS ffmpeg")
def test_real_binary_arch():
    archs = ffmpeg.binary_archs(Path(shutil.which("ffmpeg")).resolve())
    assert archs and archs <= {"arm64", "x86_64"}


def _status(archs, host_arm64=True, problem=None, encoders=frozenset(ffmpeg.parse_encoders(ENCODERS_FULL))):
    tool = ffmpeg.ToolStatus("ffmpeg", Path("/x/ffmpeg"), "folder", "v", problem, frozenset(archs))
    probe = ffmpeg.ToolStatus("ffprobe", Path("/x/ffprobe"), "folder", "v", None)
    return ffmpeg.FfmpegStatus(tool, probe, Path("/x"), False, encoders if problem is None else None, host_arm64)


def test_rosetta_notice_logic():
    assert _status({"x86_64"}).rosetta and _status({"x86_64"}).warnings == ["rosetta"]
    assert not _status({"arm64"}).rosetta
    assert not _status({"x86_64", "arm64"}).rosetta  # universal build runs natively
    assert not _status({"x86_64"}, host_arm64=False).rosetta  # Intel Mac
    failed = _status({"x86_64"}, problem=ffmpeg.FAILED_TO_RUN)
    assert failed.needs_rosetta_install and not failed.available


# ---------------------------------------------------------------- A3 encoders
def test_parse_encoders_full_and_missing_x265():
    full = ffmpeg.parse_encoders(ENCODERS_FULL)
    assert {"libx264", "libx265", "h264_videotoolbox", "hevc_videotoolbox", "aac"} <= full
    assert "Video" not in full and "=" not in full
    partial = ffmpeg.parse_encoders(ENCODERS_NO_X265)
    assert "libx264" in partial and "libx265" not in partial
    status = _status({"arm64"}, encoders=partial)
    assert status.missing_encoders == ("libx265",)
    assert not status.export_available and not status.hw_encoders_available
    assert status.warnings == ["missing_encoders"]
    ok = _status({"arm64"})
    assert ok.export_available and ok.hw_encoders_available and ok.warnings == []


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg on PATH")
def test_real_encoder_listing():
    encoders = ffmpeg.check_encoders(Path(shutil.which("ffmpeg")))
    assert encoders and "aac" in encoders


def test_help_texts_for_addendum_cases(qapp):
    from reefhue.ui.ffmpeg_dialog import help_texts, install_texts, status_lines

    rosetta = dict(help_texts(_status({"x86_64"})))
    assert rosetta["ffmpeg.dialog.help_rosetta_notice"] == "brew install ffmpeg"
    install = dict(help_texts(_status({"x86_64"}, problem=ffmpeg.FAILED_TO_RUN), platform="darwin"))
    assert install["ffmpeg.dialog.help_rosetta_install"] == "softwareupdate --install-rosetta"
    missing = dict(help_texts(_status({"arm64"}, encoders=ffmpeg.parse_encoders(ENCODERS_NO_X265))))
    assert "ffmpeg.dialog.help_missing_encoders" in missing
    mac = [k for k, _c in install_texts("darwin")]
    assert mac == ["ffmpeg.dialog.install_mac_brew", "ffmpeg.dialog.install_mac_static",
                   "ffmpeg.dialog.install_mac_rosetta", "ffmpeg.dialog.verify"]
    assert "videotoolbox" in dict(install_texts("darwin"))["ffmpeg.dialog.verify"]
    lines = " ".join(status_lines(_status({"x86_64"})))
    assert "Rosetta" in lines and "libx265 ✓" in lines


def test_clear_quarantine_is_noop_off_macos(tmp_path):
    assert ffmpeg.clear_quarantine(tmp_path, platform="win32") is False
    assert ffmpeg.clear_quarantine(tmp_path / "missing", platform="darwin") is False


def test_unavailable_status(tmp_path):
    status = ffmpeg.unavailable_status(tmp_path)
    assert not status.available and status.state == ffmpeg.BLOCKED


# ---------------------------------------------------------------- A4 Python architecture
@pytest.mark.parametrize(
    "version, machine, platform_name, apple_silicon, expected",
    [
        ((3, 12, 1), "arm64", "darwin", True, bootstrap.EXIT_OK),
        ((3, 12, 1), "x86_64", "darwin", True, bootstrap.EXIT_WRONG_ARCH),  # Intel Python under Rosetta
        ((3, 12, 1), "x86_64", "darwin", False, bootstrap.EXIT_OK),         # Intel Mac
        ((3, 10, 9), "arm64", "darwin", True, bootstrap.EXIT_TOO_OLD),
        ((3, 11, 0), "AMD64", "win32", False, bootstrap.EXIT_OK),           # Windows: version only
        ((3, 9, 0), "AMD64", "win32", False, bootstrap.EXIT_TOO_OLD),
    ],
)
def test_python_architecture_check(version, machine, platform_name, apple_silicon, expected):
    assert bootstrap.python_problem(version, machine, platform_name, apple_silicon) == expected


def test_python_check_uses_platform_machine(monkeypatch):
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "x86_64")
    assert bootstrap.python_problem((3, 12, 0), platform_name="darwin", apple_silicon=True) == bootstrap.EXIT_WRONG_ARCH
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "arm64")
    assert bootstrap.host_is_apple_silicon("darwin")
    assert not bootstrap.host_is_apple_silicon("win32")


def test_bootstrap_probe_on_this_interpreter():
    import subprocess

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-m", "reefhue.bootstrap", "probe"], cwd=root)
    assert result.returncode == 0
