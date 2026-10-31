"""Format-specific export settings with a frozen, visible input selection."""
from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from .icons import ICON_SIZE, icon
from .segmented_control import SegmentedControl

FORMATS = (("nrclip", "NRC1", ".nrclip"), ("pmtiles", "PMTiles", ".pmtiles"),
           ("geojson", "GeoJSON", ".geojson"), ("overpass", "Turnout JSON", ".json"))
NRC_OPTIONS = (
    ("scale_x", "scale_x", 1, .001, 1000),
    ("scale_y", "scale_y", 1, .001, 1000),
    ("spline_tolerance", "label_spline_tolerance", 2, .01, 10000),
    ("junction_spacing", "label_junction_spacing", 30, 0, 10000),
    ("max_spacing", "label_max_spacing", 2000, 1, 100000),
    ("curve_r_tolerance", "curve_radius_tolerance", 5, 0, 100),
)


class ExportDialog(QDialog):
    def __init__(self, request, saved, directory, locale, parent=None):
        super().__init__(parent)
        self.request = request
        self.text = locale.text
        self.setWindowTitle(self.text("export_dialog_title"))
        self.setWindowIcon(icon("arrow-down-to-line"))
        self.resize(620, 650)
        self.setMinimumSize(440, 400)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.text("export_history_target" if request.history else "export_checked_targets")))
        self.targets = QListWidget()
        self.targets.addItems(request.names)
        self.targets.setMaximumHeight(90)
        layout.addWidget(self.targets)
        bbox = ", ".join(f"{v:.7f}" for v in request.bbox) if request.bbox else self.text("export_bbox_missing")
        bounds = QLabel(f"BBox: {bbox}")
        bounds.setWordWrap(True)
        layout.addWidget(bounds)
        filters = json.loads(request.filters)
        active = any(value for key, value in filters.items() if key not in {"fields", "regex", "match_all", "lines_only"}) or filters.get("lines_only", False)
        summary = QLabel(self.text("export_history_filters" if request.history else "export_filters_active" if active else "export_filters_none"))
        summary.setWordWrap(True)
        layout.addWidget(summary)
        self.tabs = QTabWidget()
        self.nrc = {}
        self.zoom = {}
        self.track_mode = SegmentedControl(("point", "tangent"))
        self.track_mode.setObjectName("trackModeSelector")
        self.track_mode.set_labels({"point": self.text("track_mode_point"), "tangent": self.text("track_mode_tangent")})
        self.track_mode.set_value("tangent" if saved.get("nrclip", {}).get("tangent_mode", False) else "point")
        self.curve_merge = QCheckBox(self.text("curve_merge"))
        self.curve_merge.setChecked(saved.get("nrclip", {}).get("enable_curve_r_merging", True))
        for format, title, _ in FORMATS:
            content = QWidget()
            content_layout = QVBoxLayout(content)
            form = QFormLayout()
            if format == "nrclip":
                for name, key, default, minimum, maximum in NRC_OPTIONS:
                    spin = QDoubleSpinBox()
                    spin.setDecimals(3)
                    spin.setRange(minimum, maximum)
                    spin.setValue(saved.get(format, {}).get(name, default))
                    self.nrc[name] = spin
                    form.addRow(self.text(key), spin)
                    spin.valueChanged.connect(self.validate)
                    spin.lineEdit().textChanged.connect(self.validate)
                form.addRow(self.text("label_track_mode"), self.track_mode)
                content_layout.addLayout(form)
                content_layout.addWidget(self.curve_merge)
            elif format == "pmtiles":
                for name, key, default in (("min_zoom", "export_min_zoom", 0), ("max_zoom", "export_max_zoom", 14)):
                    spin = QSpinBox()
                    spin.setRange(0, 22)
                    spin.setValue(saved.get(format, {}).get(name, default))
                    self.zoom[name] = spin
                    form.addRow(self.text(key), spin)
                    spin.valueChanged.connect(self.validate)
                    spin.lineEdit().textChanged.connect(self.validate)
                content_layout.addLayout(form)
                description = QLabel(self.text("export_pmtiles_description"))
                description.setWordWrap(True)
                content_layout.addWidget(description)
            else:
                content_layout.addWidget(QLabel(self.text("export_no_settings")))
            content_layout.addStretch()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            self.tabs.addTab(scroll, title)
        layout.addWidget(self.tabs, 1)
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel(self.text("export_destination")))
        self.path = QLineEdit(str(Path(directory or Path.home()) / "tracks.nrclip"))
        path_layout.addWidget(self.path, 1)
        browse = QPushButton(self.text("export_browse"))
        browse.setIcon(icon("save"))
        browse.setIconSize(ICON_SIZE)
        browse.clicked.connect(self.browse)
        path_layout.addWidget(browse)
        layout.addLayout(path_layout)
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        layout.addWidget(self.problem)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText(self.text("export_execute"))
        self.save_button.setIcon(icon("arrow-down-to-line"))
        self.save_button.setIconSize(ICON_SIZE)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.text("export_cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.path.textChanged.connect(self.validate)
        self.path.editingFinished.connect(self.normalize_path)
        self.tabs.currentChanged.connect(self.format_changed)
        self.tabs.setCurrentIndex(next((i for i, (key, _, _) in enumerate(FORMATS) if key == saved.get("format")), 0))
        self.format_changed()

    @property
    def format(self):
        return FORMATS[self.tabs.currentIndex()][0]

    def options(self):
        if self.format == "nrclip":
            return {**{key: spin.value() for key, spin in self.nrc.items()},
                    "tangent_mode": self.track_mode.value() == "tangent", "enable_curve_r_merging": self.curve_merge.isChecked()}
        if self.format == "pmtiles":
            return {key: spin.value() for key, spin in self.zoom.items()}
        return {}

    def settings(self):
        return {"format": self.format, "nrclip": {**{key: spin.value() for key, spin in self.nrc.items()},
                "tangent_mode": self.track_mode.value() == "tangent", "enable_curve_r_merging": self.curve_merge.isChecked()},
                "pmtiles": {key: spin.value() for key, spin in self.zoom.items()}}

    def normalize_path(self):
        raw = self.path.text().strip()
        if raw:
            path = Path(raw)
            suffix = FORMATS[self.tabs.currentIndex()][2]
            if path.suffix.lower() != suffix:
                try:
                    self.path.setText(str(path.with_suffix(suffix)))
                except ValueError:
                    self.validate()

    def format_changed(self, *_):
        self.normalize_path()
        self.validate()

    def browse(self):
        _, title, suffix = FORMATS[self.tabs.currentIndex()]
        path, _ = QFileDialog.getSaveFileName(self, self.text("export_destination"), self.path.text(), f"{title} (*{suffix})",
                                             options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            self.path.setText(path)
            self.normalize_path()

    def validate(self, *_):
        # Spin boxes can emit while the settings pages are being constructed.
        if not hasattr(self, "save_button"):
            return
        problem = ""
        if not self.request.names:
            problem = self.text("export_no_targets")
        elif self.request.bbox is None:
            problem = self.text("export_bbox_missing")
        elif self.format == "pmtiles" and (any(not spin.hasAcceptableInput() for spin in self.zoom.values()) or self.zoom["min_zoom"].value() > self.zoom["max_zoom"].value()):
            problem = self.text("export_invalid_zoom")
        elif self.format == "nrclip" and any(not math.isfinite(spin.value()) or not spin.hasAcceptableInput() for spin in self.nrc.values()):
            problem = self.text("export_invalid_settings")
        elif not self.path.text().strip():
            problem = self.text("export_path_missing")
        else:
            path = Path(self.path.text().strip())
            if not path.name or path.is_dir() or not path.parent.is_dir():
                problem = self.text("export_invalid_path")
        self.problem.setText(problem)
        self.save_button.setEnabled(not problem)

    def accept(self):
        self.normalize_path()
        self.validate()
        if not self.save_button.isEnabled():
            return
        path = Path(self.path.text().strip())
        if path.exists() and QMessageBox.question(self, self.text("export_overwrite_title"), self.text("export_overwrite").format(path=path)) != QMessageBox.StandardButton.Yes:
            return
        self.destination = path
        super().accept()
