"""Shared package SVG icons for native Qt controls."""
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .localisation import resource


ICON_SIZE = QSize(20, 20)


class SvgIconEngine(QIconEngine):
    """Render bundled multi-color Fluent SVG icons directly."""

    def __init__(self, svg: bytes):
        super().__init__()
        self.svg = svg
        self.renderer = QSvgRenderer(QByteArray(svg))

    def clone(self):
        return SvgIconEngine(self.svg)

    def paint(self, painter, rect, mode, state):
        painter.save()
        if mode == QIcon.Mode.Disabled:
            painter.setOpacity(0.45)
        self.renderer.render(painter, QRectF(rect))
        painter.restore()

    def pixmap(self, size, mode, state):
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, pixmap.rect(), mode, state)
        painter.end()
        return pixmap


@lru_cache
def application_icon() -> QIcon:
    """Use the original multi-resolution ICO for native windows and taskbars."""
    return QIcon(str(resource("assets/icon.ico")))


@lru_cache
def icon(name: str) -> QIcon:
    return QIcon(SvgIconEngine(resource(f"assets/icons/{name}.svg").read_bytes()))
