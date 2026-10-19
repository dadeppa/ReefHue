"""Launcher helper (stdlib only – runs before dependencies are installed).

Used by ``start.command`` / ``start.bat`` (run from the app folder)::

    python -m reefhue.bootstrap probe   # exit 0 = this Python is usable, 2 = too old,
                                        #        3 = wrong architecture (Intel Python on Apple Silicon)
    python -m reefhue.bootstrap check   # like probe, then: 0 = requirements up to date, 1 = install needed
    python -m reefhue.bootstrap stamp   # record the current requirements hash after pip install

The stamp lives inside the venv, so a fresh venv always installs.
"""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from pathlib import Path

MIN_PYTHON = (3, 11)
RECOMMENDED_PYTHON = (3, 12)
STAMP_NAME = ".requirements.sha256"

EXIT_OK = 0
EXIT_INSTALL_NEEDED = 1
EXIT_TOO_OLD = 2
EXIT_WRONG_ARCH = 3

MSG_TOO_OLD = "ReefHue needs Python 3.11 or newer (3.12 recommended)."
MSG_WRONG_ARCH = (
    "This Python is an Intel (x86_64) build running through Rosetta on an Apple Silicon Mac.\n"
    "Please install the native Apple Silicon Python 3.12 from python.org or Homebrew."
)


def app_root() -> Path:
    return Path(__file__).resolve().parents[1]


def host_is_apple_silicon(platform_name: str = sys.platform) -> bool:
    """True on Apple Silicon – also when this process itself runs translated by Rosetta."""
    if platform_name != "darwin":
        return False
    if platform.machine() == "arm64":
        return True
    try:
        result = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"], capture_output=True,
                                text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip() == "1"


def python_problem(version_info=None, machine: str | None = None, platform_name: str = sys.platform,
                   apple_silicon: bool | None = None) -> int:
    """``EXIT_OK``, ``EXIT_TOO_OLD`` or ``EXIT_WRONG_ARCH`` for the given (or current) interpreter (A4)."""
    version_info = sys.version_info if version_info is None else version_info
    if tuple(version_info[:2]) < MIN_PYTHON:
        return EXIT_TOO_OLD
    if platform_name == "darwin":  # Windows: only the version is checked
        machine = platform.machine() if machine is None else machine
        apple_silicon = host_is_apple_silicon(platform_name) if apple_silicon is None else apple_silicon
        if apple_silicon and machine != "arm64":
            return EXIT_WRONG_ARCH
    return EXIT_OK


def requirements_hash(requirements: Path) -> str:
    return hashlib.sha256(requirements.read_bytes()).hexdigest()


def stamp_path(venv: Path) -> Path:
    return venv / STAMP_NAME


def needs_install(requirements: Path, venv: Path) -> bool:
    stamp = stamp_path(venv)
    if not stamp.is_file():
        return True
    return stamp.read_text(encoding="utf-8").strip() != requirements_hash(requirements)


def write_stamp(requirements: Path, venv: Path) -> None:
    stamp_path(venv).write_text(requirements_hash(requirements) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "check"
    problem = python_problem()
    if problem == EXIT_TOO_OLD:
        print(MSG_TOO_OLD, file=sys.stderr)
        return problem
    if problem == EXIT_WRONG_ARCH:
        print(MSG_WRONG_ARCH, file=sys.stderr)
        return problem
    if command == "probe":
        return EXIT_OK
    requirements = app_root() / "requirements.txt"
    venv = Path(sys.prefix)
    if command == "check":
        return EXIT_INSTALL_NEEDED if needs_install(requirements, venv) else EXIT_OK
    if command == "stamp":
        write_stamp(requirements, venv)
        return EXIT_OK
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
