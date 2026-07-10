from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLineEdit, QMessageBox, QDialogButtonBox, QCheckBox
from PySide6.QtCore import Qt

class AddMapDialog(QDialog):
    def __init__(self, parent=None, translation=None):
        super().__init__(parent)
        self.translation = translation or {}
        self.setWindowTitle(self.tr_msg("dialog_add_map_title", "背景地図を追加"))
        self.resize(400, 220)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText(self.tr_msg("dialog_map_name_placeholder", "例: 国土地理院地図"))
        self.url_edit = QLineEdit(self)
        self.url_edit.setPlaceholderText(self.tr_msg("dialog_map_url_placeholder", "例: https://example.com/{z}/{x}/{y}.png"))
        self.attr_edit = QLineEdit(self)
        self.attr_edit.setPlaceholderText(self.tr_msg("dialog_map_attr_placeholder", "例: &copy; Map providers"))
        self.has_lines_check = QCheckBox(self)

        form.addRow(self.tr_msg("dialog_label_map_name", "背景地図名"), self.name_edit)
        form.addRow(self.tr_msg("dialog_label_map_url", "タイルURL"), self.url_edit)
        form.addRow(self.tr_msg("dialog_label_map_attr", "著作権表記"), self.attr_edit)
        form.addRow(self.tr_msg("dialog_label_has_lines", "線（路線等）も含む"), self.has_lines_check)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def tr_msg(self, key: str, fallback: str) -> str:
        val = self.translation.get(key, fallback)
        return val if val else fallback

    def accept(self):
        name = self.name_edit.text().strip()
        url = self.url_edit.text().strip()
        if not name or not url:
            QMessageBox.warning(self, self.tr_msg("msg_validation_error", "警告"), self.tr_msg("msg_validation_empty", "名前とURLを入力してください。"))
            return
        if not all(p in url for p in ["{z}", "{x}", "{y}"]):
            QMessageBox.warning(self, self.tr_msg("msg_validation_error", "警告"), self.tr_msg("msg_validation_invalid_url", "タイルURLには {z}, {x}, {y} のプレースホルダーを含める必要があります。"))
            return
        super().accept()

    def get_data(self) -> tuple[str, str, str, bool]:
        return (
            self.name_edit.text().strip(),
            self.url_edit.text().strip(),
            self.attr_edit.text().strip(),
            self.has_lines_check.isChecked()
        )
