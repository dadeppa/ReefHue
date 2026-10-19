"""Tabbed edit panel below the preview (SPEC §7).

Tabs: Underwater | Light | Color | Curves | Details | Geometry | LUT & Presets.
The histogram sits permanently on the left. Every control is bound to a path
in the ``edit`` dict; all changes go through :class:`EditorController`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pathlib import Path

from reefhue.core import presets
from reefhue.i18n import has_key, tr
from reefhue.pipeline import curves as curve_math
from reefhue.pipeline import geometry as geo
from reefhue.pipeline import lut as lut_library
from reefhue.pipeline.params import PARAMS_BY_KEY, LEVEL_CHANNELS
from reefhue.ui.context import AppContext
from reefhue.ui.edit_panel.curves_widget import CurvesWidget
from reefhue.ui.edit_panel.histogram import HistogramWidget
from reefhue.ui.edit_panel.param_slider import ParamSlider
from reefhue.ui.editor_controller import EditorController
from reefhue.ui.sections_dialog import SectionsDialog
from reefhue.ui.util import open_in_file_manager

# Tab order is fixed by the spec: Underwater | Light | Color | Curves | Details | Geometry | LUT & Presets
TAB_KEYS = ("underwater", "light", "color", "curves", "details", "geometry", "lut_presets")
TAB_SECTIONS = {  # what each tab's reset button resets
    "underwater": ["underwater"],
    "light": ["light"],
    "color": ["white_balance", "color"],
    "curves": ["curves", "levels"],
    "details": ["details"],
    "geometry": ["geometry"],
    "lut_presets": ["lut"],
}
EYEDROPPER_DEFAULT = 11


def _columns(widgets: list[QWidget], columns: int = 2) -> QGridLayout:
    grid = QGridLayout()
    grid.setHorizontalSpacing(28)
    grid.setVerticalSpacing(6)
    rows = (len(widgets) + columns - 1) // columns
    for index, widget in enumerate(widgets):
        grid.addWidget(widget, index % rows, index // rows)
    for col in range(columns):
        grid.setColumnStretch(col, 1)
    return grid


class EditPanel(QWidget):
    crop_mode_changed = Signal(bool)
    eyedropper_toggled = Signal(bool)
    actual_size_requested = Signal()

    def __init__(self, ctx: AppContext, editor: EditorController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.editor = editor
        self.setObjectName("EditPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumHeight(170)
        self.sliders: dict[tuple, ParamSlider] = {}
        self.combos: dict[tuple, QComboBox] = {}
        self.checks: dict[tuple, QCheckBox] = {}
        self._translatable: list = []  # (widget, setter, key)
        self._updating = False

        # ---- histogram (left)
        self.histogram_frame = QFrame()
        self.histogram_frame.setFixedWidth(240)
        self.histogram_title = QLabel()
        self.histogram_title.setObjectName("PaneHeader")
        self.histogram = HistogramWidget()
        self.clipping_box = QCheckBox()
        hist_layout = QVBoxLayout(self.histogram_frame)
        hist_layout.setContentsMargins(12, 10, 12, 10)
        hist_layout.addWidget(self.histogram_title)
        hist_layout.addWidget(self.histogram, 1)
        hist_layout.addWidget(self.clipping_box)

        # ---- tabs
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.reset_buttons: dict[str, QPushButton] = {}
        builders = {
            "underwater": self._build_underwater, "light": self._build_light, "color": self._build_color,
            "curves": self._build_curves, "details": self._build_details, "geometry": self._build_geometry,
            "lut_presets": self._build_lut_presets,
        }
        for key in TAB_KEYS:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(14, 10, 14, 10)
            header = QHBoxLayout()
            header.setSpacing(8)
            body = QVBoxLayout()
            builders[key](header, body)
            header.addStretch(1)
            reset = QPushButton()
            reset.clicked.connect(lambda _=False, k=key: editor.reset_sections(TAB_SECTIONS[k]))
            self.reset_buttons[key] = reset
            header.addWidget(reset)
            page_layout.addLayout(header)
            page_layout.addLayout(body)
            page_layout.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, "")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.disabled_note = QLabel(alignment=Qt.AlignCenter)
        self.disabled_note.setObjectName("MutedLabel")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.histogram_frame)
        right = QVBoxLayout()
        right.setSpacing(0)
        right.addWidget(self.tabs, 1)
        right.addWidget(self.disabled_note)
        layout.addLayout(right, 1)

        editor.item_changed.connect(self._on_item_changed)
        editor.edit_changed.connect(self._on_edit_changed)
        ctx.language_changed.connect(self.retranslate_ui)
        ctx.theme_changed.connect(self._apply_theme_colors)
        self._apply_theme_colors()
        self.retranslate_ui()
        self._on_item_changed(editor.item)

    # ------------------------------------------------------------------ builders
    def _slider(self, group: str, key: str, path: tuple | None = None) -> ParamSlider:
        path = path or (group, key)
        slider = ParamSlider(PARAMS_BY_KEY[(group, key)], path)
        slider.changed.connect(lambda p, v: self.editor.set_param(p, v, live=True))
        slider.committed.connect(lambda _p: self.editor.commit())
        self.sliders[path] = slider
        return slider

    def _check(self, path: tuple) -> QCheckBox:
        box = QCheckBox()
        box.toggled.connect(lambda on, p=path: None if self._updating else self.editor.set_param(p, on, live=False))
        box.toggled.connect(lambda _on: None if self._updating else self.editor.commit())
        self.checks[path] = box
        self._tr(box, box.setText, f"param.{path[0]}.{path[1]}")
        self._tr(box, box.setToolTip, f"param.{path[0]}.{path[1]}.tip")
        return box

    def _choice(self, path: tuple) -> QWidget:
        param = PARAMS_BY_KEY[path]
        combo = QComboBox()
        for choice in param.choices:
            combo.addItem("", choice)
        combo.currentIndexChanged.connect(
            lambda _i, p=path, c=combo: None if self._updating else (
                self.editor.set_param(p, c.currentData(), live=False), self.editor.commit()))
        self.combos[path] = combo
        label = QLabel()
        self._tr(label, label.setText, param.label_key)
        for widget in (label, combo):
            self._tr(widget, widget.setToolTip, param.tip_key)
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        label.setMinimumWidth(92)
        lay.addWidget(label)
        lay.addWidget(combo, 1)
        return row

    def _button(self, key: str, slot, checkable: bool = False) -> QPushButton:
        button = QPushButton()
        button.setCheckable(checkable)
        button.clicked.connect(slot)
        self._tr(button, button.setText, key)
        if has_key(f"{key}_tip"):
            self._tr(button, button.setToolTip, f"{key}_tip")
        return button

    def _tr(self, widget, setter, key: str) -> None:
        self._translatable.append((widget, setter, key))

    def _build_underwater(self, header: QHBoxLayout, body: QVBoxLayout) -> None:
        self.magic_button = self._button("editor.underwater.auto", self._run_magic)
        self.magic_button.setObjectName("PrimaryButton")
        self._tr(self.magic_button, self.magic_button.setToolTip, "editor.magic_tip")
        self.magic_selection_button = self._button("editor.magic_selection", self._run_magic_selection)
        self.magic_frame_box = QCheckBox()
        self._tr(self.magic_frame_box, self.magic_frame_box.setText, "editor.magic_current_frame")
        self._tr(self.magic_frame_box, self.magic_frame_box.setToolTip, "editor.magic_current_frame_tip")
        self.magic_status = QLabel()
        self.magic_status.setObjectName("MutedLabel")
        for widget in (self.magic_button, self.magic_selection_button, self.magic_frame_box, self.magic_status):
            header.addWidget(widget)
        light = self._check(("underwater", "artificial_light"))
        body.addLayout(_columns([
            self._slider("underwater", "auto_strength"), self._slider("underwater", "depth_m"),
            self._slider("underwater", "red_comp"), self._choice(("underwater", "water_type")), light,
        ]))
        self.editor.magic_progress.connect(self._on_magic_progress)
        self.editor.busy_changed.connect(self._on_busy)

    def _build_light(self, header: QHBoxLayout, body: QVBoxLayout) -> None:
        header.addWidget(self._button("editor.auto", lambda: self.editor.auto("light")))
        body.addLayout(_columns([self._slider("light", k) for k in
                                 ("exposure_ev", "contrast", "highlights", "shadows", "whites", "blacks")]))

    def _build_color(self, header: QHBoxLayout, body: QVBoxLayout) -> None:
        header.addWidget(self._button("editor.auto_wb", lambda: self.editor.auto("white_balance")))
        self.eyedropper_button = self._button("editor.eyedropper", self._on_eyedropper, checkable=True)
        self._tr(self.eyedropper_button, self.eyedropper_button.setToolTip, "editor.eyedropper_tip")
        header.addWidget(self.eyedropper_button)
        self.eyedropper_size = QSpinBox()
        self.eyedropper_size.setRange(1, 31)
        self.eyedropper_size.setSingleStep(2)
        size = self.ctx.settings.qsettings.value("editor/eyedropper_size", EYEDROPPER_DEFAULT)
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = EYEDROPPER_DEFAULT
        self.eyedropper_size.setValue(max(1, min(31, size | 1)))
        self.eyedropper_size.valueChanged.connect(self._on_eyedropper_size)
        self._tr(self.eyedropper_size, self.eyedropper_size.setToolTip, "editor.eyedropper_size")
        header.addWidget(self.eyedropper_size)
        body.addLayout(_columns([
            self._slider("white_balance", "temperature"), self._slider("white_balance", "tint"),
            self._slider("color", "vibrance"), self._slider("color", "saturation"),
        ]))

    def _build_curves(self, header: QHBoxLayout, body: QVBoxLayout) -> None:
        # curves (collapsible via View ▸ Curves)
        self.curves_box = QGroupBox()
        self._tr(self.curves_box, self.curves_box.setTitle, "editor.curves")
        self.curve_channel = QComboBox()
        for ch in ("master", "r", "g", "b"):
            self.curve_channel.addItem("", ch)
        self.curve_channel.currentIndexChanged.connect(lambda _i: self._update_curve_view())
        self._tr(self.curve_channel, self.curve_channel.setToolTip, "editor.curve_channel_tip")
        self.curve_enabled = QCheckBox()
        self._tr(self.curve_enabled, self.curve_enabled.setText, "editor.curve_enabled")
        self._tr(self.curve_enabled, self.curve_enabled.setToolTip, "editor.curve_enabled_tip")
        self.curve_enabled.toggled.connect(self._on_curve_enabled)
        self.curve_preset = QComboBox()
        self.curve_preset.addItem("", None)
        for name in curve_math.PRESETS:
            self.curve_preset.addItem("", name)
        self.curve_preset.activated.connect(self._on_curve_preset)
        self._tr(self.curve_preset, self.curve_preset.setToolTip, "editor.curve_preset_tip")
        self.curves = CurvesWidget()
        self.curves.setMinimumSize(160, 160)
        self.curves.setMaximumHeight(220)  # fits the panel without scrolling
        self.curves.points_changed.connect(
            lambda pts: self.editor.set_curve(self.curve_channel.currentData(), pts, live=True))
        self.curves.committed.connect(self.editor.commit)
        top = QHBoxLayout()
        top.addWidget(self.curve_channel)
        top.addWidget(self.curve_enabled)
        top.addStretch(1)
        top.addWidget(self.curve_preset)
        hint = QLabel()
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        self._tr(hint, hint.setText, "editor.curves_hint")
        curves_layout = QVBoxLayout(self.curves_box)
        curves_layout.addLayout(top)
        curves_layout.addWidget(self.curves, 1)
        curves_layout.addWidget(hint)
        # levels
        levels_box = QGroupBox()
        self._tr(levels_box, levels_box.setTitle, "editor.levels")
        self.level_channel = QComboBox()
        for ch in LEVEL_CHANNELS:
            self.level_channel.addItem("", ch)
        self.level_channel.currentIndexChanged.connect(lambda _i: self._update_levels_view())
        self._tr(self.level_channel, self.level_channel.setToolTip, "editor.level_channel_tip")
        self.level_stack: dict[str, list[ParamSlider]] = {}
        levels_layout = QVBoxLayout(levels_box)
        levels_layout.addWidget(self.level_channel)
        for ch in LEVEL_CHANNELS:
            group = [self._slider("levels", k, ("levels", ch, k)) for k in ("black", "white", "gamma")]
            self.level_stack[ch] = group
            for slider in group:
                levels_layout.addWidget(slider)
        levels_layout.addStretch(1)
        row = QHBoxLayout()
        row.addWidget(self.curves_box, 1)
        row.addWidget(levels_box, 1)
        body.addLayout(row)
        self._update_levels_view()

    def _build_details(self, header: QHBoxLayout, body: QVBoxLayout) -> None:
        self.actual_size_button = self._button("editor.view_100", self.actual_size_requested.emit)
        self._tr(self.actual_size_button, self.actual_size_button.setToolTip, "editor.view_100_tip")
        header.addWidget(self.actual_size_button)
        body.addLayout(_columns([self._slider("details", k) for k in
                                 ("dehaze", "clarity", "sharpen_amount", "sharpen_radius", "denoise")]))

    def _build_geometry(self, header: QHBoxLayout, body: QVBoxLayout) -> None:
        self.aspect = QComboBox()
        for name in geo.ASPECTS:
            self.aspect.addItem("", name)
        self.aspect.activated.connect(self._on_aspect)
        self._tr(self.aspect, self.aspect.setToolTip, "editor.aspect_tip")
        aspect_label = QLabel()
        self._tr(aspect_label, aspect_label.setText, "editor.crop_aspect")
        header.addWidget(aspect_label)
        header.addWidget(self.aspect)
        header.addWidget(self._button("editor.crop_reset", self.editor.reset_crop))
        header.addSpacing(12)
        rotate_left = self._button("editor.rotate_left", lambda: self.editor.rotate(-1))
        rotate_right = self._button("editor.rotate_right", lambda: self.editor.rotate(1))
        header.addWidget(rotate_left)
        header.addWidget(rotate_right)
        body.addLayout(_columns([
            self._slider("geometry", "straighten_deg"), self._check(("geometry", "flip_h")),
            self._check(("geometry", "flip_v")), self._slider("geometry", "distortion"),
            self._slider("geometry", "vignette_corr"),
        ]))
        self.crop_hint = QLabel()
        self.crop_hint.setObjectName("MutedLabel")
        self._tr(self.crop_hint, self.crop_hint.setText, "editor.crop_hint")
        body.addWidget(self.crop_hint)

    def _build_lut_presets(self, header: QHBoxLayout, body: QVBoxLayout) -> None:
        lut_box = QGroupBox()
        self._tr(lut_box, lut_box.setTitle, "editor.lut")
        self.lut_combo = QComboBox()
        self.lut_combo.activated.connect(self._on_lut_chosen)
        self._tr(self.lut_combo, self.lut_combo.setToolTip, "editor.lut_combo_tip")
        lut_import = self._button("editor.lut_import", self._import_lut)
        lut_folder = self._button("editor.lut_folder", lambda: open_in_file_manager(lut_library.app_lut_dir()))
        lut_row = QHBoxLayout()
        lut_row.addWidget(self.lut_combo, 1)
        lut_row.addWidget(lut_import)
        lut_row.addWidget(lut_folder)
        self.lut_status = QLabel(wordWrap=True)
        self.lut_status.setObjectName("Notice")
        self.lut_status.setVisible(False)
        self.lut_intensity = self._slider("lut", "intensity")
        self.lut_position = self._choice(("lut", "position"))
        lut_layout = QVBoxLayout(lut_box)
        lut_layout.addLayout(lut_row)
        lut_layout.addWidget(self.lut_status)
        lut_layout.addWidget(self.lut_intensity)
        lut_layout.addWidget(self.lut_position)
        lut_layout.addStretch(1)
        self._lut_check_cache: dict = {}
        self.reload_luts()

        preset_box = QGroupBox()
        self._tr(preset_box, preset_box.setTitle, "editor.presets")
        self.preset_list = QListWidget()
        self.preset_list.setMinimumHeight(90)
        self.preset_list.itemDoubleClicked.connect(lambda _i: self.apply_preset())
        self.preset_list.currentRowChanged.connect(lambda _r: self._update_preset_buttons())
        self.preset_apply = self._button("presets.apply", self.apply_preset)
        self.preset_save = self._button("presets.save", self.save_preset)
        self.preset_delete = self._button("presets.delete", self.delete_preset)
        buttons = QHBoxLayout()
        for b in (self.preset_apply, self.preset_save, self.preset_delete):
            buttons.addWidget(b)
        buttons.addStretch(1)
        preset_layout = QVBoxLayout(preset_box)
        preset_layout.addWidget(self.preset_list, 1)
        preset_layout.addLayout(buttons)
        row = QHBoxLayout()
        row.addWidget(lut_box, 1)
        row.addWidget(preset_box, 1)
        body.addLayout(row)
        self.reload_presets()

    # ------------------------------------------------------------------ i18n / theme
    def retranslate_ui(self, *_args) -> None:
        self.histogram_title.setText(tr("editpanel.histogram"))
        self.clipping_box.setText(tr("editor.clipping"))
        self.clipping_box.setToolTip(tr("editor.clipping_tip"))
        for index, key in enumerate(TAB_KEYS):
            self.tabs.setTabText(index, tr(f"editpanel.tab.{key}").replace("&", "&&"))
            self.reset_buttons[key].setText(tr("editor.reset_tab"))
            self.reset_buttons[key].setToolTip(tr("editor.reset_tab_tip"))
        for slider in self.sliders.values():
            slider.retranslate_ui()
        for path, combo in self.combos.items():
            for i in range(combo.count()):
                combo.setItemText(i, tr(f"param.{path[0]}.{path[1]}.{combo.itemData(i)}"))
        for widget, setter, key in self._translatable:
            setter(tr(key))
        for i in range(self.curve_channel.count()):
            self.curve_channel.setItemText(i, tr(f"editor.channel.{self.curve_channel.itemData(i)}"))
        for i in range(self.level_channel.count()):
            self.level_channel.setItemText(i, tr(f"editor.channel.{self.level_channel.itemData(i)}"))
        self.curve_preset.setItemText(0, tr("editor.curve_presets"))
        for i in range(1, self.curve_preset.count()):
            self.curve_preset.setItemText(i, tr(f"editor.curve_preset.{self.curve_preset.itemData(i)}"))
        for i in range(self.aspect.count()):
            self.aspect.setItemText(i, tr(f"editor.aspect.{self.aspect.itemData(i).replace(':', '_')}"))
        self.disabled_note.setText(tr("editpanel.no_file"))

    def _apply_theme_colors(self, *_args) -> None:
        self.histogram.set_colors(self.ctx.color("border"), self.ctx.color("text"), self.ctx.theme.active == "dark")
        self.curves.set_colors(self.ctx.color("text"), self.ctx.color("border_strong"))

    # ------------------------------------------------------------------ model → widgets
    def _on_item_changed(self, item) -> None:
        enabled = item is not None
        self.magic_frame_box.setVisible(item is not None and item.kind == "video")
        self.tabs.setEnabled(enabled)
        self.disabled_note.setVisible(not enabled)
        if not enabled:
            self.histogram.set_histogram(None)
            if self.eyedropper_button.isChecked():
                self.eyedropper_button.setChecked(False)
                self.eyedropper_toggled.emit(False)

    def _on_edit_changed(self, edit: dict, _live: bool) -> None:
        self._updating = True
        for path, slider in self.sliders.items():
            node = edit
            for key in path:
                node = node[key]
            slider.set_value(float(node))
        for path, box in self.checks.items():
            box.setChecked(bool(edit[path[0]][path[1]]))
        for path, combo in self.combos.items():
            combo.setCurrentIndex(max(0, combo.findData(edit[path[0]][path[1]])))
        crop = edit["geometry"]["crop"]
        self.aspect.setCurrentIndex(max(0, self.aspect.findData(crop.get("aspect", "free"))))
        self._update_lut_state(edit)
        if not self.editor_busy:
            self._update_magic_status(edit)
        self._updating = False
        self._update_curve_view()

    @property
    def editor_busy(self) -> bool:
        return not self.magic_button.isEnabled()

    def _update_curve_view(self) -> None:
        channel = self.curve_channel.currentData() or "master"
        spec = self.editor.edit["curves"][channel]
        self.curves.set_curve(spec["points"], channel)
        self.curve_enabled.blockSignals(True)
        self.curve_enabled.setChecked(bool(spec.get("enabled", True)))
        self.curve_enabled.blockSignals(False)

    def _update_levels_view(self) -> None:
        current = self.level_channel.currentData() or "master"
        for ch, group in self.level_stack.items():
            for slider in group:
                slider.setVisible(ch == current)

    # ------------------------------------------------------------------ widget → editor
    def _on_curve_enabled(self, on: bool) -> None:
        channel = self.curve_channel.currentData()
        self.editor.set_param(("curves", channel, "enabled"), bool(on), live=False)
        self.editor.commit()

    def _on_curve_preset(self, index: int) -> None:
        name = self.curve_preset.itemData(index)
        if name:
            self.editor.set_curve(self.curve_channel.currentData(), curve_math.PRESETS[name], live=False)
            self.editor.commit()
        self.curve_preset.setCurrentIndex(0)

    def _on_aspect(self, index: int) -> None:
        result = self.editor.engine.last_final
        if result is None:
            return
        self.editor.set_crop_aspect(self.aspect.itemData(index), self.frame_size())

    def frame_size(self) -> tuple[int, int]:
        """Full-resolution size of the uncropped frame (crop reference)."""
        item = self.editor.item
        result = self.editor.engine.last_final
        if result is not None and result.crop_mode:
            return result.full_size
        if item is None:
            return (1, 1)
        media = item.entry["media"]
        w, h = media.get("width") or 1, media.get("height") or 1
        if media.get("rotation_meta") in (90, 270):
            w, h = h, w
        return geo.uncropped_size(int(w), int(h), self.editor.edit["geometry"])

    def _on_eyedropper(self, checked: bool) -> None:
        self.eyedropper_toggled.emit(checked)

    def _on_eyedropper_size(self, value: int) -> None:
        if value % 2 == 0:  # keep the square centred: odd sizes only
            self.eyedropper_size.setValue(value + 1)
            return
        self.ctx.settings.qsettings.setValue("editor/eyedropper_size", value)

    def eyedropper_done(self) -> None:
        self.eyedropper_button.setChecked(False)
        self.eyedropper_toggled.emit(False)

    def _on_tab_changed(self, index: int) -> None:
        self.crop_mode_changed.emit(TAB_KEYS[index] == "geometry")

    @property
    def crop_mode(self) -> bool:
        return TAB_KEYS[self.tabs.currentIndex()] == "geometry" and self.editor.item is not None

    def set_histogram_visible(self, visible: bool) -> None:
        self.histogram_frame.setVisible(visible)

    def set_curves_visible(self, visible: bool) -> None:
        self.curves_box.setVisible(visible)

    # ------------------------------------------------------------------ presets
    def reload_presets(self) -> None:
        self.preset_list.clear()
        for preset in presets.load_builtin_presets() + presets.load_presets():
            item = QListWidgetItem(tr(preset.name_key) if preset.name_key else preset.name)
            item.setData(Qt.UserRole, preset)
            if preset.builtin:
                item.setToolTip(tr("presets.builtin"))
            self.preset_list.addItem(item)
        self._update_preset_buttons()

    def _update_preset_buttons(self) -> None:
        preset = self.selected_preset()
        self.preset_apply.setEnabled(preset is not None)
        self.preset_delete.setEnabled(preset is not None and not preset.builtin)

    # ------------------------------------------------------------------ LUTs
    def reload_luts(self, select: str | None = None) -> None:
        current = select if select is not None else self.editor.edit["lut"].get("file")
        self.lut_combo.blockSignals(True)
        self.lut_combo.clear()
        self.lut_combo.addItem(tr("editor.lut_none"), None)
        for name in lut_library.LIBRARY.names():
            self.lut_combo.addItem(name[:-5] if name.lower().endswith(".cube") else name, name)
        if current and self.lut_combo.findData(current) < 0:
            self.lut_combo.addItem(tr("editor.lut_missing_item", name=current), current)
        self.lut_combo.setCurrentIndex(max(0, self.lut_combo.findData(current)))
        self.lut_combo.blockSignals(False)

    def _on_lut_chosen(self, index: int) -> None:
        self.editor.set_lut(self.lut_combo.itemData(index))

    def _import_lut(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, tr("editor.lut_import"), str(Path.home()), "LUT (*.cube *.CUBE)")
        if not chosen:
            return
        try:
            target = lut_library.LIBRARY.import_file(Path(chosen))
        except (lut_library.LutError, OSError) as exc:
            QMessageBox.warning(self, tr("editor.lut"), tr("editor.lut_invalid", detail=str(exc)))
            return
        self.reload_luts(select=target.name)
        if self.editor.item is not None:
            self.editor.set_lut(target.name)

    def _update_lut_state(self, edit: dict) -> None:
        lut = edit.get("lut") or {}
        name = lut.get("file")
        if self.lut_combo.findData(name) < 0 or self.lut_combo.currentData() != name:
            self.reload_luts(select=name)
        has = bool(name)
        self.lut_intensity.setEnabled(has)
        self.lut_position.setEnabled(has)
        state = self._lut_state(name, lut.get("sha256"))
        self.lut_status.setVisible(state != "ok")
        if state != "ok":
            self.lut_status.setText(tr(f"editor.lut_{state}", name=name or ""))

    def _lut_state(self, name: str | None, sha: str | None) -> str:
        path = lut_library.LIBRARY.resolve(name)
        key = (name, sha, path.stat().st_mtime_ns if path else None)
        if key not in self._lut_check_cache:
            self._lut_check_cache[key] = lut_library.LIBRARY.check(name, sha)
        return self._lut_check_cache[key]

    # ------------------------------------------------------------------ magic button
    #: set by the viewer: playback position of the current video (None for photos)
    current_frame_time = staticmethod(lambda: None)

    def _run_magic(self) -> None:
        self.editor.magic(None, self.magic_frame_box.isChecked(), self.current_frame_time())

    def _run_magic_selection(self) -> None:
        self.editor.magic(self.target_items())

    def _on_magic_progress(self, done: int, total: int) -> None:
        if total > 1:
            self.magic_status.setText(tr("editor.magic_progress", done=done + 1, total=total))
        elif total == 1:
            self.magic_status.setText(tr("editor.magic_running"))
        else:
            self._update_magic_status(self.editor.edit)

    def _on_busy(self, busy: bool) -> None:
        for button in (self.magic_button, self.magic_selection_button):
            button.setEnabled(not busy)

    def _update_magic_status(self, edit: dict) -> None:
        auto = edit["underwater"].get("auto_result") or {}
        text = ""
        if auto.get("depth_estimated"):
            text = tr("editor.depth_estimated")
        elif auto.get("analyzed_at"):
            text = tr("editor.magic_done")
        self.magic_status.setText(text)

    def selected_preset(self):
        item = self.preset_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    #: set by the main window: returns the files a paste/preset applies to
    target_items = staticmethod(lambda: [])

    def apply_preset(self) -> None:
        preset = self.selected_preset()
        if preset is None:
            return
        dialog = SectionsDialog(tr("presets.apply_title", name=preset.name), tr("presets.apply_intro"),
                                available=preset.sections, ok_text=tr("presets.apply"), parent=self)
        if dialog.exec() == SectionsDialog.Accepted:
            self.editor.apply_partial(self.target_items(), preset.edit, dialog.selected())
        dialog.deleteLater()

    def save_preset(self) -> None:
        if self.editor.item is None:
            return
        dialog = SectionsDialog(tr("presets.save_title"), tr("presets.save_intro"), ask_name=True,
                                ok_text=tr("presets.save"), parent=self)
        if dialog.exec() == SectionsDialog.Accepted:
            presets.save_preset(dialog.preset_name, self.editor.edit, dialog.selected())
            self.reload_presets()
        dialog.deleteLater()

    def delete_preset(self) -> None:
        preset = self.selected_preset()
        if preset is None:
            return
        box = QMessageBox(QMessageBox.Question, tr("presets.delete_title"),
                          tr("presets.delete_confirm", name=preset.name), parent=self)
        delete = box.addButton(tr("presets.delete"), QMessageBox.DestructiveRole)
        box.addButton(tr("common.cancel"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is delete:
            presets.delete_preset(preset)
            self.reload_presets()

