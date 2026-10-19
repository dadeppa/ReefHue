"""System information for the About dialog and bug reports. No Qt."""

from __future__ import annotations

import importlib
import platform
import sys

# (display name, import name, version attribute)
LIBRARIES = (
    ("NumPy", "numpy", "__version__"),
    ("OpenCV", "cv2", "__version__"),
    ("Pillow", "PIL", "__version__"),
    ("pillow-heif", "pillow_heif", "__version__"),
    ("PyAV", "av", "__version__"),
    ("piexif", "piexif", "VERSION"),
)


def library_versions() -> list[tuple[str, str]]:
    result = []
    for name, module, attr in LIBRARIES:
        try:
            version = str(getattr(importlib.import_module(module), attr, "?"))
        except Exception:  # noqa: BLE001 – a broken optional library must not break the dialog
            version = "–"
        result.append((name, version))
    return result


def platform_text() -> str:
    """e.g. "macOS 26.0 (arm64)" or "Windows 11 (AMD64)"."""
    machine = platform.machine() or "?"
    if sys.platform == "darwin":
        name = f"macOS {platform.mac_ver()[0] or '?'}"
    elif sys.platform.startswith("win"):
        name = f"Windows {platform.release()}"
    else:
        name = f"{platform.system()} {platform.release()}"
    return f"{name} ({machine})"


def python_text() -> str:
    return f"{platform.python_version()} ({platform.machine() or '?'})"


def ffmpeg_version(first_line: str | None) -> str | None:
    """'ffmpeg version 8.0.1 Copyright …' → '8.0.1'."""
    if not first_line:
        return None
    parts = first_line.split()
    if len(parts) >= 3 and parts[1] == "version":
        return parts[2]
    return first_line.strip()
