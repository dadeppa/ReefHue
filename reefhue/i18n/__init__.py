"""Tiny JSON-based i18n (SPEC §4 "Languages").

* One file per language in this folder (``de.json``, ``en.json``, ``nl.json`` …).
  Adding a language = adding a file. Each file has a ``_meta`` object with
  ``native_name`` (shown in the language picker).
* Keys are stable identifiers (``"menu.file"``), never the English text.
* :func:`tr` looks up the current language, falls back to English, then to the
  key itself (and logs the miss once).

No Qt here; the UI re-translates itself when :func:`set_language` is called
via its own signal.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

FALLBACK = "en"
META_KEY = "_meta"
_DIR = Path(__file__).resolve().parent

_tables: dict[str, dict[str, str]] = {}
_meta: dict[str, dict] = {}
_current: str = FALLBACK
_missing_logged: set[tuple[str, str]] = set()


def i18n_dir() -> Path:
    return _DIR


def load_table(path: Path) -> tuple[dict[str, str], dict]:
    """Load one language file. Returns ``(strings, meta)``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: top level must be an object")
    meta = data.pop(META_KEY, {}) or {}
    strings = {str(k): str(v) for k, v in data.items()}
    return strings, meta


def _load_all(directory: Path = _DIR) -> None:
    _tables.clear()
    _meta.clear()
    for path in sorted(directory.glob("*.json")):
        code = path.stem
        try:
            strings, meta = load_table(path)
        except (OSError, ValueError) as exc:
            log.error("cannot load language file %s: %s", path, exc)
            continue
        _tables[code] = strings
        _meta[code] = meta


def _ensure_loaded() -> None:
    if not _tables:
        _load_all()


def reload(directory: Path | None = None) -> None:
    _load_all(_DIR if directory is None else directory)
    _missing_logged.clear()


def available_languages() -> dict[str, str]:
    """``{code: native_name}`` for every language file, sorted by code."""
    _ensure_loaded()
    return {code: str(_meta[code].get("native_name", code)) for code in sorted(_tables)}


def resolve_language(preferred: Iterable[str], available: Iterable[str] | None = None) -> str:
    """Pick the best available language for OS preferences like ``["de-DE", "en-US"]``.

    Exact match first, then the primary subtag (``de-AT`` → ``de``); fallback English.
    """
    avail = list(available_languages() if available is None else available)
    lowered = {a.lower(): a for a in avail}
    for tag in preferred:
        tag = (tag or "").replace("_", "-").lower()
        if not tag:
            continue
        if tag in lowered:
            return lowered[tag]
        primary = tag.split("-")[0]
        if primary in lowered:
            return lowered[primary]
    return FALLBACK


def set_language(code: str) -> str:
    """Activate ``code`` (unknown codes fall back to English). Returns the active code."""
    global _current
    _ensure_loaded()
    _current = code if code in _tables else FALLBACK
    log.info("language set to %s", _current)
    return _current


def current_language() -> str:
    return _current


def has_key(key: str, code: str | None = None) -> bool:
    _ensure_loaded()
    return key in _tables.get(code or _current, {})


def tr(key: str, **kwargs) -> str:
    """Translate ``key`` and ``str.format`` it with ``kwargs``."""
    _ensure_loaded()
    text = _tables.get(_current, {}).get(key)
    if text is None:
        text = _tables.get(FALLBACK, {}).get(key)
        if (_current, key) not in _missing_logged:
            _missing_logged.add((_current, key))
            log.warning("missing translation: %s [%s]", key, _current)
        if text is None:
            text = key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            log.warning("bad format arguments for %s: %s", key, kwargs)
    return text
