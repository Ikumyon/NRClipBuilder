import json
from collections import OrderedDict

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt


class FeatureTable(QAbstractTableModel):
    """Fetch only the visible page from the immutable native selection."""
    PAGE_SIZE = 128

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selection = None
        self.fields = []
        self.pages = OrderedDict()

    def set_selection(self, selection):
        self.beginResetModel()
        self.selection = selection
        self.fields = selection.fields() if selection else []
        self.pages.clear()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.selection is None else self.selection.count

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.fields)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole or self.selection is None:
            return None
        page = index.row() // self.PAGE_SIZE
        if page not in self.pages:
            self.pages[page] = json.loads(self.selection.page(page * self.PAGE_SIZE, self.PAGE_SIZE))
            if len(self.pages) > 8:
                self.pages.popitem(last=False)
        self.pages.move_to_end(page)
        value = self.pages[page][index.row() % self.PAGE_SIZE]["properties"].get(self.fields[index.column()], "")
        return str(value) if value is not None else ""

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.fields[section] if orientation == Qt.Orientation.Horizontal else str(section + 1)
        return None
