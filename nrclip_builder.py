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
    from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
    HAS_WEBENGINE = True
except Exception:
    QWebEngineView = None
    QWebEngineSettings = None
    QWebEnginePage = None
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
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>
  html, body, #map {{ height: 100%; margin: 0; }}
  .info {{ background: white; padding: 8px 10px; border-radius: 4px; box-shadow: 0 1px 5px rgba(0,0,0,.35); font: 13px/1.4 sans-serif; }}
  .history-label {{
    background-color: rgba(255, 255, 255, 0.85);
    border: 1px solid #3388ff;
    border-radius: 3px;
    padding: 1px 3px;
    font-size: 10px;
    font-weight: bold;
    color: #3388ff;
    box-shadow: 0 1px 3px rgba(0,0,0,0.2);
  }}
</style>
</head>
<body>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
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

// History layer group and controls
const historyLayer = L.layerGroup().addTo(map);
L.control.layers(
  {{'国土地理院地図': gsiStd, 'OpenStreetMap': osm}},
  {{'過去の出力履歴': historyLayer}},
  {{collapsed: false}}
).addTo(map);

function propHtml(props) {{
  const keys = Object.keys(props || {{}}).slice(0, 30);
  if (!keys.length) return '(属性なし)';
  return '<table>' + keys.map(k => '<tr><th style="text-align:left;padding-right:8px">' + escapeHtml(k) + '</th><td>' + escapeHtml(String(props[k] ?? '')) + '</td></tr>').join('') + '</table>';
}}
function escapeHtml(s) {{ return s.replace(/[&<>"']/g, m => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m])); }}

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

// --- Active Selection Bounding Box Logic ---
let selectMode = false;
let activeRect = null;
let dragStartLatLng = null;
let activeHandles = [];

const SelectControl = L.Control.extend({{
  options: {{ position: 'topleft' }},
  onAdd: function(map) {{
    const btn = L.DomUtil.create('button', 'leaflet-bar');
    btn.innerHTML = '範囲選択';
    btn.style.backgroundColor = 'white';
    btn.style.border = '2px solid rgba(0,0,0,0.2)';
    btn.style.borderRadius = '4px';
    btn.style.padding = '6px 10px';
    btn.style.cursor = 'pointer';
    btn.style.fontWeight = 'bold';
    
    L.DomEvent.on(btn, 'click', function(e) {{
      L.DomEvent.stopPropagation(e);
      selectMode = !selectMode;
      if (selectMode) {{
        btn.style.backgroundColor = '#ffc9c9';
        btn.innerHTML = '範囲選択中 (ドラッグして囲む)';
        map.dragging.disable();
      }} else {{
        btn.style.backgroundColor = 'white';
        btn.innerHTML = '範囲選択';
        map.dragging.enable();
      }}
    }});
    return btn;
  }}
}});
new SelectControl().addTo(map);

function clearHandles() {{
  activeHandles.forEach(h => map.removeLayer(h.marker));
  activeHandles = [];
}}

function createHandles() {{
  clearHandles();
  if (!activeRect) return;

  const bounds = activeRect.getBounds();
  const corners = {{
    nw: bounds.getNorthWest(),
    ne: bounds.getNorthEast(),
    sw: bounds.getSouthWest(),
    se: bounds.getSouthEast()
  }};

  const handleIcon = L.divIcon({{
    className: 'bbox-handle-icon',
    html: '<div style="width:10px;height:10px;background-color:#ff3333;border:1px solid white;border-radius:50%;cursor:move;box-shadow:0 1px 3px rgba(0,0,0,0.4)"></div>',
    iconSize: [10, 10],
    iconAnchor: [5, 5]
  }});

  for (let key in corners) {{
    const marker = L.marker(corners[key], {{
      draggable: true,
      icon: handleIcon
    }}).addTo(map);

    marker.on('drag', function(e) {{
      updateBoundsFromHandle(key, marker.getLatLng());
    }});

    marker.on('dragend', function() {{
      notifyBounds();
    }});

    activeHandles.push({{ key: key, marker: marker }});
  }}
}}

function updateBoundsFromHandle(key, latlng) {{
  if (!activeRect) return;
  const bounds = activeRect.getBounds();
  let west = bounds.getWest();
  let south = bounds.getSouth();
  let east = bounds.getEast();
  let north = bounds.getNorth();

  if (key === 'nw') {{
    west = latlng.lng;
    north = latlng.lat;
  }} else if (key === 'ne') {{
    east = latlng.lng;
    north = latlng.lat;
  }} else if (key === 'sw') {{
    west = latlng.lng;
    south = latlng.lat;
  }} else if (key === 'se') {{
    east = latlng.lng;
    south = latlng.lat;
  }}

  const newBounds = L.latLngBounds([south, west], [north, east]);
  activeRect.setBounds(newBounds);

  const newCorners = {{
    nw: newBounds.getNorthWest(),
    ne: newBounds.getNorthEast(),
    sw: newBounds.getSouthWest(),
    se: newBounds.getSouthEast()
  }};

  activeHandles.forEach(h => {{
    if (h.key !== key) {{
      h.marker.setLatLng(newCorners[h.key]);
    }}
  }});
}}

function notifyBounds() {{
  if (!activeRect) return;
  const bounds = activeRect.getBounds();
  const west = bounds.getWest();
  const south = bounds.getSouth();
  const east = bounds.getEast();
  const north = bounds.getNorth();
  document.title = "BBOX:" + west + "," + south + "," + east + "," + north;
}}

map.on('mousedown', function(e) {{
  if (!selectMode) return;
  dragStartLatLng = e.latlng;
  if (activeRect) {{
    map.removeLayer(activeRect);
  }}
  clearHandles();
  activeRect = L.rectangle([dragStartLatLng, dragStartLatLng], {{
    color: '#ff3333',
    weight: 2,
    fillOpacity: 0.1
  }}).addTo(map);
}});

map.on('mousemove', function(e) {{
  if (!selectMode || !dragStartLatLng || !activeRect) return;
  const bounds = L.latLngBounds(dragStartLatLng, e.latlng);
  activeRect.setBounds(bounds);
}});

map.on('mouseup', function(e) {{
  if (!selectMode || !dragStartLatLng || !activeRect) return;
  const bounds = activeRect.getBounds();
  const west = bounds.getWest();
  const south = bounds.getSouth();
  const east = bounds.getEast();
  const north = bounds.getNorth();
  
  createHandles();
  notifyBounds();
  
  dragStartLatLng = null;
  selectMode = false;
  
  const btns = document.getElementsByTagName('button');
  for (let btn of btns) {{
    if (btn.classList.contains('leaflet-bar')) {{
      btn.style.backgroundColor = 'white';
      btn.innerHTML = '範囲選択';
    }}
  }}
  map.dragging.enable();
}});

// --- Python-callable APIs ---
window.setActiveBounds = function(west, south, east, north) {{
  if (activeRect) {{
    map.removeLayer(activeRect);
  }}
  const bounds = L.latLngBounds([south, west], [north, east]);
  activeRect = L.rectangle(bounds, {{
    color: '#ff3333',
    weight: 2,
    fillOpacity: 0.1
  }}).addTo(map);
  createHandles();
}};

window.clearActiveBounds = function() {{
  if (activeRect) {{
    map.removeLayer(activeRect);
    activeRect = null;
  }}
  clearHandles();
}};

window.addHistoryBounds = function(west, south, east, north, name) {{
  const bounds = L.latLngBounds([south, west], [north, east]);
  const rect = L.rectangle(bounds, {{
    color: '#3388ff',
    weight: 2,
    fillOpacity: 0.05,
    interactive: true
  }}).addTo(historyLayer);

  rect.bindTooltip(name, {{
    permanent: true,
    direction: 'top',
    className: 'history-label'
  }});

  rect.on('click', function(e) {{
    L.DomEvent.stopPropagation(e);
    document.title = "SELECT_HISTORY:" + name + "," + west + "," + south + "," + east + "," + north;
  }});
}};

window.clearHistoryBounds = function() {{
  historyLayer.clearLayers();
}};
</script>
</body>
</html>"""


class CustomWebPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level: int, message: str, lineNumber: int, sourceID: str) -> None:
        print(f"JS Console message: {message} at line {lineNumber} (source: {sourceID})")


class MapWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if HAS_WEBENGINE:
            self.web = QWebEngineView()
            self.page = CustomWebPage(self.web)
            self.web.setPage(self.page)
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

        # BBox 関連の初期化とシグナル接続
        self.selected_bbox = None
        self.history_file = Path(__file__).parent / "bbox_history.json"
        self.history_data = []

        if HAS_WEBENGINE:
            self.map_widget.web.titleChanged.connect(self.on_title_changed)
            self.map_widget.web.loadFinished.connect(self.on_map_load_finished)
        self.clear_bbox_btn.clicked.connect(self.clear_bbox)
        self.use_bbox_check.stateChanged.connect(self.on_use_bbox_changed)

        self.min_lon_edit.editingFinished.connect(self.on_coordinate_edited)
        self.min_lat_edit.editingFinished.connect(self.on_coordinate_edited)
        self.max_lon_edit.editingFinished.connect(self.on_coordinate_edited)
        self.max_lat_edit.editingFinished.connect(self.on_coordinate_edited)

        # 履歴テーブルの初期設定
        self.history_table.setColumnCount(6)
        self.history_table.setHorizontalHeaderLabels(["名前", "最小経度", "最小緯度", "最大経度", "最大緯度", "出力日時"])
        self.history_table.itemSelectionChanged.connect(self.on_history_selected)
        self.delete_history_btn.clicked.connect(self.delete_history)

        self.load_history_from_file()

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

    def feature_intersects_bbox(self, feat: dict, bbox: tuple[float, float, float, float]) -> bool:
        min_lon, min_lat, max_lon, max_lat = bbox
        geom = feat.get("geometry") or {}
        for line in iter_lines_from_geometry(geom):
            for pt in line:
                lon, lat = pt[0], pt[1]
                if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat:
                    return True
        return False

    def on_use_bbox_changed(self, state: int) -> None:
        if self.use_bbox_check.isChecked():
            self.on_coordinate_edited()
        else:
            if HAS_WEBENGINE:
                self.map_widget.web.page().runJavaScript("window.clearActiveBounds();")

    def on_map_load_finished(self, ok: bool) -> None:
        if ok:
            self.update_map_history_bboxes()
            self.update_map_active_bbox()

    def on_coordinate_edited(self) -> None:
        try:
            w = float(self.min_lon_edit.text())
            s = float(self.min_lat_edit.text())
            e = float(self.max_lon_edit.text())
            n = float(self.max_lat_edit.text())
            self.selected_bbox = (w, s, e, n)
            self.use_bbox_check.setChecked(True)
            self.update_map_active_bbox()
        except ValueError:
            pass

    def update_map_active_bbox(self) -> None:
        if self.selected_bbox:
            w, s, e, n = self.selected_bbox
            if HAS_WEBENGINE:
                self.map_widget.web.page().runJavaScript(f"window.setActiveBounds({w}, {s}, {e}, {n});")
        else:
            if HAS_WEBENGINE:
                self.map_widget.web.page().runJavaScript("window.clearActiveBounds();")

    def update_map_history_bboxes(self) -> None:
        if not HAS_WEBENGINE:
            return
        page = self.map_widget.web.page()
        page.runJavaScript("window.clearHistoryBounds();")
        for item in self.history_data:
            bbox = item.get("bbox")
            name = item.get("name", "")
            if bbox and len(bbox) == 4:
                w, s, e, n = bbox
                name_esc = name.replace("'", "\\'")
                page.runJavaScript(f"window.addHistoryBounds({w}, {s}, {e}, {n}, '{name_esc}');")

    def on_title_changed(self, title: str) -> None:
        if title.startswith("BBOX:"):
            parts = title[5:].split(",")
            if len(parts) == 4:
                try:
                    w, s, e, n = map(float, parts)
                    self.selected_bbox = (w, s, e, n)
                    self.min_lon_edit.setText(f"{w:.7f}")
                    self.min_lat_edit.setText(f"{s:.7f}")
                    self.max_lon_edit.setText(f"{e:.7f}")
                    self.max_lat_edit.setText(f"{n:.7f}")
                    self.use_bbox_check.setChecked(True)
                except ValueError:
                    pass
        elif title.startswith("SELECT_HISTORY:"):
            parts = title[15:].split(",")
            if len(parts) == 5:
                name = parts[0]
                try:
                    w, s, e, n = map(float, parts[1:])
                    self.selected_bbox = (w, s, e, n)
                    self.min_lon_edit.setText(f"{w:.7f}")
                    self.min_lat_edit.setText(f"{s:.7f}")
                    self.max_lon_edit.setText(f"{e:.7f}")
                    self.max_lat_edit.setText(f"{n:.7f}")
                    self.use_bbox_check.setChecked(True)
                    self.update_map_active_bbox()
                    self.select_history_row_by_name(name)
                except ValueError:
                    pass

    def select_history_row_by_name(self, name: str) -> None:
        for r in range(self.history_table.rowCount()):
            item = self.history_table.item(r, 0)
            if item and item.text() == name:
                self.history_table.selectRow(r)
                break

    def clear_bbox(self) -> None:
        self.selected_bbox = None
        self.min_lon_edit.clear()
        self.min_lat_edit.clear()
        self.max_lon_edit.clear()
        self.max_lat_edit.clear()
        self.use_bbox_check.setChecked(False)
        if HAS_WEBENGINE:
            self.map_widget.web.page().runJavaScript("window.clearActiveBounds();")

    def load_history_from_file(self) -> None:
        self.history_data = []
        if self.history_file.exists():
            try:
                self.history_data = json.loads(self.history_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        self.refresh_history_table()

    def save_history_to_file(self) -> None:
        try:
            self.history_file.write_text(json.dumps(self.history_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def refresh_history_table(self) -> None:
        self.history_table.setRowCount(0)
        for item in self.history_data:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            bbox = item.get("bbox", [0, 0, 0, 0])
            w, s, e, n = bbox
            self.history_table.setItem(row, 0, QTableWidgetItem(item.get("name", "")))
            self.history_table.setItem(row, 1, QTableWidgetItem(f"{w:.7f}"))
            self.history_table.setItem(row, 2, QTableWidgetItem(f"{s:.7f}"))
            self.history_table.setItem(row, 3, QTableWidgetItem(f"{e:.7f}"))
            self.history_table.setItem(row, 4, QTableWidgetItem(f"{n:.7f}"))
            self.history_table.setItem(row, 5, QTableWidgetItem(item.get("timestamp", "")))
        self.history_table.resizeColumnsToContents()

    def on_history_selected(self) -> None:
        selected = self.history_table.selectedRanges()
        if not selected:
            return
        row = selected[0].topRow()
        if 0 <= row < len(self.history_data):
            item = self.history_data[row]
            bbox = item.get("bbox")
            if bbox and len(bbox) == 4:
                w, s, e, n = bbox
                self.selected_bbox = (w, s, e, n)
                self.min_lon_edit.setText(f"{w:.7f}")
                self.min_lat_edit.setText(f"{s:.7f}")
                self.max_lon_edit.setText(f"{e:.7f}")
                self.max_lat_edit.setText(f"{n:.7f}")
                self.use_bbox_check.setChecked(True)
                self.update_map_active_bbox()

    def delete_history(self) -> None:
        selected = self.history_table.selectedRanges()
        if not selected:
            QMessageBox.information(self, "削除", "削除する履歴を選択してください。")
            return
        row = selected[0].topRow()
        if 0 <= row < len(self.history_data):
            deleted = self.history_data.pop(row)
            self.save_history_to_file()
            self.refresh_history_table()
            self.update_map_history_bboxes()
            self.log_msg(f"履歴を削除しました: {deleted.get('name')}")
            if self.selected_bbox == tuple(deleted.get("bbox", [])):
                self.clear_bbox()

    def check_bbox_required(self) -> bool:
        if not self.use_bbox_check.isChecked() or not self.selected_bbox:
            QMessageBox.warning(
                self,
                "警告",
                "出力範囲が設定されていないか、有効になっていません。\n"
                "地図上の「範囲選択」ボタンを押してドラッグで囲むか、"
                "数値を入力して「出力範囲を使用」にチェックを入れてください。"
            )
            return False
        return True

    def add_to_history(self, name: str, bbox: tuple[float, float, float, float]) -> None:
        import datetime
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 重複削除
        self.history_data = [item for item in self.history_data if item.get("name") != name and tuple(item.get("bbox", [])) != bbox]
        self.history_data.insert(0, {
            "name": name,
            "bbox": list(bbox),
            "timestamp": now
        })
        self.save_history_to_file()
        self.refresh_history_table()
        self.update_map_history_bboxes()

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
        if not self.check_bbox_required():
            return
        if not self.filtered:
            QMessageBox.information(self, "出力", "出力対象がありません。")
            return
        export_features = [f for f in self.filtered if self.feature_intersects_bbox(f, self.selected_bbox)]
        if not export_features:
            QMessageBox.information(self, "出力", "選択された出力範囲内にデータがありません。")
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
            write_json(path, {"type": "FeatureCollection", "features": export_features}, pretty=True)
            self.last_output_dir = path.parent
            self.add_to_history(path.stem, self.selected_bbox)
            self.log_msg(f"GeoJSON保存: {path} ({len(export_features)} features)")
            QMessageBox.information(self, "保存完了", f"GeoJSONを保存しました。\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "保存エラー", str(exc))

    def export_turnout_json(self) -> None:
        if not self.check_bbox_required():
            return
        if not self.filtered:
            QMessageBox.information(self, "出力", "出力対象がありません。")
            return
        export_features = [f for f in self.filtered if self.feature_intersects_bbox(f, self.selected_bbox)]
        line_features = [f for f in export_features if self.geometry_is_line(f.get("geometry") or {})]
        if not line_features:
            QMessageBox.information(self, "出力", "選択された出力範囲内に線データがありません。")
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
            self.add_to_history(path.stem, self.selected_bbox)
            elements = data.get("elements", [])
            ways = sum(1 for e in elements if e.get("type") == "way")
            nodes = sum(1 for e in elements if e.get("type") == "node")
            self.log_msg(f"Turnout用JSON保存: {path} ({nodes} nodes, {ways} ways)")
            QMessageBox.information(self, "保存完了", f"Turnout用JSONを保存しました。\n{path}\n{nodes} nodes, {ways} ways")
        except Exception as exc:
            QMessageBox.critical(self, "保存エラー", str(exc))

    def export_nrclip(self) -> None:
        if not self.check_bbox_required():
            return
        if not self.filtered:
            QMessageBox.information(self, "出力", "出力対象がありません。")
            return
        export_features = [f for f in self.filtered if self.feature_intersects_bbox(f, self.selected_bbox)]
        line_features = [f for f in export_features if self.geometry_is_line(f.get("geometry") or {})]
        if not line_features:
            QMessageBox.information(self, "出力", "選択された出力範囲内に線データがありません。")
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
            self.add_to_history(name, self.selected_bbox)
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
