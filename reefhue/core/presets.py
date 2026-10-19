"""Edit sections for copy/paste and presets; user presets in the app data folder (SPEC §12). No Qt."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from reefhue import __version__
from reefhue.core import paths
from reefhue.core.timeutil import now_wallclock
from reefhue.pipeline.params import default_edit

log = logging.getLogger(__name__)

# Section id → keys of the ``edit`` dict it covers. Order = dialog order (SPEC §12).
SECTIONS: dict[str, tuple[str, ...]] = {
    "underwater": ("underwater",),
    "white_balance": ("white_balance",),
    "light": ("light",),
    "color": ("color",),
    "levels": ("levels",),
    "curves": ("curves",),
    "details": ("details",),
    "lut": ("lut",),
    "geometry": ("geometry",),
}
DEFAULT_SECTIONS = tuple(s for s in SECTIONS if s != "geometry")  # geometry unchecked by default


def extract(edit: dict, sections) -> dict:
    """The parts of ``edit`` belonging to ``sections`` (deep copy)."""
    return {key: copy.deepcopy(edit[key]) for s in sections for key in SECTIONS[s] if key in edit}


def apply(edit: dict, partial: dict, sections) -> dict:
    """Return a copy of ``edit`` with ``sections`` taken from ``partial`` (missing ones untouched)."""
    result = copy.deepcopy(edit)
    for s in sections:
        for key in SECTIONS[s]:
            if key in partial:
                result[key] = copy.deepcopy(partial[key])
    return result


def reset_sections(edit: dict, sections) -> dict:
    return apply(edit, default_edit(), sections)


def sections_in(partial: dict) -> list[str]:
    return [s for s, keys in SECTIONS.items() if all(k in partial for k in keys)]


# --------------------------------------------------------------------------- user presets
@dataclass
class Preset:
    name: str
    edit: dict
    path: Path | None = None
    builtin: bool = False
    name_key: str | None = None  # bundled presets: translated name (i18n key)

    @property
    def sections(self) -> list[str]:
        return sections_in(self.edit)


def presets_dir() -> Path:
    path = paths.data_dir() / "presets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip()).strip("._") or "preset"
    return slug[:60]


def save_preset(name: str, edit: dict, sections, directory: Path | None = None) -> Preset:
    directory = presets_dir() if directory is None else directory
    partial = extract(edit, sections)
    path = directory / f"{_slug(name)}.json"
    counter = 2
    while path.exists() and json.loads(path.read_text(encoding="utf-8")).get("name") != name:
        path = directory / f"{_slug(name)}_{counter}.json"
        counter += 1
    payload = {"name": name.strip(), "created": now_wallclock(), "app_version": __version__, "edit": partial}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return Preset(payload["name"], partial, path)


BUILTIN_ORDER = ("shallow_5m", "blue_water_20m", "green_water_lake", "with_lamp")


def builtin_dir() -> Path:
    return paths.resources_dir() / "presets"


def load_builtin_presets(directory: Path | None = None) -> list[Preset]:
    """Bundled starter presets (SPEC §12); read-only."""
    directory = builtin_dir() if directory is None else directory
    result = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result.append(Preset(path.stem, data["edit"], path, builtin=True, name_key=data.get("name_key")))
        except (OSError, ValueError, KeyError) as exc:
            log.warning("ignoring bundled preset %s: %s", path, exc)
    order = {name: i for i, name in enumerate(BUILTIN_ORDER)}
    result.sort(key=lambda p: (order.get(p.name, len(order)), p.name))
    return result


def load_presets(directory: Path | None = None) -> list[Preset]:
    directory = presets_dir() if directory is None else directory
    result = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            edit = data["edit"]
            if not isinstance(edit, dict):
                raise ValueError("edit is not an object")
            result.append(Preset(str(data.get("name") or path.stem), edit, path))
        except (OSError, ValueError, KeyError) as exc:
            log.warning("ignoring preset %s: %s", path, exc)
    result.sort(key=lambda p: p.name.casefold())
    return result


def delete_preset(preset: Preset) -> None:
    if preset.path is not None and not preset.builtin:
        preset.path.unlink(missing_ok=True)
