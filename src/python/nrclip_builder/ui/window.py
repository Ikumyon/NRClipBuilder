from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QItemSelectionModel
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSlider, QSplitter, QTabWidget, QTableView, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from .. import __version__, _native
from ..services import ApplicationService
from ..state import HISTORY_ID, Layer
from .localisation import Localisation
from .icons import ICON_SIZE, icon
from .map_view import MapView
from .structure_selector import StructureSelector
from .table import FeatureTable
from .export_dialog import ExportDialog


class MainWindow(QMainWindow):
    def __init__(self, service=None):
        super().__init__()
        self.service = service or ApplicationService(self)
        self.locale = Localisation(self)
        self.bindings = []
        self.refreshing = False
        self.closing = False
        self.close_allowed = False
        self.history_signature = None
        available = self.screen().availableGeometry()
        self.resize(min(1280, int(available.width() * .9)), min(820, int(available.height() * .9)))
        self.setWindowTitle(f"NRClipBuilder {__version__}")
        self.map = MapView(self)
        self.map.event.connect(self.map_event)
        self.map.failed.connect(self.error)
        self.table_model = FeatureTable(self)
        self.history_model = QStandardItemModel(self)
        self.build_ui()
        self.locale.changed.connect(self.retranslate)
        self.service.changed.connect(self.refresh)
        self.service.rendered.connect(self.rendered)
        self.service.failed.connect(self.error)
        self.service.message.connect(lambda text: self.statusBar().showMessage(text, 10000))
        self.service.jobs.busy_changed.connect(self.busy_changed)
        self.service.ready.connect(self.restore_controls)
        self.retranslate()
        self.service.start()

    def bind(self, widget, key, default=None):
        self.bindings.append((widget.setText, key, default))
        widget.setText(self.locale.text(key, default))
        return widget

    def button(self, layout, key, callback, default=None):
        button = self.bind(QPushButton(), key, default)
        name = {
            "delete_history_btn": "trash-2", "clear_history_btn": "trash-2",
            "remove_layer_btn": "trash-2", "add_map_btn": "map-plus",
            "export_reexport": "arrow-down-to-line",
            "add_line_btn": "folder-open-line", "new_layer": "layer-add",
            "registered_data": "folder-recent", "rename_layer": "rename",
            "clear_bbox_btn": "bbox-clear",
        }.get(key)
        if name:
            button.setIcon(icon(name))
            button.setIconSize(ICON_SIZE)
        button.clicked.connect(lambda: self.call(callback))
        layout.addWidget(button)
        return button

    def form_field(self, form, key, widget, default=None):
        label = self.bind(QLabel(), key, default)
        form.addRow(label, widget)
        return widget

    def icon_button(self, layout, key, callback, asset, default=None):
        button = QToolButton()
        button.setObjectName(key)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setAutoRaise(True)
        button.setFixedSize(32, 32)
        button.setIcon(icon(asset))
        button.setIconSize(ICON_SIZE)
        for setter in (button.setToolTip, button.setAccessibleName):
            self.bindings.append((setter, key, default))
            setter(self.locale.text(key, default))
        button.clicked.connect(lambda: self.call(callback))
        layout.addWidget(button)
        return button

    def build_ui(self):
        splitter = QSplitter()
        self.setCentralWidget(splitter)
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        self.languages = QComboBox()
        for code, strings in sorted(self.locale.catalogues.items()):
            self.languages.addItem(strings.get("lang_name", code), code)
        self.languages.currentIndexChanged.connect(self.change_language)
        side.addWidget(self.languages)
        self.left_tabs = QTabWidget()
        self.left_tabs.setIconSize(ICON_SIZE)
        side.addWidget(self.left_tabs)
        splitter.addWidget(sidebar)
        self.tabs = QTabWidget()
        self.tabs.setIconSize(ICON_SIZE)
        self.tabs.addTab(self.map, "Map")
        self.table = QTableView()
        self.table.setModel(self.table_model)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setDefaultSectionSize(140)
        self.tabs.addTab(self.table, icon("attribute-table"), "Attributes")
        self.history_table = QTableView()
        self.history_table.setModel(self.history_model)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.selectionModel().currentRowChanged.connect(lambda current, previous: self.select_history(current) if current.isValid() else None)
        history = QWidget()
        layout = QVBoxLayout(history)
        layout.addWidget(self.history_table)
        self.reexport_button = self.button(layout, "export_reexport", self.reexport)
        self.button(layout, "delete_history_btn", self.delete_history)
        self.button(layout, "clear_history_btn", self.clear_history)
        self.tabs.addTab(history, icon("history"), "History")
        splitter.addWidget(self.tabs)
        splitter.setSizes([330, 950])

        layers = QWidget()
        layout = QVBoxLayout(layers)
        self.layer_list = QListWidget()
        self.layer_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.layer_list.itemChanged.connect(self.layer_changed)
        self.layer_list.currentItemChanged.connect(self.layer_selected)
        self.layer_list.model().rowsMoved.connect(self.layers_moved)
        layout.addWidget(self.layer_list)
        actions = QWidget()
        actions.setObjectName("layerActions")
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for key, callback, asset, default in (
            ("add_line_btn", self.open_data, "folder-open-line", None),
            ("new_layer", self.new_layer, "layer-add", "New Layer"),
            ("add_map_btn", self.add_map, "map-plus", None),
            ("registered_maps", self.registered_maps, "map-bookmark", "Registered Maps"),
            ("registered_data", self.registered_data, "folder-recent", "Recent Files"),
            ("rename_layer", self.rename_layer, "rename", "Rename"),
            ("remove_layer_btn", self.remove_layer, "trash-2", None),
        ):
            if row.count():
                row.addStretch()
            self.icon_button(row, key, callback, asset, default)
        layout.addWidget(actions)
        form = QFormLayout()
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.valueChanged.connect(self.opacity_changed)
        self.form_field(form, "label_opacity", self.opacity)
        layout.addLayout(form)
        self.left_tabs.addTab(layers, icon("layers"), "Layers")

        filters = QWidget()
        layout = QVBoxLayout(filters)
        form = QFormLayout()
        self.keywords = self.form_field(form, "label_keyword", QLineEdit())
        self.fields = self.form_field(form, "label_fields", QLineEdit())
        self.excludes = self.form_field(form, "label_exclude", QLineEdit())
        layout.addLayout(form)
        self.regex = self.bind(QCheckBox(), "regex_check")
        self.match_all = self.bind(QCheckBox(), "and_radio")
        self.lines_only = self.bind(QCheckBox(), "line_only_check")
        self.use_bbox = self.bind(QCheckBox(), "use_bbox_check")
        for check in (self.regex, self.match_all, self.lines_only, self.use_bbox):
            layout.addWidget(check)
            check.toggled.connect(self.filter_changed)
        for edit in (self.keywords, self.fields, self.excludes):
            edit.textChanged.connect(self.filter_changed)
        self.railways = {}
        for railway in ("rail", "subway", "tram", "light_rail", "monorail", "funicular", "abandoned"):
            check = self.bind(QCheckBox(), "railway_" + railway)
            check.setChecked(True)
            check.toggled.connect(self.filter_changed)
            self.railways[railway] = check
            layout.addWidget(check)
        bbox_form = QFormLayout()
        self.bbox_edits = []
        for key in ("min_lon_placeholder", "min_lat_placeholder", "max_lon_placeholder", "max_lat_placeholder"):
            edit = self.form_field(bbox_form, key, QLineEdit())
            edit.editingFinished.connect(self.bbox_edited)
            self.bbox_edits.append(edit)
        layout.addLayout(bbox_form)
        self.button(layout, "clear_bbox_btn", lambda: self.service.set_bbox(None))
        layout.addStretch()
        filter_scroll = QScrollArea()
        filter_scroll.setWidgetResizable(True)
        filter_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        filter_scroll.setWidget(filters)
        self.left_tabs.addTab(filter_scroll, icon("filter"), "Filter")

        toolbar = QToolBar()
        toolbar.setIconSize(ICON_SIZE)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.addToolBar(toolbar)
        self.draw_action = QAction(self)
        self.draw_action.setIcon(icon("pencil-line"))
        self.bind(self.draw_action, "draw_line_btn")
        self.draw_action.setCheckable(True)
        self.draw_action.toggled.connect(lambda active: self.map.send({"type": "draw", "active": active}))
        toolbar.addAction(self.draw_action)
        for key, callback, default, asset in (
            ("finish_drawing", self.finish_draft, "Finish Line", "draw-confirm"),
            ("cancel_drawing", self.cancel_draft, "Discard Draft", "draw-discard"),
            ("undo", self.service.undo_edit, "Undo", "undo"),
        ):
            action = self.bind(QAction(self), key, default)
            action.setIcon(icon(asset))
            action.triggered.connect(lambda checked=False, callback=callback: self.call(callback))
            toolbar.addAction(action)
        self.structure = StructureSelector()
        self.structure.valueChanged.connect(lambda value: self.map.send({"type": "structure", "value": value}))
        toolbar.addWidget(self.structure)
        self.cancel_action = self.bind(QAction(self), "cancel_operation", "Cancel Operation")
        self.cancel_action.setIcon(icon("operation-stop"))
        self.cancel_action.triggered.connect(self.service.jobs.cancel)
        toolbar.addAction(self.cancel_action)
        self.cancel_action.setEnabled(False)
        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.activated.connect(lambda: self.call(self.service.undo_edit))
        self.next_structure_shortcut = QShortcut(QKeySequence("E"), self)
        self.next_structure_shortcut.activated.connect(lambda: self.structure.step(1))
        self.prev_structure_shortcut = QShortcut(QKeySequence("Q"), self)
        self.prev_structure_shortcut.activated.connect(lambda: self.structure.step(-1))

        self.file_menu = self.menuBar().addMenu("File")
        for key, callback in (("action_open", self.open_data), ("export_action", self.export), ("action_open_html", self.preview), ("action_quit", self.close)):
            action = self.bind(QAction(self), key)
            asset = {"action_open": "folder-open-line", "export_action": "arrow-down-to-line", "action_open_html": "browser-preview"}.get(key)
            if asset:
                action.setIcon(icon(asset))
                action.setIconVisibleInMenu(True)
            action.triggered.connect(lambda checked=False, callback=callback: self.call(callback))
            self.file_menu.addAction(action)

    def call(self, callback):
        try:
            callback()
        except Exception as exc:
            self.error(str(exc))

    def error(self, message):
        self.statusBar().showMessage(self.locale.text("operation_failed"))
        if self.closing:
            self.closing = False
            self.setEnabled(True)
        box = QMessageBox(QMessageBox.Icon.Critical, self.locale.text("dialog_error"), self.locale.text("operation_failed"), parent=self)
        box.setDetailedText(str(message))
        box.exec()

    def busy_changed(self, busy):
        self.cancel_action.setEnabled(busy)
        if busy:
            self.statusBar().showMessage(self.locale.text("processing", "Processing…"))

    def change_language(self):
        if self.refreshing:
            return
        self.locale.choose(self.languages.currentData())
        self.service.state.language = self.locale.language
        self.service.touch(False)

    def retranslate(self):
        self.service.strings = self.locale.strings
        for setter, key, default in self.bindings:
            setter(self.locale.text(key, default))
        for index, key in enumerate(("left_tab_layers", "left_tab_filter")):
            self.left_tabs.setTabText(index, self.locale.text(key))
        for index, key in enumerate(("tab_map", "tab_table", "tab_history")):
            self.tabs.setTabText(index, self.locale.text(key))
        self.file_menu.setTitle(self.locale.text("menu_file"))
        self.setWindowTitle(f"{self.locale.text('window_title')} {__version__}")
        self.structure.set_labels({value: self.locale.text("structure_" + value) for value in self.structure.VALUES})
        self.map.send({"type": "locale", "language": self.locale.language, "strings": self.locale.strings})

    def restore_controls(self):
        if not self.service.initialized:
            self.centralWidget().setEnabled(False)
            for toolbar in self.findChildren(QToolBar):
                toolbar.setEnabled(False)
            return
        state = self.service.state
        self.refreshing = True
        if state.language:
            self.locale.choose(state.language)
        self.languages.setCurrentIndex(self.languages.findData(self.locale.language))
        options = state.filter
        for edit, key in ((self.keywords, "keywords"), (self.fields, "fields"), (self.excludes, "excludes")):
            edit.setText(", ".join(options.get(key, [])))
        for check, key in ((self.regex, "regex"), (self.match_all, "match_all"), (self.lines_only, "lines_only")):
            check.setChecked(options.get(key, False))
        self.use_bbox.setChecked(options.get("bbox") is not None)
        if options.get("railway_types") is not None:
            for railway, check in self.railways.items():
                check.setChecked(railway in options["railway_types"])
        self.refreshing = False
        self.map.send({"type": "view", "view": state.view})
        self.map.send({"type": "draft", "draft": state.draft})
        self.refresh()

    def refresh(self):
        self.refreshing = True
        state = self.service.state
        self.reexport_button.setEnabled(any(item["id"] == state.selected_history for item in state.history))
        self.draw_action.setEnabled(not self.service.draft_committing)
        self.layer_list.clear()
        for layer in state.layers:
            name = self.locale.text("map_layer_history") if layer.kind == "history" else layer.name
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, layer.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked)
            self.layer_list.addItem(item)
            if layer.id == state.selected:
                self.layer_list.setCurrentItem(item)
        layer = state.layer(state.selected)
        self.opacity.setEnabled(layer is not None)
        self.opacity.setValue(round(layer.opacity * 100) if layer else 100)
        for index, edit in enumerate(self.bbox_edits):
            if not edit.hasFocus():
                edit.setText(f"{state.bbox[index]:.7f}" if state.bbox else "")
        headers = self.locale.text("history_headers")
        signature = (tuple(headers), tuple((item["id"], item["name"], *item["bbox"], item["timestamp"]) for item in state.history))
        if signature != self.history_signature:
            selected_history_ids = {self.history_model.item(index.row(), 0).data(Qt.ItemDataRole.UserRole) for index in self.history_table.selectionModel().selectedRows()}
            self.history_signature = signature
            self.history_model.clear()
            self.history_model.setHorizontalHeaderLabels(headers)
            for history in state.history:
                items = [QStandardItem(str(value)) for value in [history["name"], *history["bbox"], history["timestamp"]]]
                items[0].setData(history["id"], Qt.ItemDataRole.UserRole)
                self.history_model.appendRow(items)
                index = self.history_model.index(self.history_model.rowCount()-1, 0)
                if history["id"] in selected_history_ids:
                    self.history_table.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        for row, history in enumerate(state.history):
            if history["id"] == state.selected_history:
                index = self.history_model.index(row, 0)
                self.history_table.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
                if not self.history_table.selectionModel().hasSelection():
                    self.history_table.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.refreshing = False

    def layer_changed(self, item):
        if not self.refreshing:
            layer = self.service.state.layer(item.data(Qt.ItemDataRole.UserRole))
            layer.visible = item.checkState() == Qt.CheckState.Checked
            self.service.touch(notify=False)

    def layer_selected(self, item, previous=None):
        if not self.refreshing:
            self.service.state.selected = item.data(Qt.ItemDataRole.UserRole) if item else None
            layer = self.service.state.layer(self.service.state.selected)
            self.refreshing = True
            self.opacity.setEnabled(layer is not None)
            self.opacity.setValue(round(layer.opacity*100) if layer else 100)
            self.refreshing = False
            self.service.touch(notify=False)

    def layers_moved(self, *args):
        if not self.refreshing:
            by_id = {layer.id: layer for layer in self.service.state.layers}
            self.service.state.layers = [by_id[self.layer_list.item(i).data(Qt.ItemDataRole.UserRole)] for i in range(self.layer_list.count())]
            QTimer.singleShot(0, lambda: self.service.touch(notify=False))

    def opacity_changed(self, value):
        if not self.refreshing:
            layer = self.service.state.layer(self.service.state.selected)
            if layer:
                layer.opacity = value / 100
                self.service.touch(notify=False)

    def filter_changed(self, *args):
        if self.refreshing:
            return
        split = lambda value: [part.strip() for part in re.split(r"[,、\n\t]+", value) if part.strip()]
        railways = [railway for railway, check in self.railways.items() if check.isChecked()]
        if "abandoned" in railways:
            railways.extend(["disused", "construction", "proposed"])
        self.service.state.filter = {"keywords": split(self.keywords.text()), "excludes": split(self.excludes.text()),
                                     "fields": split(self.fields.text()), "regex": self.regex.isChecked(),
                                     "match_all": self.match_all.isChecked(), "lines_only": self.lines_only.isChecked(),
                                     "railway_types": railways, "bbox": self.service.state.bbox if self.use_bbox.isChecked() else None}
        self.service.touch(notify=False)

    def bbox_edited(self):
        if not self.refreshing and all(edit.text().strip() for edit in self.bbox_edits):
            self.call(lambda: self.service.set_bbox([float(edit.text()) for edit in self.bbox_edits]))
            self.filter_changed()

    def rendered(self, payload):
        self.map.update_layers(payload)
        self.map.send({"type": "draft", "draft": self.service.state.draft})
        self.table_model.set_selection(self.service.selections.get(self.service.state.selected))
        count = sum(selection.count for selection in self.service.selections.values())
        total = sum(self.service.engine.capture(layer.id).count for layer in self.service.state.layers if layer.kind == "data")
        self.statusBar().showMessage(self.locale.text("msg_filtered_count").format(filtered=count, total=total))

    def open_data(self):
        paths, _ = QFileDialog.getOpenFileNames(self, self.locale.text("dialog_open_file_title"), self.service.state.last_directory,
                                               "GIS (*.zip *.shp *.geojson *.json);;All files (*)")
        for path in paths:
            self.service.load(path)

    def new_layer(self):
        name, ok = QInputDialog.getText(self, self.locale.text("new_layer", "New Layer"), self.locale.text("dialog_drawn_line_prompt"))
        if ok and name.strip():
            self.service.add_features(name.strip(), [])

    def registered_data(self):
        menu = QMenu(self)
        for path in self.service.state.registered_sources:
            action = menu.addAction(path, lambda path=path: self.service.load(path))
            action.setIcon(icon("folder-open-line"))
            action.setIconVisibleInMenu(True)
        menu.exec(self.cursor().pos())

    def add_map(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.locale.text("dialog_add_map_title"))
        form = QFormLayout(dialog)
        edits = [QLineEdit() for _ in range(3)]
        for key, edit in zip(("dialog_label_map_name", "dialog_label_map_url", "dialog_label_map_attr"), edits):
            form.addRow(self.locale.text(key), edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, url, attribution = [edit.text().strip() for edit in edits]
            if not name or not url.startswith(("https://", "http://")) or not all(token in url for token in ("{x}", "{y}", "{z}")):
                raise ValueError(self.locale.text("msg_validation_invalid_url"))
            self.service.add_map(name, url, attribution)

    def registered_maps(self):
        menu = QMenu(self)
        for item in self.service.state.registered_maps:
            menu.addAction(item["name"], lambda item=item: self.service.add_map(**item))
        menu.exec(self.cursor().pos())

    def rename_layer(self):
        layer = self.service.state.layer(self.service.state.selected)
        if layer and layer.kind != "history":
            name, ok = QInputDialog.getText(self, self.locale.text("rename_layer", "Rename"), self.locale.text("dialog_drawn_line_prompt"), text=layer.name)
            if ok and name.strip():
                layer.name = name.strip()
                self.service.touch()

    def remove_layer(self):
        layer = self.service.state.layer(self.service.state.selected)
        if layer and layer.kind != "history" and QMessageBox.question(self, self.locale.text("remove_layer_btn"), layer.name) == QMessageBox.StandardButton.Yes:
            self.service.remove_layer(layer.id)

    def select_history(self, index):
        if self.refreshing or not index.isValid():
            return
        history_id = self.history_model.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
        self.select_history_id(history_id)

    def select_history_id(self, history_id):
        item = next((h for h in self.service.state.history if h["id"] == history_id), None)
        if item:
            self.service.state.selected_history = history_id
            self.service.state.selected = HISTORY_ID
            self.service.state.layer(HISTORY_ID).visible = True
            self.service.set_bbox(item["bbox"])
            self.map.send({"type": "fit", "bbox": item["bbox"]})

    def delete_history(self):
        ids = {self.history_model.item(index.row(), 0).data(Qt.ItemDataRole.UserRole) for index in self.history_table.selectionModel().selectedRows()}
        if ids:
            self.service.delete_history(ids)

    def clear_history(self):
        if QMessageBox.question(self, self.locale.text("clear_history_btn"), self.locale.text("msg_clear_history_confirm")) == QMessageBox.StandardButton.Yes:
            self.service.delete_history({item["id"] for item in self.service.state.history})

    def finish_draft(self):
        if not self.service.state.draft.get("segments"):
            return
        name, ok = QInputDialog.getText(self, self.locale.text("dialog_drawn_line_title"), self.locale.text("dialog_drawn_line_prompt"))
        if ok and name.strip():
            self.draw_action.setChecked(False)
            self.service.finish_draft(name.strip())
            self.map.send({"type": "draft", "draft": self.service.state.draft})

    def cancel_draft(self):
        if self.service.state.draft.get("points") and QMessageBox.question(self, self.locale.text("cancel_drawing", "Discard Draft"), self.locale.text("discard_draft_confirm", "Discard the unfinished line?")) != QMessageBox.StandardButton.Yes:
            return
        self.service.state.draft = {"points": [], "segments": []}
        self.service.touch(False)
        self.map.send({"type": "draft", "draft": self.service.state.draft})

    def export(self, history_id=None):
        request = self.service.prepare_export(history_id)
        dialog = ExportDialog(request, self.service.state.export_options,
                              self.service.state.last_directory, self.locale, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        self.service.state.export_options = dialog.settings()
        self.service.touch(False, notify=False)
        if accepted:
            self.service.export(dialog.destination, dialog.format, dialog.options(), request)

    def reexport(self):
        if self.service.state.selected_history:
            self.export(self.service.state.selected_history)

    def preview(self):
        self.map.open_preview(Path(_native.data_directory()) / "preview.html", self.service.state.view, self.locale.language, self.locale.strings)

    def map_event(self, event):
        def handle():
            kind = event["type"]
            if kind == "bbox":
                self.service.set_bbox(event["bbox"])
                self.filter_changed()
            elif kind == "fetch_osm":
                self.service.fetch_osm()
            elif kind == "draft":
                self.service.state.draft = event["draft"]
                self.service.touch(False, notify=False)
            elif kind == "finish_draft":
                self.finish_draft()
            elif kind == "draw_end":
                self.draw_action.setChecked(False)
            elif kind == "edit":
                self.service.edit(event["layer_id"], event["feature"])
            elif kind == "history":
                self.select_history_id(event["id"])
            elif kind == "view":
                self.service.state.view = event["view"]
                self.service.touch(False, notify=False)
        self.call(handle)

    def closeEvent(self, event):
        if self.close_allowed or not self.service.initialized:
            event.accept()
            return
        event.ignore()
        if not self.closing:
            self.closing = True
            self.setEnabled(False)
            self.service.render_timer.stop()
            self.check_close()

    def check_close(self):
        if not self.closing:
            return
        if self.service.jobs.inflight or self.service.edit_busy:
            QTimer.singleShot(100, self.check_close)
        elif self.service.save_pending:
            self.service.save()
            QTimer.singleShot(100, self.check_close)
        else:
            self.close_allowed = True
            self.close()
