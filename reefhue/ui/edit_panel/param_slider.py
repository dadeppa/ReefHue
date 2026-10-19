"""Slider + number field for one registry parameter (SPEC §7 "Slider behavior")."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QSlider, QWidget

from reefhue.i18n import tr
from reefhue.pipeline import params as P
from reefhue.pipeline.params import Param

STEP_KEYS = {
    Qt.Key_Plus: 1, Qt.Key_Equal: 1, Qt.Key_Up: 1, Qt.Key_Right: 1,
    Qt.Key_Minus: -1, Qt.Key_Underscore: -1, Qt.Key_Down: -1, Qt.Key_Left: -1,
}


def modifier_name(modifiers) -> str:
    if modifiers & Qt.ShiftModifier:
        return "shift"
    if modifiers & Qt.AltModifier:  # Option on macOS
        return "alt"
    return "normal"


class _Label(QLabel):
    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.double_clicked.emit()


class _Slider(QSlider):
    double_clicked = Signal()
    step = Signal(int, str)
    step_done = Signal()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.double_clicked.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():  # never change values while scrolling the panel
            super().wheelEvent(event)
        else:
            event.ignore()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        direction = STEP_KEYS.get(event.key())
        if direction is None:
            return super().keyPressEvent(event)
        self.step.emit(direction, modifier_name(event.modifiers()))
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in STEP_KEYS and not event.isAutoRepeat():
            self.step_done.emit()
            event.accept()
            return
        super().keyReleaseEvent(event)


class ParamSlider(QWidget):
    """Emits ``changed(path, value)`` for every live change and ``committed(path)`` when a step ends."""

    changed = Signal(tuple, float)
    committed = Signal(tuple)

    def __init__(self, param: Param, path: tuple, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.param = param
        self.path = path
        self.value = float(param.default)
        self._res = P.resolution(param)
        self._ticks = int(round((param.maximum - param.minimum) / self._res))
        self._updating = False

        self.label = _Label()
        self.label.setMinimumWidth(92)
        self.label.double_clicked.connect(self.reset)
        self.slider = _Slider(Qt.Horizontal)
        self.slider.setRange(0, self._ticks)
        self.slider.setSingleStep(max(1, int(round(param.step / self._res))))
        self.slider.setPageStep(max(1, int(round(param.step * 10 / self._res))))
        self.slider.setFocusPolicy(Qt.StrongFocus)
        self.slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.slider.sliderReleased.connect(lambda: self.committed.emit(self.path))
        self.slider.valueChanged.connect(self._on_slider_value)
        self.slider.double_clicked.connect(self.reset)
        self.slider.step.connect(self.step)
        self.slider.step_done.connect(lambda: self.committed.emit(self.path))
        self.field = QLineEdit()
        self.field.setFixedWidth(64)
        self.field.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.field.editingFinished.connect(self._on_field)
        self.field.installEventFilter(self)
        self.unit = QLabel(param.unit)
        self.unit.setObjectName("MutedLabel")
        self.unit.setFixedWidth(22)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.label)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.field)
        layout.addWidget(self.unit)
        self.retranslate_ui()
        self.set_value(param.default)

    def retranslate_ui(self) -> None:
        self.label.setText(tr(self.param.label_key))
        tip = f"{tr(self.param.tip_key)}\n{tr('editor.slider_hint')}"
        for widget in (self.label, self.slider, self.field):
            widget.setToolTip(tip)

    # ---- value <-> widgets ----------------------------------------------------------
    def _tick_for(self, value: float) -> int:
        return int(round((value - self.param.minimum) / self._res))

    def _value_for(self, tick: int) -> float:
        return P.quantize(self.param, self.param.minimum + tick * self._res)

    def format(self, value: float) -> str:
        digits = min(P.decimals(self._res), max(P.decimals(self.param.step), P.decimals(value)))
        text = f"{value:.{digits}f}"
        return text if digits else str(int(round(value)))

    def set_value(self, value: float) -> None:
        """Update from the model (no signals)."""
        self.value = P.quantize(self.param, value)
        self._updating = True
        self.slider.setValue(self._tick_for(self.value))
        self.field.setText(self.format(self.value))
        self._updating = False

    def _emit(self, value: float) -> None:
        value = P.quantize(self.param, value)
        if value == self.value:
            self.set_value(value)
            return
        self.set_value(value)
        self.changed.emit(self.path, value)

    # ---- user input --------------------------------------------------------------------
    def _on_slider_moved(self, tick: int) -> None:
        self._emit(P.snap_zero(self.param, self._value_for(tick)))

    def _on_slider_value(self, tick: int) -> None:
        if self._updating or self.slider.isSliderDown():
            return
        self._emit(self._value_for(tick))  # click on the groove / page step
        self.committed.emit(self.path)

    def _on_field(self) -> None:
        text = self.field.text().strip().replace(",", ".").replace("−", "-")
        try:
            value = float(text)
        except ValueError:
            self.set_value(self.value)
            return
        self._emit(value)  # typed values are clamped by quantize()
        self.committed.emit(self.path)

    def step(self, direction: int, modifier: str = "normal") -> None:
        self._emit(P.step_value(self.param, self.value, direction, modifier))

    def reset(self) -> None:
        self._emit(float(self.param.default))
        self.committed.emit(self.path)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.field and event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Up, Qt.Key_Down):
            self.step(1 if event.key() == Qt.Key_Up else -1, modifier_name(event.modifiers()))
            return True
        if obj is self.field and event.type() == QEvent.KeyRelease and event.key() in (Qt.Key_Up, Qt.Key_Down):
            if not event.isAutoRepeat():
                self.committed.emit(self.path)
            return True
        return super().eventFilter(obj, event)
