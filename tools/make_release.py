"""Build the release downloads into dist/ (attach them to a GitHub release).

* ``ReefHue-<version>.zip`` – the app for users: a ``ReefHue/`` folder with the
  committed files minus everything marked ``export-ignore`` in .gitattributes
  (tests, tools, docs, samples …). Built with ``git archive`` from HEAD, so the
  executable bit of start.command and the line endings of start.bat are kept.
* ``ReefHue-samples.zip`` – the sample photo and video (``ReefHue samples/``).

Run from the repository root with a clean working tree:  python tools/make_release.py
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def version() -> str:
    text = (ROOT / "reefhue" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if git("status", "--porcelain", "--untracked-files=no").strip():
        print("Uncommitted changes – commit first, the release is built from HEAD.")
        return 1
    DIST.mkdir(exist_ok=True)
    app_zip = DIST / f"ReefHue-{version()}.zip"
    git("archive", "--format=zip", "--prefix=ReefHue/", "-o", str(app_zip), "HEAD")

    samples_zip = DIST / "ReefHue-samples.zip"
    with zipfile.ZipFile(samples_zip, "w") as zf:
        for rel in git("ls-files", "samples").split():
            src = ROOT / rel
            # photos/videos are already compressed: store them as they are
            method = zipfile.ZIP_DEFLATED if src.suffix.lower() in (".md", ".txt") else zipfile.ZIP_STORED
            zf.write(src, f"ReefHue samples/{src.name}", compress_type=method)

    for path in (app_zip, samples_zip):
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size / 1e6:.1f} MB  sha256 {sha256(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
