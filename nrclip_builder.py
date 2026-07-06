#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
N05 / SHP / GeoJSON Map Filter Exporter

A small PySide6 desktop app that loads railway line data, previews it on a Leaflet map,
filters by attributes, and exports filtered GeoJSON or Turnout-compatible Overpass JSON.

Designed for Japanese National Land Numerical Information N05 RailroadSection2 shapefiles,
but also works with ordinary GeoJSON and shapefiles whose coordinates are lon/lat or can be
reprojected by pyproj.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import sys
import tempfile
import traceback
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from core.geojson import iter_lines_from_geometry, geojson_to_overpass
from core.nrclip import geojson_to_nrclip_bytes

try:
    import shapefile  # pyshp
except Exception:  # pragma: no cover
    shapefile = None

try:
    from pyproj import CRS, Transformer
except Exception:  # pragma: no cover
    CRS = None
    Transformer = None

from PySide6.QtCore import Qt, QUrl, QFile
from PySide6.QtUiTools import QUiLoader
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QRadioButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings
    HAS_WEBENGINE = True
except Exception:
    QWebEngineView = None
    QWebEngineSettings = None
    HAS_WEBENGINE = False


APP_TITLE = "NRClipBuilder"
DEFAULT_KEYWORDS = "歌登,幌別,本幌別"
DEFAULT_SEARCH_FIELDS = "*"
MAX_TABLE_ROWS = 300


@dataclass
class FeatureStore:
    features: list[dict[str, Any]] = field(default_factory=list)
    source_path: Optional[Path] = None
    temp_dir: Optional[Path] = None
    fields: list[str] = field(default_factory=list)
    crs_note: str = ""

    def cleanup(self) -> None:
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.temp_dir = None

    def feature_collection(self, features: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        return {
            "type": "FeatureCollection",
            "features": features if features is not None else self.features,
        }


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for enc in ("utf-8", "cp932", "shift_jis"):
            try:
                return value.decode(enc)
            except Exception:
                pass
        return value.decode("latin1", errors="replace")
    return str(value)


def normalize_feature(feature: dict[str, Any]) -> Optional[dict[str, Any]]:
    geom = feature.get("geometry")
    if not geom:
        return None
    props = feature.get("properties") or {}
    return {"type": "Feature", "properties": props, "geometry": geom}


def collect_fields(features: list[dict[str, Any]]) -> list[str]:
    keys: set[str] = set()
    for feat in features:
        props = feat.get("properties") or {}
        keys.update(str(k) for k in props.keys())
    def sort_key(k: str) -> tuple[int, str]:
        # Keep N05_* fields first, then alphabetical.
        return (0 if k.startswith("N05_") else 1, k)
    return sorted(keys, key=sort_key)


def load_geojson(path: Path) -> FeatureStore:
    data = json.loads(path.read_text(encoding="utf-8"))
    features: list[dict[str, Any]] = []

    if data.get("type") == "FeatureCollection":
        raw = data.get("features") or []
        for feat in raw:
            norm = normalize_feature(feat)
            if norm:
                features.append(norm)
    elif data.get("type") == "Feature":
        norm = normalize_feature(data)
        if norm:
            features.append(norm)
    elif "type" in data and "coordinates" in data:
        features.append({"type": "Feature", "properties": {}, "geometry": data})
    else:
        raise ValueError("GeoJSONとして読めませんでした。FeatureCollection / Feature / Geometry が必要です。")

    return FeatureStore(features=features, source_path=path, fields=collect_fields(features), crs_note="GeoJSONとして読み込みました。座標はEPSG:4326想定です。")


def find_shapefile_in_zip(zip_path: Path) -> tuple[Path, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="n05_exporter_"))
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(temp_dir)

    shp_files = list(temp_dir.rglob("*.shp"))
    if not shp_files:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise ValueError("ZIP内に .shp が見つかりませんでした。")

    # Prefer N05 line layer.
    preferred = [p for p in shp_files if "RailroadSection" in p.name]
    if preferred:
        return preferred[0], temp_dir

    # Then any line-like layer.
    preferred = [p for p in shp_files if any(s in p.name.lower() for s in ("rail", "line", "section"))]
    if preferred:
        return preferred[0], temp_dir

    return shp_files[0], temp_dir


def read_prj_for(path: Path) -> Optional[str]:
    prj = path.with_suffix(".prj")
    if prj.exists():
        try:
            return prj.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return prj.read_text(encoding="cp932", errors="ignore")
    return None


def make_transformer_to_wgs84(path: Path) -> tuple[Optional[Any], str]:
    if CRS is None or Transformer is None:
        return None, "pyprojが無いため、SHPの座標変換は行っていません。N05の経緯度データなら通常そのまま使えます。"

    prj_text = read_prj_for(path)
    if not prj_text:
        return None, "PRJが無いため座標変換なしで読み込みました。N05は通常このままで問題ありません。"

    try:
        source_crs = CRS.from_wkt(prj_text)
        target_crs = CRS.from_epsg(4326)
        if source_crs == target_crs or source_crs.to_epsg() in (4326, 4612, 6668):
            return None, f"CRS: {source_crs.to_string()}。経緯度として読み込みました。"
        transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
        return transformer, f"CRS: {source_crs.to_string()} → EPSG:4326 に変換しました。"
    except Exception as exc:
        return None, f"PRJ解析に失敗したため座標変換なしで読み込みました: {exc}"


def transform_coords(obj: Any, transformer: Any) -> Any:
    if transformer is None:
        return obj
    if isinstance(obj, (list, tuple)):
        # Coordinate pair or triple: [x, y, ...]
        if len(obj) >= 2 and all(isinstance(v, (int, float)) for v in obj[:2]):
            x, y = transformer.transform(float(obj[0]), float(obj[1]))
            rest = list(obj[2:])
            return [x, y] + rest
        return [transform_coords(v, transformer) for v in obj]
    return obj


def transform_geometry(geom: dict[str, Any], transformer: Any) -> dict[str, Any]:
    if transformer is None:
        return geom
    g = dict(geom)
    if g.get("type") == "GeometryCollection":
        g["geometries"] = [transform_geometry(x, transformer) for x in g.get("geometries", [])]
    elif "coordinates" in g:
        g["coordinates"] = transform_coords(g["coordinates"], transformer)
    return g


def load_shapefile(path: Path, temp_dir: Optional[Path] = None) -> FeatureStore:
    if shapefile is None:
        raise RuntimeError("pyshpがインストールされていません。pip install pyshp を実行してください。")

    last_error: Optional[Exception] = None
    reader = None
    for enc in ("utf-8", "cp932", "shift_jis", "latin1"):
        try:
            reader = shapefile.Reader(str(path), encoding=enc)
            # Force DBF decode early.
            _ = reader.fields
            if len(reader) > 0:
                _ = reader.record(0)
            break
        except Exception as exc:
            last_error = exc
            reader = None
    if reader is None:
        raise RuntimeError(f"SHP/DBFを読めませんでした: {last_error}")

    fields = [f[0] for f in reader.fields[1:]]
    transformer, crs_note = make_transformer_to_wgs84(path)
    features: list[dict[str, Any]] = []

    for sr in reader.iterShapeRecords():
        props = {name: safe_str(value).strip() for name, value in zip(fields, sr.record)}
        try:
            geom = sr.shape.__geo_interface__
        except Exception:
            continue
        if not geom:
            continue
        geom = transform_geometry(geom, transformer)
        features.append({"type": "Feature", "properties": props, "geometry": geom})

    return FeatureStore(features=features, source_path=path, temp_dir=temp_dir, fields=collect_fields(features), crs_note=crs_note)


def load_any(path: Path) -> FeatureStore:
    suffix = path.suffix.lower()
    if suffix in (".geojson", ".json"):
        return load_geojson(path)
    if suffix == ".shp":
        return load_shapefile(path)
    if suffix == ".zip":
        shp, temp = find_shapefile_in_zip(path)
        store = load_shapefile(shp, temp_dir=temp)
        store.source_path = path
        return store
    raise ValueError("対応形式は .zip / .shp / .geojson / .json です。")


def split_csv_like(text: str) -> list[str]:
    parts = re.split(r"[,、\n\t]+", text)
    return [p.strip() for p in parts if p.strip()]


def feature_text(props: dict[str, Any], fields: list[str]) -> str:
    if not fields or fields == ["*"]:
        vals = props.values()
    else:
        vals = [props.get(f, "") for f in fields]
    return "\n".join(safe_str(v) for v in vals)


def filter_features(
    features: list[dict[str, Any]],
    keywords_text: str,
    fields_text: str,
    regex: bool,
    match_all: bool,
    exclude_text: str,
) -> list[dict[str, Any]]:
    keywords = split_csv_like(keywords_text)
    excludes = split_csv_like(exclude_text)
    fields = split_csv_like(fields_text)
    if not fields:
        fields = ["*"]

    def matches_one(needle: str, haystack: str) -> bool:
        if regex:
            try:
                return re.search(needle, haystack, flags=re.IGNORECASE) is not None
            except re.error:
                return False
        return needle.lower() in haystack.lower()

    result: list[dict[str, Any]] = []
    for feat in features:
        props = feat.get("properties") or {}
        text = feature_text(props, fields)

        if excludes and any(matches_one(ex, text) for ex in excludes):
            continue

        if not keywords:
            result.append(feat)
            continue

        checks = [matches_one(k, text) for k in keywords]
        if (all(checks) if match_all else any(checks)):
            result.append(feat)

    return result









def bounds_from_features(features: list[dict[str, Any]]) -> Optional[tuple[float, float, float, float]]:
    xs: list[float] = []
    ys: list[float] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, (list, tuple)):
            if len(obj) >= 2 and all(isinstance(v, (int, float)) for v in obj[:2]):
                xs.append(float(obj[0]))
                ys.append(float(obj[1]))
            else:
                for v in obj:
                    walk(v)

    for feat in features:
        geom = feat.get("geometry") or {}
        if "coordinates" in geom:
            walk(geom["coordinates"])
        elif geom.get("type") == "GeometryCollection":
            for g in geom.get("geometries", []) or []:
                if "coordinates" in g:
                    walk(g["coordinates"])
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def make_leaflet_html(geojson: dict[str, Any], title: str = APP_TITLE) -> str:
    # Compact JSON to avoid huge HTML.
    gj = json.dumps(geojson, ensure_ascii=False, separators=(",", ":"))
    feature_count = len(geojson.get("features", []))
    return f"""<!doctype html>
<html lang=\"ja\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>{html.escape(title)}</title>
<link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\" />
<style>
  html, body, #map {{ height: 100%; margin: 0; }}
  .info {{ background: white; padding: 8px 10px; border-radius: 4px; box-shadow: 0 1px 5px rgba(0,0,0,.35); font: 13px/1.4 sans-serif; }}
</style>
</head>
<body>
<div id=\"map\"></div>
<script src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\"></script>
<script>
const data = {gj};
const map = L.map('map', {{ zoomControl: true }});
const gsiStd = L.tileLayer('https://cyberjapandata.gsi.go.jp/xyz/std/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '国土地理院地図', maxZoom: 18
}});
const osm = L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '&copy; OpenStreetMap contributors', maxZoom: 19
}});
gsiStd.addTo(map);
L.control.layers({{'国土地理院地図': gsiStd, 'OpenStreetMap': osm}}, null, {{collapsed: false}}).addTo(map);

function propHtml(props) {{
  const keys = Object.keys(props || {{}}).slice(0, 30);
  if (!keys.length) return '(属性なし)';
  return '<table>' + keys.map(k => '<tr><th style=\"text-align:left;padding-right:8px\">' + escapeHtml(k) + '</th><td>' + escapeHtml(String(props[k] ?? '')) + '</td></tr>').join('') + '</table>';
}}
function escapeHtml(s) {{ return s.replace(/[&<>\"']/g, m => ({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[m])); }}

const layer = L.geoJSON(data, {{
  style: function(feature) {{ return {{ color: '#d6336c', weight: 5, opacity: 0.9 }}; }},
  pointToLayer: function(feature, latlng) {{ return L.circleMarker(latlng, {{ radius: 5, color: '#d6336c', weight: 2, fillOpacity: 0.8 }}); }},
  onEachFeature: function(feature, layer) {{ layer.bindPopup(propHtml(feature.properties)); }}
}}).addTo(map);
const b = layer.getBounds();
if (b.isValid()) map.fitBounds(b.pad(0.15)); else map.setView([44.8, 142.5], 9);
const info = L.control({{position:'bottomleft'}});
info.onAdd = function() {{ const div = L.DomUtil.create('div','info'); div.innerHTML = '<b>{html.escape(title)}</b><br>{feature_count} features'; return div; }};
info.addTo(map);
</script>
</body>
</html>"""


class MapWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if HAS_WEBENGINE:
            self.web = QWebEngineView()
            if QWebEngineSettings is not None:
                settings = self.web.settings()
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            layout.addWidget(self.web)
        else:
            self.web = QTextBrowser()
            self.web.setOpenExternalLinks(True)
            self.web.setHtml("<p>PySide6 QtWebEngine が見つかりません。<br>requirements.txt の依存関係を入れてください。</p>")
            layout.addWidget(self.web)
        self.last_html_path: Optional[Path] = None

    def set_geojson(self, geojson: dict[str, Any], title: str) -> None:
        html_text = make_leaflet_html(geojson, title=title)
        out = Path(tempfile.gettempdir()) / "n05_map_filter_exporter_preview.html"
        out.write_text(html_text, encoding="utf-8")
        self.last_html_path = out
        if HAS_WEBENGINE:
            self.web.load(QUrl.fromLocalFile(str(out)))
        else:
            self.web.setHtml(
                f"<p>地図プレビューHTMLを作成しました:</p><p><a href='{out.as_uri()}'>{html.escape(str(out))}</a></p>"
            )


class UiLoader(QUiLoader):
    def __init__(self, baseinstance) -> None:
        super().__init__()
        self.baseinstance = baseinstance

    def createWidget(self, classname: str, parent: Optional[QWidget] = None, name: str = "") -> QWidget:
        if parent is None and self.baseinstance:
            return self.baseinstance
        return super().createWidget(classname, parent, name)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.store = FeatureStore()
        self.filtered: list[dict[str, Any]] = []
        self.last_output_dir = Path.cwd()

        # UIのロード
        loader = UiLoader(self)
        ui_path = Path(__file__).parent / "ui" / "main_window.ui"
        ui_file = QFile(str(ui_path))
        if not ui_file.open(QFile.ReadOnly):
            raise RuntimeError(f"UIファイルを開けませんでした: {ui_path}")
        loader.load(ui_file)
        ui_file.close()

        # テーブルのプロパティ設定
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

        # 地図プレビューウィジェットの追加
        self.map_widget = MapWidget()
        self.map_tab.layout().addWidget(self.map_widget)

        # イベントの接続
        self.apply_btn.clicked.connect(self.apply_filter)

        # メニューアクション of 接続
        self.action_open.triggered.connect(self.open_file)
        self.action_export_geojson.triggered.connect(self.export_geojson)
        self.action_export_turnout.triggered.connect(self.export_turnout_json)
        self.action_export_nrclip.triggered.connect(self.export_nrclip)
        self.action_open_html.triggered.connect(self.open_preview_in_browser)
        self.action_quit.triggered.connect(self.close)

        # ステータスバーと初期化
        self.statusBar().showMessage("ファイルを開いてください。対応: N05 ZIP / SHP / GeoJSON")
        self._refresh_map([])

    def log_msg(self, msg: str) -> None:
        self.log.appendPlainText(msg)
        self.statusBar().showMessage(msg)

    def open_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "データを開く",
            str(self.last_output_dir),
            "GIS data (*.zip *.shp *.geojson *.json);;All files (*.*)",
        )
        if not path_str:
            return
        self.load_path(Path(path_str))

    def load_path(self, path: Path) -> None:
        try:
            self.store.cleanup()
            self.setWindowTitle(f"{APP_TITLE} - {path.name}")
            self.log.clear()
            self.log_msg(f"読み込み中: {path}")
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.store = load_any(path)
            QApplication.restoreOverrideCursor()
            self.last_output_dir = path.parent
            self.log_msg(f"読み込み完了: {len(self.store.features):,} features")
            if self.store.fields:
                self.log_msg("フィールド: " + ", ".join(self.store.fields[:60]) + (" ..." if len(self.store.fields) > 60 else ""))
            self.log_msg(self.store.crs_note)
            self.update_info_label()
            self.apply_filter()
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            self.log_msg("エラー: " + str(exc))
            QMessageBox.critical(self, "読み込みエラー", f"{exc}\n\n{traceback.format_exc()}")

    def update_info_label(self) -> None:
        src = self.store.source_path or Path("-")
        self.info_label.setText(
            f"元ファイル: {src}\n"
            f"全件: {len(self.store.features):,}\n"
            f"抽出: {len(self.filtered):,}\n"
            f"フィールド数: {len(self.store.fields)}\n"
            f"{self.store.crs_note}"
        )

    def geometry_is_line(self, geom: dict[str, Any]) -> bool:
        if not geom:
            return False
        if geom.get("type") in ("LineString", "MultiLineString"):
            return True
        if geom.get("type") == "GeometryCollection":
            return any(self.geometry_is_line(g) for g in geom.get("geometries", []) or [])
        return False

    def apply_filter(self) -> None:
        try:
            features = self.store.features
            if self.line_only_check.isChecked():
                features = [f for f in features if self.geometry_is_line(f.get("geometry") or {})]
            self.filtered = filter_features(
                features,
                keywords_text=self.keyword_edit.text(),
                fields_text=self.field_edit.text(),
                regex=self.regex_check.isChecked(),
                match_all=self.and_radio.isChecked(),
                exclude_text=self.exclude_edit.text(),
            )
            self.log_msg(f"抽出: {len(self.filtered):,} / {len(self.store.features):,} features")
            self.update_info_label()
            self._refresh_map(self.filtered)
            self._refresh_table(self.filtered)
        except Exception as exc:
            QMessageBox.critical(self, "フィルターエラー", f"{exc}\n\n{traceback.format_exc()}")

    def _refresh_map(self, features: list[dict[str, Any]]) -> None:
        title = "抽出結果" if features else "No data"
        if self.store.source_path:
            title = self.store.source_path.name
        self.map_widget.set_geojson({"type": "FeatureCollection", "features": features}, title=title)

    def _refresh_table(self, features: list[dict[str, Any]]) -> None:
        fields = collect_fields(features) if features else self.store.fields
        if not fields:
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return
        display_fields = fields[:80]
        rows = min(len(features), MAX_TABLE_ROWS)
        self.table.clear()
        self.table.setColumnCount(len(display_fields))
        self.table.setHorizontalHeaderLabels(display_fields)
        self.table.setRowCount(rows)
        for r, feat in enumerate(features[:rows]):
            props = feat.get("properties") or {}
            for c, field_name in enumerate(display_fields):
                item = QTableWidgetItem(safe_str(props.get(field_name, "")))
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()

    def export_geojson(self) -> None:
        if not self.filtered:
            QMessageBox.information(self, "出力", "出力対象がありません。")
            return
        default = self.last_output_dir / "filtered_lines.geojson"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "GeoJSONを保存",
            str(default),
            "GeoJSON (*.geojson);;JSON (*.json);;All files (*.*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            write_json(path, {"type": "FeatureCollection", "features": self.filtered}, pretty=True)
            self.last_output_dir = path.parent
            self.log_msg(f"GeoJSON保存: {path}")
            QMessageBox.information(self, "保存完了", f"GeoJSONを保存しました。\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "保存エラー", str(exc))

    def export_turnout_json(self) -> None:
        if not self.filtered:
            QMessageBox.information(self, "出力", "出力対象がありません。")
            return
        line_features = [f for f in self.filtered if self.geometry_is_line(f.get("geometry") or {})]
        if not line_features:
            QMessageBox.information(self, "出力", "線データがありません。Turnout用JSONはLineString/MultiLineStringが必要です。")
            return
        default = self.last_output_dir / "turnout_tracks.json"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Turnout用JSONを保存",
            str(default),
            "JSON (*.json);;All files (*.*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            data = geojson_to_overpass(line_features)
            write_json(path, data, pretty=False)
            self.last_output_dir = path.parent
            elements = data.get("elements", [])
            ways = sum(1 for e in elements if e.get("type") == "way")
            nodes = sum(1 for e in elements if e.get("type") == "node")
            self.log_msg(f"Turnout用JSON保存: {path} ({nodes} nodes, {ways} ways)")
            QMessageBox.information(self, "保存完了", f"Turnout用JSONを保存しました。\n{path}\n{nodes} nodes, {ways} ways")
        except Exception as exc:
            QMessageBox.critical(self, "保存エラー", str(exc))

    def export_nrclip(self) -> None:
        if not self.filtered:
            QMessageBox.information(self, "出力", "出力対象がありません。")
            return
        line_features = [f for f in self.filtered if self.geometry_is_line(f.get("geometry") or {})]
        if not line_features:
            QMessageBox.information(self, "出力", "線データがありません。nrclip出力にはLineString/MultiLineStringが必要です。")
            return
        default = self.last_output_dir / "tracks.nrclip"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "nrclipを保存",
            str(default),
            "NIMBY Rails Clipboard (*.nrclip);;All files (*.*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            name = path.stem
            data = geojson_to_nrclip_bytes(line_features, name)
            path.write_bytes(data)
            self.last_output_dir = path.parent
            self.log_msg(f"nrclip保存: {path} ({len(line_features)} features)")
            QMessageBox.information(self, "保存完了", f"nrclipを保存しました。\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "保存エラー", f"{exc}\n\n{traceback.format_exc()}")

    def open_preview_in_browser(self) -> None:
        import webbrowser
        if self.map_widget.last_html_path and self.map_widget.last_html_path.exists():
            webbrowser.open(self.map_widget.last_html_path.as_uri())
        else:
            QMessageBox.information(self, "プレビュー", "まだプレビューHTMLがありません。")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.store.cleanup()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    win = MainWindow()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if path.exists():
            win.load_path(path)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
