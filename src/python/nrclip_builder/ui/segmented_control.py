"""Shared segmented selection style and interaction for native controls."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QSizePolicy, QToolButton, QWidget

from .icons import ICON_SIZE, icon


class SegmentedControl(QWidget):
    valueChanged = Signal(str)

    def __init__(self, values, parent=None, *, icons=None):
        super().__init__(parent)
        self.values = tuple(values)
        self.icon_only = icons is not None
        self.setProperty("segmentedControl", True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget[segmentedControl="true"] {
                background-color: palette(alternate-base);
                border: 1px solid palette(mid);
                border-radius: 6px;
            }
            QWidget[segmentedControl="true"] QToolButton {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 0;
            }
            QWidget[segmentedControl="true"] QToolButton:hover {
                background-color: palette(button);
            }
            QWidget[segmentedControl="true"] QToolButton:checked,
            QWidget[segmentedControl="true"] QToolButton:checked:hover {
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
            QWidget[segmentedControl="true"] QToolButton:focus {
                border-color: palette(highlight);
            }
            QWidget[segmentedControl="true"] QToolButton:disabled:checked {
                background-color: palette(midlight);
            }
        """)
        self.setSizePolicy(QSizePolicy.Policy.Fixed if self.icon_only else QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        self._value = self.values[0]
        for index, value in enumerate(self.values):
            button = QToolButton(self)
            button.setAutoRaise(True)
            button.setCheckable(True)
            button.setFixedHeight(28)
            if self.icon_only:
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                button.setIcon(icon(icons[value]))
                button.setIconSize(ICON_SIZE)
                button.setFixedWidth(36)
            else:
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
                button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.installEventFilter(self)
            self.group.addButton(button, index)
            self.buttons[value] = button
            layout.addWidget(button, 1)
        self.buttons[self._value].setChecked(True)
        self._update_focus_policy()
        self.group.idToggled.connect(self._toggled)

    def value(self):
        return self._value

    def set_value(self, value):
        if value not in self.buttons:
            raise ValueError(f"Unknown selection: {value}")
        if value != self._value:
            self.buttons[value].setChecked(True)

    def step(self, direction, focus=False):
        if not self.isEnabled():
            return
        index = (self.values.index(self._value) + direction) % len(self.values)
        self.set_value(self.values[index])
        if focus:
            self.buttons[self._value].setFocus(Qt.FocusReason.OtherFocusReason)

    def set_labels(self, labels):
        width = max(self.fontMetrics().horizontalAdvance(labels[value]) for value in self.values) + 24
        for value, button in self.buttons.items():
            button.setToolTip(labels[value])
            button.setAccessibleName(labels[value])
            if not self.icon_only:
                button.setText(labels[value])
                button.setMinimumWidth(width)
        self.setAccessibleName(" / ".join(labels[value] for value in self.values))

    def _update_focus_policy(self):
        for value, button in self.buttons.items():
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus if value == self._value else Qt.FocusPolicy.ClickFocus)

    def _toggled(self, index, checked):
        if checked:
            value = self.values[index]
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
