from __future__ import annotations

import json
import re
import string
from pathlib import Path

import pytest

from reefhue import i18n

ROOT = Path(__file__).resolve().parents[1]
I18N_DIR = ROOT / "reefhue" / "i18n"
REQUIRED_LANGUAGES = {"de", "en", "nl"}


def _tables() -> dict[str, dict[str, str]]:
    result = {}
    for path in I18N_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("_meta", None)
        result[path.stem] = data
    return result


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_required_languages_present_with_native_names():
    langs = i18n.available_languages()
    assert REQUIRED_LANGUAGES <= set(langs)
    assert langs["de"] == "Deutsch"
    assert langs["en"] == "English"
    assert langs["nl"] == "Nederlands"


def test_all_languages_have_the_same_keys():
    tables = _tables()
    reference = set(tables["en"])
    for code, table in tables.items():
        assert set(table) == reference, (
            code,
            sorted(reference - set(table)),
            sorted(set(table) - reference),
        )


def test_no_empty_strings_and_matching_placeholders():
    tables = _tables()
    for code, table in tables.items():
        for key, text in table.items():
            assert text.strip(), (code, key)
            assert _placeholders(text) == _placeholders(tables["en"][key]), (code, key)


def test_keys_are_identifiers_not_english_text():
    for key in _tables()["en"]:
        assert re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)+", key), key


# Keys built dynamically in the UI (f-strings) – enumerate them explicitly.
DYNAMIC_KEYS = (
    [f"theme.{c}" for c in ("system", "light", "dark")]
    + [f"settings.preview_quality.{c}" for c in ("auto", "50", "25")]
    + [f"export.mode.{c}" for c in ("suffix", "subfolder")]
    + [f"export.quality.{c}" for c in ("draft", "high", "very_high")]
    + [f"ffmpeg.dialog.heading_{s}" for s in ("ok", "missing", "blocked")]
    + [f"ffmpeg.source.{s}" for s in ("folder", "homebrew", "path")]
    + [f"status.{s}" for s in ("all", "unedited", "edited", "exported", "changed_after_export", "missing")]
    + [f"files.type.{t}" for t in ("all", "photo", "video", "other")]
    + [f"files.source.{s}" for s in ("exif", "ffprobe", "filesystem")]
    + [f"sidecar.warn.{w}" for w in ("newer_schema", "corrupt", "locked")]
    + ["sidecar.error.save"]
    + [f"section.{s}" for s in ("underwater", "white_balance", "light", "color", "levels", "curves", "details",
                                 "lut", "geometry")]
    + [f"editor.channel.{c}" for c in ("master", "r", "g", "b")]
    + [f"profile.{p}" for p in ("standard", "flat", "log", "hdr_hlg")]
    + [f"export.status.{s}" for s in ("queued", "running", "done", "failed", "skipped", "cancelled", "conflict_skipped")]
    + [f"export.error.{e}" for e in ("disk_space", "exists", "missing_source", "no_ffmpeg", "write", "ffmpeg", "verify",
                                     "unexpected", "read_only")]
    + [f"export.note.{n}" for n in ("no_metadata_16bit", "exif_dropped", "telemetry_dropped")]
    + ["export.notify_done", "export.notify_failed", "export.videos_unavailable_short", "status.exported_overwritten"]
    + [f"editor.lut_{s}" for s in ("missing", "changed")]
    + [f"preset.builtin.{p}" for p in ("shallow_5m", "blue_water_20m", "green_water_lake", "with_lamp")]
)


def test_every_key_used_in_code_exists():
    from reefhue.ui.edit_panel import TAB_KEYS

    from reefhue.pipeline import curves, geometry
    from reefhue.pipeline.params import PARAMS
    from reefhue.ui.file_model import COLUMN_KEYS

    generated = {p.label_key for p in PARAMS}
    generated |= {f"{p.label_key}.{c}" for p in PARAMS for c in p.choices}
    generated |= {f"editor.curve_preset.{n}" for n in curves.PRESETS}
    generated |= {f"editor.aspect.{a.replace(':', '_')}" for a in geometry.ASPECTS}

    used = set(DYNAMIC_KEYS) | generated | {f"editpanel.tab.{k}" for k in TAB_KEYS} | {f"files.col.{k}" for k in COLUMN_KEYS}
    literal = re.compile(r"""\btr\(\s*["']([a-z0-9_.]+)["']""")
    prop = re.compile(r"""_action\(\s*["']([a-z0-9_.]+)["']|a\(\s*["']([a-z0-9_.]+)["']""")
    tr_key_arg = re.compile(r"""\(\s*["']((?:ffmpeg|settings|action|menu)\.[a-z0-9_.]+)["']\s*,\s*f?["']""")
    for path in (ROOT / "reefhue").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        used |= set(literal.findall(text))
        for groups in prop.findall(text):
            used |= {g for g in groups if g}
        used |= set(tr_key_arg.findall(text))
    en = _tables()["en"]
    missing = sorted(k for k in used if k not in en)
    assert missing == []


@pytest.mark.parametrize(
    "preferred, expected",
    [
        (["de-DE", "en-US"], "de"),
        (["de_AT"], "de"),
        (["nl-BE"], "nl"),
        (["fr-FR", "nl-NL"], "nl"),
        (["fr-FR"], "en"),
        ([], "en"),
        (["EN-gb"], "en"),
    ],
)
def test_resolve_language(preferred, expected):
    assert i18n.resolve_language(preferred, ["de", "en", "nl"]) == expected


def test_tr_switches_language_at_runtime():
    i18n.set_language("en")
    assert i18n.tr("menu.view") == "&View"
    i18n.set_language("de")
    assert i18n.tr("menu.view") == "&Ansicht"
    i18n.set_language("nl")
    assert i18n.tr("menu.view") == "&Weergave"


def test_tr_fallbacks_and_formatting():
    assert i18n.set_language("xx") == "en"
    assert i18n.tr("does.not.exist") == "does.not.exist"
    assert i18n.tr("about.version", version="1.2.3") == "Version 1.2.3"
    # wrong format args do not crash
    assert "Version" in i18n.tr("about.version", other=1)


def test_missing_key_falls_back_to_english(tmp_path):
    (tmp_path / "en.json").write_text('{"_meta": {"native_name": "English"}, "a.b": "Hello"}', encoding="utf-8")
    (tmp_path / "de.json").write_text('{"_meta": {"native_name": "Deutsch"}}', encoding="utf-8")
    try:
        i18n.reload(tmp_path)
        i18n.set_language("de")
        assert i18n.tr("a.b") == "Hello"
    finally:
        i18n.reload()
        i18n.set_language("en")


def test_adding_a_language_is_adding_a_file(tmp_path):
    for path in I18N_DIR.glob("*.json"):
        (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "fr.json").write_text(
        '{"_meta": {"native_name": "Français"}, "menu.view": "&Affichage"}', encoding="utf-8"
    )
    try:
        i18n.reload(tmp_path)
        assert i18n.available_languages()["fr"] == "Français"
        i18n.set_language("fr")
        assert i18n.tr("menu.view") == "&Affichage"
        assert i18n.tr("menu.help") == "&Help"  # English fallback
    finally:
        i18n.reload()
        i18n.set_language("en")
