"""Icon-only, exclusive structure selection for the drawing toolbar."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QSizePolicy, QToolButton, QWidget

from .icons import ICON_SIZE, icon


class StructureSelector(QWidget):
    valueChanged = Signal(str)
    VALUES = ("ground", "bridge", "tunnel")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("structureSelector")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget#structureSelector {
                background-color: palette(alternate-base);
                border: 1px solid palette(mid);
                border-radius: 6px;
            }
            QWidget#structureSelector QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 4px;
                padding: 0;
            }
            QWidget#structureSelector QToolButton:hover {
                background-color: palette(button);
            }
            QWidget#structureSelector QToolButton:checked,
            QWidget#structureSelector QToolButton:checked:hover {
                background-color: palette(highlight);
            }
            QWidget#structureSelector QToolButton:focus {
                border: 1px solid palette(highlight);
            }
            QWidget#structureSelector QToolButton:disabled:checked {
                background-color: palette(midlight);
            }
        """)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        self._value = "ground"
        for index, (value, asset) in enumerate(zip(self.VALUES, (
            "ground", "bridge-4", "train-front-tunnel",
        ))):
            button = QToolButton(self)
            button.setObjectName("structure_" + value)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setAutoRaise(True)
            button.setCheckable(True)
            button.setIcon(icon(asset))
            button.setIconSize(ICON_SIZE)
            button.setFixedSize(36, 28)
            button.installEventFilter(self)
            self.group.addButton(button, index)
            self.buttons[value] = button
            layout.addWidget(button)
        self.buttons[self._value].setChecked(True)
        self._update_focus_policy()
        self.group.idToggled.connect(self._toggled)

    def value(self):
        return self._value

    def set_value(self, value):
        if value not in self.buttons:
            raise ValueError(f"Unknown structure: {value}")
        if value != self._value:
            self.buttons[value].setChecked(True)

    def step(self, direction, focus=False):
        if not self.isEnabled():
            return
        index = (self.VALUES.index(self._value) + direction) % len(self.VALUES)
        self.set_value(self.VALUES[index])
        if focus:
            self.buttons[self._value].setFocus(Qt.FocusReason.OtherFocusReason)

    def set_labels(self, labels):
        for value, button in self.buttons.items():
            button.setToolTip(labels[value])
            button.setAccessibleName(labels[value])
        self.setAccessibleName(" / ".join(labels[value] for value in self.VALUES))

    def _update_focus_policy(self):
        for value, button in self.buttons.items():
            button.setFocusPolicy(
                Qt.FocusPolicy.StrongFocus if value == self._value else Qt.FocusPolicy.ClickFocus
            )

    def _toggled(self, index, checked):
        if checked:
            value = self.VALUES[index]
            if value != self._value:
                self._value = value
                self._update_focus_policy()
                self.valueChanged.emit(value)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                self.step(-1 if event.key() == Qt.Key.Key_Left else 1, focus=True)
                return True
        return super().eventFilter(watched, event)

