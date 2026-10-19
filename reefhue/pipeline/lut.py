""".cube LUTs (SPEC §11): parser, interpolation and the LUT library. No Qt.

* Formats: 3D (``LUT_3D_SIZE``), 1D (``LUT_1D_SIZE``) and Resolve-style 1D shaper +
  3D in one file; ``DOMAIN_MIN``/``DOMAIN_MAX`` and ``LUT_1D/3D_INPUT_RANGE``.
  3D data order: red changes fastest, then green, then blue.
* Interpolation: per-channel linear (1D) and trilinear (3D), vectorised.
* Library: ``<app>/luts`` plus a per-user folder (used when the app folder is
  read-only). Files are referenced by name; the sidecar also stores their SHA-256.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from reefhue.core import paths
from reefhue.pipeline import parallel

log = logging.getLogger(__name__)

MAX_3D_SIZE = 129
MAX_1D_SIZE = 65536


class LutError(ValueError):
    pass


@dataclass
class CubeLut:
    title: str
    domain_min: np.ndarray
    domain_max: np.ndarray
    table3d: np.ndarray | None = None   # (n, n, n, 3) indexed [b, g, r]
    table1d: np.ndarray | None = None   # (n, 3)
    range1d: tuple[float, float] = (0.0, 1.0)  # input range of a shaper / 1D LUT

    @property
    def size(self) -> int:
        return int(self.table3d.shape[0]) if self.table3d is not None else int(self.table1d.shape[0])

    # ---- application -------------------------------------------------------------
    def apply_1d(self, img: np.ndarray, table: np.ndarray, lo: float, hi: float) -> np.ndarray:
        n = table.shape[0]
        x = (np.clip(img, lo, hi) - np.float32(lo)) * np.float32((n - 1) / max(hi - lo, 1e-9))
        i0 = np.minimum(x.astype(np.int32), n - 2)
        f = (x - i0).astype(np.float32, copy=False)
        out = np.empty(img.shape, dtype=np.float32)
        for c in range(3):
            col = table[:, c]
            lo_v = col[i0[..., c]]
            out[..., c] = lo_v + (col[i0[..., c] + 1] - lo_v) * f[..., c]
        return out

    def apply_3d(self, img: np.ndarray) -> np.ndarray:
        """Trilinear interpolation on a flat table (red fastest, then green, then blue)."""
        t = self.table3d
        n = t.shape[0]
        flat = t.reshape(-1, 3)
        lo, hi = self.domain_min, self.domain_max
        x = (np.clip(img, lo, hi) - lo) * ((n - 1) / np.maximum(hi - lo, 1e-9)).astype(np.float32)
        x = x.astype(np.float32, copy=False)
        i0 = np.minimum(x.astype(np.int32), n - 2)  # x ≥ 0, so truncation == floor
        f = x - i0
        base = (i0[..., 2] * n + i0[..., 1]) * n + i0[..., 0]
        fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
        sg, sb = n, n * n

        def corner(offset: int) -> np.ndarray:
            return np.take(flat, base + offset if offset else base, axis=0)

        c00 = corner(0)
        c00 += (corner(1) - c00) * fr
        c10 = corner(sg)
        c10 += (corner(sg + 1) - c10) * fr
        c01 = corner(sb)
        c01 += (corner(sb + 1) - c01) * fr
        c11 = corner(sb + sg)
        c11 += (corner(sb + sg + 1) - c11) * fr
        c00 += (c10 - c00) * fg
        c01 += (c11 - c01) * fg
        c00 += (c01 - c00) * fb
        return c00

    def _apply(self, img: np.ndarray) -> np.ndarray:
        out = img
        if self.table1d is not None:
            lo, hi = self.range1d
            out = self.apply_1d(out, self.table1d, lo, hi)
        if self.table3d is not None:
            out = self.apply_3d(out)
        return out

    def __call__(self, img: np.ndarray) -> np.ndarray:
        return parallel.map_rows(self._apply, np.asarray(img, dtype=np.float32))


# --------------------------------------------------------------------------- parsing
_FLOAT = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_TRIPLE = re.compile(rf"^\s*({_FLOAT})\s+({_FLOAT})\s+({_FLOAT})\s*$")


def parse_cube(text: str) -> CubeLut:
    title = ""
    size3 = size1 = 0
    dmin = np.zeros(3, np.float32)
    dmax = np.ones(3, np.float32)
    range1: tuple[float, float] | None = None
    range3: tuple[float, float] | None = None
    rows: list[tuple[float, float, float]] = []
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _TRIPLE.match(line)
        if match:
            rows.append(tuple(float(v) for v in match.groups()))  # type: ignore[arg-type]
            continue
        key, _, rest = line.partition(" ")
        key = key.upper()
        values = rest.split()
        try:
            if key == "TITLE":
                title = rest.strip().strip('"')
            elif key == "LUT_3D_SIZE":
                size3 = int(values[0])
            elif key == "LUT_1D_SIZE":
                size1 = int(values[0])
            elif key == "DOMAIN_MIN":
                dmin = np.array([float(v) for v in values[:3]], np.float32)
            elif key == "DOMAIN_MAX":
                dmax = np.array([float(v) for v in values[:3]], np.float32)
            elif key == "LUT_1D_INPUT_RANGE":
                range1 = (float(values[0]), float(values[1]))
            elif key == "LUT_3D_INPUT_RANGE":
                range3 = (float(values[0]), float(values[1]))
            else:
                log.debug("cube: ignoring line %d: %s", line_no, line)
        except (IndexError, ValueError) as exc:
            raise LutError(f"line {line_no}: {line!r}") from exc
    if not size3 and not size1:
        raise LutError("no LUT_3D_SIZE or LUT_1D_SIZE")
    if size3 and not 2 <= size3 <= MAX_3D_SIZE or size1 and not 2 <= size1 <= MAX_1D_SIZE:
        raise LutError("unsupported LUT size")
    expected = size1 + size3 ** 3
    if len(rows) != expected:
        raise LutError(f"expected {expected} data rows, found {len(rows)}")
    data = np.asarray(rows, dtype=np.float32)
    lut = CubeLut(title, dmin, dmax)
    if size1:
        lut.table1d = data[:size1]
        if range1 is not None:
            lut.range1d = range1
        elif not size3:  # a plain 1D LUT uses its DOMAIN; a shaper defaults to 0–1
            lut.range1d = (float(dmin.min()), float(dmax.max()))
    if size3:
        lut.table3d = data[size1:].reshape(size3, size3, size3, 3)  # [b][g][r], red fastest
        if range3 is not None:
            lut.domain_min = np.full(3, range3[0], np.float32)
            lut.domain_max = np.full(3, range3[1], np.float32)
    return lut


def load_cube(path: Path) -> CubeLut:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise LutError(str(exc)) from exc
    return parse_cube(text)


def write_cube(path: Path, table3d: np.ndarray, title: str = "") -> None:
    """Write a 3D LUT (``table3d`` indexed [b, g, r]) as .cube."""
    n = table3d.shape[0]
    lines = [f'TITLE "{title}"'] if title else []
    lines += [f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    for b in range(n):
        for g in range(n):
            for r in range(n):
                v = table3d[b, g, r]
                lines.append(f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def identity_table(n: int) -> np.ndarray:
    grid = np.linspace(0.0, 1.0, n, dtype=np.float32)
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")
    return np.stack([r, g, b], axis=-1)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------- library
def app_lut_dir() -> Path:
    return paths.app_root() / "luts"


def user_lut_dir() -> Path:
    path = paths.data_dir() / "luts"
    path.mkdir(parents=True, exist_ok=True)
    return path


class LutLibrary:
    """Finds, loads (cached, thread-safe) and imports .cube files."""

    CACHE_SIZE = 6

    def __init__(self, dirs: list[Path] | None = None) -> None:
        self._dirs = dirs
        self._cache: OrderedDict[tuple, CubeLut] = OrderedDict()
        self._lock = threading.Lock()

    def dirs(self) -> list[Path]:
        return list(self._dirs) if self._dirs is not None else [app_lut_dir(), user_lut_dir()]

    def names(self) -> list[str]:
        found: dict[str, Path] = {}
        for directory in self.dirs():
            if directory.is_dir():
                for path in directory.glob("*.cube"):
                    found.setdefault(path.name, path)
        return sorted(found, key=str.casefold)

    def resolve(self, name: str | None) -> Path | None:
        if not name:
            return None
        safe = Path(name).name  # names only: never a path outside the LUT folders
        for directory in self.dirs():
            candidate = directory / safe
            if candidate.is_file():
                return candidate
        return None

    def load(self, name: str) -> CubeLut | None:
        path = self.resolve(name)
        if path is None:
            return None
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        try:
            lut = load_cube(path)
        except LutError as exc:
            log.warning("cannot load LUT %s: %s", path, exc)
            return None
        with self._lock:
            self._cache[key] = lut
            while len(self._cache) > self.CACHE_SIZE:
                self._cache.popitem(last=False)
        return lut

    def check(self, name: str | None, sha256: str | None) -> str:
        """``"ok"``, ``"missing"`` or ``"changed"`` (file differs from the hash in the sidecar)."""
        if not name:
            return "ok"
        path = self.resolve(name)
        if path is None:
            return "missing"
        if sha256 and sha256_of(path) != sha256:
            return "changed"
        return "ok"

    def import_file(self, source: Path) -> Path:
        """Validate and copy a .cube into the LUT folder (app folder, else per-user folder)."""
        parse_cube(source.read_text(encoding="utf-8", errors="replace"))  # raises LutError if invalid
        for directory in self.dirs():
            try:
                directory.mkdir(parents=True, exist_ok=True)
                target = directory / source.name
                n = 2
                while target.exists():
                    target = directory / f"{source.stem}_{n}{source.suffix}"
                    n += 1
                tmp = target.with_name(target.name + ".part")
                shutil.copyfile(source, tmp)
                os.replace(tmp, target)
                return target
            except OSError as exc:  # read-only app folder → try the next one
                log.info("cannot import LUT into %s: %s", directory, exc)
        raise LutError("no writable LUT folder")


LIBRARY = LutLibrary()


def default_provider(name: str) -> CubeLut | None:
    return LIBRARY.load(name)
