"""core/, pipeline/, io/, i18n/ (and the launcher helper) must not import Qt (SPEC §16)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADLESS_PACKAGES = ("core", "pipeline", "io", "i18n")
QT_IMPORT = re.compile(r"^\s*(from|import)\s+(PySide6|PyQt\d|shiboken6|qtpy)\b", re.MULTILINE)


def test_headless_sources_have_no_qt_imports():
    offenders = []
    for package in HEADLESS_PACKAGES:
        for path in (ROOT / "reefhue" / package).rglob("*.py"):
            if QT_IMPORT.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ROOT)))
    for path in (ROOT / "reefhue" / "bootstrap.py", ROOT / "reefhue" / "__init__.py"):
        if QT_IMPORT.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_importing_headless_modules_does_not_load_qt():
    modules = []
    for package in HEADLESS_PACKAGES:
        for path in sorted((ROOT / "reefhue" / package).rglob("*.py")):
            rel = path.relative_to(ROOT).with_suffix("")
            parts = list(rel.parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            modules.append(".".join(parts))
    modules.append("reefhue.bootstrap")
    code = (
        "import importlib, sys\n"
        f"for m in {modules!r}: importlib.import_module(m)\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('PySide6', 'shiboken6')]\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
