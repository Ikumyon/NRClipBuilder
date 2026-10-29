from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSlider, QSplitter, QTabWidget, QTableView, QToolBar, QVBoxLayout, QWidget,
)

from .. import __version__, _native
from ..services import ApplicationService
from ..state import HISTORY_ID, Layer
from .localisation import Localisation
from .icons import ICON_SIZE, icon
from .map_view import MapView
from .structure_selector import StructureSelector
from .table import FeatureTable


class MainWindow(QMainWindow):
    def __init__(self, service=None, show_errors=True):
        super().__init__()
        self.service = service or ApplicationService(self)
        self.show_errors = show_errors
        self.locale = Localisation(self)
        self.bindings = []
        self.refreshing = False
        self.closing = False
        self.close_allowed = False
        self.resize(1280, 820)
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
            "action_export_geojson": "arrow-down-to-line",
            "action_export_turnout": "arrow-down-to-line",
            "action_export_nrclip": "arrow-down-to-line",
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
        self.tabs.addTab(self.table, "Attributes")
        self.history_table = QTableView()
        self.history_table.setModel(self.history_model)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.clicked.connect(self.select_history)
        history = QWidget()
        layout = QVBoxLayout(history)
        layout.addWidget(self.history_table)
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
        row = QHBoxLayout()
        self.button(row, "add_line_btn", self.open_data)
        self.button(row, "new_layer", self.new_layer, "New Layer")
        layout.addLayout(row)
        row = QHBoxLayout()
        self.button(row, "add_map_btn", self.add_map)
        self.button(row, "registered_data", self.registered_data, "Recent Files")
        layout.addLayout(row)
        row = QHBoxLayout()
        self.button(row, "rename_layer", self.rename_layer, "Rename")
        self.button(row, "remove_layer_btn", self.remove_layer)
        layout.addLayout(row)
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
        self.left_tabs.addTab(filters, "Filter")

        exports = QWidget()
        layout = QVBoxLayout(exports)
        form = QFormLayout()
        self.options = {}
        for name, key, value, minimum, maximum in (
            ("scale_x", "scale_x", 1, .001, 1000), ("scale_y", "scale_y", 1, .001, 1000),
            ("spline_tolerance", "label_spline_tolerance", 2, .01, 10000),
            ("junction_spacing", "label_junction_spacing", 30, 0, 10000),
            ("max_spacing", "label_max_spacing", 2000, 1, 100000),
            ("curve_r_tolerance", "curve_radius_tolerance", 5, 0, 100),
        ):
            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(minimum, maximum)
            spin.setValue(value)
            self.form_field(form, key, spin, name.replace("_", " ").title())
            spin.valueChanged.connect(self.options_changed)
            self.options[name] = spin
        layout.addLayout(form)
        self.tangent = self.bind(QCheckBox(), "track_mode_tangent")
        self.curve_merge = self.bind(QCheckBox(), "curve_merge", "Merge constant-radius arcs")
        self.curve_merge.setIcon(icon("spline"))
        self.curve_merge.setIconSize(ICON_SIZE)
        self.curve_merge.setChecked(True)
        for check in (self.tangent, self.curve_merge):
            check.toggled.connect(self.options_changed)
            layout.addWidget(check)
        for key, format in (("action_export_geojson", "geojson"), ("action_export_turnout", "overpass"), ("action_export_nrclip", "nrclip")):
            self.button(layout, key, lambda format=format: self.export(format))
        layout.addStretch()
        self.left_tabs.addTab(exports, "Export")

        toolbar = QToolBar()
        toolbar.setIconSize(ICON_SIZE)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)
        self.draw_action = QAction(self)
        self.draw_action.setIcon(icon("pencil-line"))
        self.bind(self.draw_action, "draw_line_btn")
        self.draw_action.setCheckable(True)
        self.draw_action.toggled.connect(lambda active: self.map.send({"type": "draw", "active": active}))
        toolbar.addAction(self.draw_action)
        for key, callback, default in (("finish_drawing", self.finish_draft, "Finish Line"), ("cancel_drawing", self.cancel_draft, "Discard Draft"), ("undo", self.service.undo_edit, "Undo")):
            action = self.bind(QAction(self), key, default)
            action.triggered.connect(lambda checked=False, callback=callback: self.call(callback))
            toolbar.addAction(action)
        self.structure = StructureSelector()
        self.structure.valueChanged.connect(lambda value: self.map.send({"type": "structure", "value": value}))
        toolbar.addWidget(self.structure)
        self.cancel_button = QPushButton()
        self.bind(self.cancel_button, "cancel_operation", "Cancel Operation")
        self.cancel_button.clicked.connect(self.service.jobs.cancel)
        toolbar.addWidget(self.cancel_button)
        self.cancel_button.setEnabled(False)
        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.activated.connect(self.service.undo_edit)
        self.next_structure_shortcut = QShortcut(QKeySequence("E"), self)
        self.next_structure_shortcut.activated.connect(lambda: self.structure.step(1))
        self.prev_structure_shortcut = QShortcut(QKeySequence("Q"), self)
        self.prev_structure_shortcut.activated.connect(lambda: self.structure.step(-1))

        self.file_menu = self.menuBar().addMenu("File")
        for key, callback in (("action_open", self.open_data), ("action_open_html", self.preview), ("action_quit", self.close)):
            action = self.bind(QAction(self), key)
            action.triggered.connect(lambda checked=False, callback=callback: self.call(callback))
            self.file_menu.addAction(action)

    def call(self, callback):
        try:
            callback()
        except Exception as exc:
            self.error(str(exc))

    def error(self, message):
        self.statusBar().showMessage(message)
        if self.closing:
            self.closing = False
            self.setEnabled(True)
        if self.show_errors:
            QMessageBox.critical(self, self.locale.text("dialog_error"), str(message))
        else:
            print(message, flush=True)

    def busy_changed(self, busy):
        self.cancel_button.setEnabled(busy)
        if busy:
            self.statusBar().showMessage(self.locale.text("processing", "Processing…"))

    def change_language(self):
        if self.refreshing:
            return
        self.locale.choose(self.languages.currentData())
        self.service.state.language = self.locale.language
        self.service.touch(False)

    def retranslate(self):
        for setter, key, default in self.bindings:
            setter(self.locale.text(key, default))
        for index, key in enumerate(("left_tab_layers", "left_tab_filter", "left_tab_export")):
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
        for key, spin in self.options.items():
            spin.setValue(state.export_options.get(key, spin.value()))
        self.tangent.setChecked(state.export_options.get("tangent_mode", False))
        self.curve_merge.setChecked(state.export_options.get("enable_curve_r_merging", True))
        self.refreshing = False
        self.map.send({"type": "view", "view": state.view})
        self.map.send({"type": "draft", "draft": state.draft})
        self.refresh()

    def refresh(self):
        self.refreshing = True
        state = self.service.state
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
        self.history_model.clear()
        self.history_model.setHorizontalHeaderLabels(self.locale.text("history_headers"))
        for history in state.history:
            items = [QStandardItem(str(value)) for value in [history["name"], *history["bbox"], history["timestamp"]]]
            items[0].setData(history["id"], Qt.ItemDataRole.UserRole)
            self.history_model.appendRow(items)
            if history["id"] == state.selected_history:
                self.history_table.setCurrentIndex(self.history_model.index(self.history_model.rowCount()-1, 0))
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

    def options_changed(self, *args):
        if not self.refreshing:
            self.service.state.export_options = self.export_options()
            self.service.touch(False, notify=False)

    def export_options(self):
        return {**{name: spin.value() for name, spin in self.options.items()},
                "tangent_mode": self.tangent.isChecked(), "enable_curve_r_merging": self.curve_merge.isChecked()}

    def bbox_edited(self):
        if not self.refreshing and all(edit.text().strip() for edit in self.bbox_edits):
            self.call(lambda: self.service.set_bbox([float(edit.text()) for edit in self.bbox_edits]))
            self.filter_changed()

    def rendered(self, payload):
        self.map.update_layers(payload)
        self.map.send({"type": "draft", "draft": self.service.state.draft})
        self.table_model.set_selection(self.service.selections.get(self.service.state.selected))
        count = sum(selection.count for selection in self.service.selections.values())
        self.statusBar().showMessage(f"{count:,} features")

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
            menu.addAction(path, lambda path=path: self.service.load(path))
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
            self.service.state.layers.append(Layer(name, kind="tile", url=url, attribution=attribution))
            self.service.touch()

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
        if self.refreshing:
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
        history_id = self.service.state.selected_history
        if history_id:
            self.service.state.history = [h for h in self.service.state.history if h["id"] != history_id]
            self.service.state.selected_history = None
            self.service.rebuild_history()

    def clear_history(self):
        if QMessageBox.question(self, self.locale.text("clear_history_btn"), self.locale.text("msg_clear_history_confirm")) == QMessageBox.StandardButton.Yes:
            self.service.state.history.clear()
            self.service.state.selected_history = None
            self.service.rebuild_history()

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

    def export(self, format):
        suffix = {"geojson": "geojson", "overpass": "json", "nrclip": "nrclip"}[format]
        path, _ = QFileDialog.getSaveFileName(self, self.locale.text("msg_export_title"), str(Path(self.service.state.last_directory) / f"tracks.{suffix}"), f"{format} (*.{suffix})")
        if path:
            self.service.export(path, format, self.export_options())

    def preview(self):
        self.map.open_preview(Path(_native.data_directory()) / "preview.html")

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
