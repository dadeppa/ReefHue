"""Light / dark / follow-OS theming: Fusion style + QPalette + templated QSS."""

from __future__ import annotations

import logging
import re

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from reefhue.core import paths

log = logging.getLogger(__name__)

PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#f3f5f8",
        "surface": "#ffffff",
        "surface_alt": "#eef1f5",
        "input_bg": "#ffffff",
        "canvas": "#e6eaef",
        "border": "#d8dde4",
        "border_strong": "#b9c1cc",
        "text": "#1b2129",
        "text_muted": "#5d6978",
        "text_disabled": "#a3abb6",
        "accent": "#0b7fbf",
        "accent_hover": "#096ca3",
        "accent_soft": "#d9edf8",
        "accent_text": "#ffffff",
        "danger": "#d03b3b",
        "status_ok": "#1f8f50",
        "status_warn": "#b86e00",
        "banner_bg": "#fff5d9",
        "banner_border": "#efd48b",
        "banner_text": "#5f4500",
    },
    "dark": {
        "bg": "#15181c",
        "surface": "#1d2126",
        "surface_alt": "#262b31",
        "input_bg": "#191c20",
        "canvas": "#0e1013",
        "border": "#323840",
        "border_strong": "#4a525d",
        "text": "#e5e9ee",
        "text_muted": "#97a2af",
        "text_disabled": "#5b636d",
        "accent": "#2ea6e3",
        "accent_hover": "#4bb6ec",
        "accent_soft": "#173246",
        "accent_text": "#06131c",
        "danger": "#ef6b6b",
        "status_ok": "#4cc98a",
        "status_warn": "#f0a541",
        "banner_bg": "#3a3112",
        "banner_border": "#6b5a1e",
        "banner_text": "#f3d886",
    },
}

THEME_CHOICES = ("system", "light", "dark")
_TOKEN = re.compile(r"@([a-z_]+)")


def resolve_theme(choice: str, os_is_dark: bool) -> str:
    """Map a user choice (system/light/dark) to a concrete palette name."""
    if choice == "light":
        return "light"
    if choice == "dark":
        return "dark"
    return "dark" if os_is_dark else "light"


def render_qss(template: str, palette: dict[str, str]) -> str:
    """Replace ``@token`` placeholders. Unknown tokens raise ``KeyError``."""
    return _TOKEN.sub(lambda m: palette[m.group(1)], template)


def os_prefers_dark() -> bool:
    hints = QGuiApplication.styleHints()
    try:
        return hints.colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:  # Qt < 6.5
        return QGuiApplication.palette().color(QPalette.Window).lightness() < 128


def _build_palette(p: dict[str, str]) -> QPalette:
    pal = QPalette()
    c = QColor
    pal.setColor(QPalette.Window, c(p["bg"]))
    pal.setColor(QPalette.WindowText, c(p["text"]))
    pal.setColor(QPalette.Base, c(p["input_bg"]))
    pal.setColor(QPalette.AlternateBase, c(p["surface_alt"]))
    pal.setColor(QPalette.ToolTipBase, c(p["surface"]))
    pal.setColor(QPalette.ToolTipText, c(p["text"]))
    pal.setColor(QPalette.PlaceholderText, c(p["text_muted"]))
    pal.setColor(QPalette.Text, c(p["text"]))
    pal.setColor(QPalette.Button, c(p["surface_alt"]))
    pal.setColor(QPalette.ButtonText, c(p["text"]))
    pal.setColor(QPalette.BrightText, c(p["danger"]))
    pal.setColor(QPalette.Highlight, c(p["accent"]))
    pal.setColor(QPalette.HighlightedText, c(p["accent_text"]))
    pal.setColor(QPalette.Link, c(p["accent"]))
    pal.setColor(QPalette.LinkVisited, c(p["accent"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, c(p["text_disabled"]))
    return pal


def _check_image(color: str) -> str:
    """QSS needs a file for the checkbox tick: write a recoloured copy to the app data folder."""
    target = paths.data_dir() / "theme" / f"check-{color.lstrip('#')}.svg"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        svg = (paths.resources_dir() / "icons" / "check.svg").read_text(encoding="utf-8")
        target.write_text(svg.replace("currentColor", color), encoding="utf-8")
    return target.as_posix()


class ThemeManager(QObject):
    """Applies the theme to the QApplication and follows OS changes in 'system' mode."""

    theme_applied = Signal(str)  # concrete palette name: "light" | "dark"

    def __init__(self, app: QApplication, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self._choice = "system"
        self._active = "light"
        self._template = (paths.resources_dir() / "themes" / "base.qss").read_text(encoding="utf-8")
        app.setStyle("Fusion")
        try:
            app.styleHints().colorSchemeChanged.connect(self._on_os_scheme_changed)
        except AttributeError:  # Qt < 6.5: no live OS following
            log.info("colorSchemeChanged not available; system theme is read at startup only")

    @property
    def choice(self) -> str:
        return self._choice

    @property
    def active(self) -> str:
        return self._active

    def color(self, token: str) -> str:
        return PALETTES[self._active][token]

    def apply(self, choice: str) -> str:
        self._choice = choice if choice in THEME_CHOICES else "system"
        name = resolve_theme(self._choice, os_prefers_dark())
        palette = dict(PALETTES[name])
        palette["check_image"] = _check_image(palette["accent_text"])
        self._active = name
        self._app.setPalette(_build_palette(palette))
        self._app.setStyleSheet(render_qss(self._template, palette))
        log.info("theme applied: %s (choice %s)", name, self._choice)
        self.theme_applied.emit(name)
        return name

    def _on_os_scheme_changed(self, *_args) -> None:
        if self._choice == "system":
            self.apply("system")
