import html
import json
from typing import Any

APP_TITLE = "NRClipBuilder"


def make_leaflet_html(
    geojson: dict[str, Any],
    title: str = APP_TITLE,
    lang: str = "ja",
    translation: dict[str, Any] = None,
    tile_configs: list[dict[str, str]] = None,
    layers_svg_base64: str = "",
    initial_view: dict[str, Any] | None = None,
) -> str:
    if tile_configs is None:
        tile_configs = [{
            "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            "attribution": "&copy; OpenStreetMap contributors"
        }]
    tile_configs_json = json.dumps(tile_configs, ensure_ascii=False)
    initial_view_json = json.dumps(initial_view, ensure_ascii=False) if initial_view else "null"
    gj = json.dumps(geojson, ensure_ascii=False, separators=(",", ":"))
    feature_count = len(geojson.get("features", []))
    
    t = translation or {}
    gsi_attr = t.get("map_layer_gsi", "GSI Map" if lang == "en" else "国土地理院地図")
    gsi_layer_name = t.get("map_layer_gsi", "GSI Map" if lang == "en" else "国土地理院地図")
    osm_layer_name = t.get("map_layer_osm", "OpenStreetMap")
    history_layer_name = t.get("map_layer_history", "History Bounds" if lang == "en" else "過去の出力履歴")
    osm_abandoned_layer_name = t.get("map_layer_osm_abandoned", "OSM廃線データ" if lang == "ja" else "OSM Abandoned Rails")
    no_attr_text = t.get("map_no_attr", "(No attributes)" if lang == "en" else "(属性なし)")
    select_btn_text = t.get("map_select_btn", "Select BBox" if lang == "en" else "範囲選択")
    selecting_btn_text = t.get("map_select_btn_active", "Selecting BBox (Drag on map)" if lang == "en" else "範囲選択中 (ドラッグして囲む)")
    fetch_osm_text = t.get("map_fetch_osm_btn", "Fetch OSM" if lang == "en" else "OSM取得")

    return f"""<!doctype html>
<html lang="{lang}">
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
    border: 1px solid #f08c00;
    border-radius: 3px;
    padding: 1px 3px;
    font-size: 10px;
    font-weight: bold;
    color: #c46a00;
    box-shadow: 0 1px 3px rgba(0,0,0,0.2);
  }}
  .bbox-handle-icon.nw, .bbox-handle-icon.se, .bbox-handle-icon.nw div, .bbox-handle-icon.se div {{
    cursor: nwse-resize !important;
  }}
  .bbox-handle-icon.ne, .bbox-handle-icon.sw, .bbox-handle-icon.ne div, .bbox-handle-icon.sw div {{
    cursor: nesw-resize !important;
  }}
  .history-rect {{
    pointer-events: fill !important;
    cursor: pointer;
  }}
  .leaflet-control-layers-toggle {{
    background-image: url("data:image/svg+xml;base64,{layers_svg_base64}") !important;
    background-size: 20px 20px;
    background-position: center;
  }}
  .bbox-fetch-osm-btn {{
    background: #228be6;
    border: 2px solid #fff;
    border-radius: 4px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.35);
    color: #fff;
    cursor: pointer;
    font: 12px/1.2 sans-serif;
    font-weight: 700;
    padding: 5px 8px;
    white-space: nowrap;
  }}
  .bbox-fetch-osm-btn:hover {{
    background: #1c7ed6;
  }}
  .draw-line-preview {{
    stroke-dasharray: 6 6;
  }}
</style>
</head>
<body>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const data = {gj};
const lang = '{lang}';
const initialView = {initial_view_json};
const map = L.map('map', {{ zoomControl: true, preferCanvas: true }});
const pointRenderer = L.canvas({{ padding: 0.5 }});
const tileConfigs = {tile_configs_json};
tileConfigs.forEach(cfg => {{
  if (cfg.url) {{
    L.tileLayer(cfg.url, {{
      attribution: cfg.attribution || '', maxZoom: 19
    }}).addTo(map);
  }}
}});

// History layer group (controls will be added dynamically at the bottom left)
const historyLayer = L.layerGroup().addTo(map);

function propHtml(props) {{
  const keys = Object.keys(props || {{}}).slice(0, 30);
  if (!keys.length) return '{no_attr_text}';
  return '<table>' + keys.map(k => '<tr><th style="text-align:left;padding-right:8px">' + escapeHtml(k) + '</th><td>' + escapeHtml(String(props[k] ?? '')) + '</td></tr>').join('') + '</table>';
}}
function escapeHtml(s) {{ return s.replace(/[&<>"']/g, m => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m])); }}

// Dynamically create layer groups based on the "_source" (or "source") property of each feature
const layers = {{}};
const layerNames = {{}};
const groupedFeatures = {{}};
const sourceOrder = [];
const OSM_SOURCE = "osm";
const HISTORY_SOURCE = "過去の出力履歴";
const osmLayerName = '{osm_abandoned_layer_name}';

function isHistoryFeature(feat) {{
  const props = (feat && feat.properties) || {{}};
  return props._history_output === true || props._history_output === "true" || props._source === HISTORY_SOURCE;
}}
function featureColor(feat, src) {{
  if (isHistoryFeature(feat)) return '#ffd43b';
  if (src === OSM_SOURCE) return '#4c6ef5';
  return '#d6336c';
}}
function featureWeight(feat, src) {{
  if (isHistoryFeature(feat)) return 6;
  if (src === OSM_SOURCE) return 4;
  return 5;
}}

data.features.forEach(function(f) {{
  var src = (f.properties && (f.properties._source || f.properties.source)) || "Imported Data";
  if (!groupedFeatures[src]) {{
    groupedFeatures[src] = [];
    sourceOrder.push(src);
  }}
  groupedFeatures[src].push(f);
}});

sourceOrder.forEach(function(src) {{
  const srcFeatures = groupedFeatures[src];
  const manyFeatures = srcFeatures.length > 5000;
  layers[src] = L.geoJSON({{ type: "FeatureCollection", features: srcFeatures }}, {{
    style: function(feat) {{
      return {{ color: featureColor(feat, src), weight: featureWeight(feat, src), opacity: 0.9, renderer: pointRenderer }};
    }},
    pointToLayer: function(feat, latlng) {{
      const color = featureColor(feat, src);
      return L.circleMarker(latlng, {{
        renderer: pointRenderer,
        radius: manyFeatures ? 3 : 5,
        color: color,
        fillColor: color,
        weight: manyFeatures ? 1 : 2,
        fillOpacity: 0.8
      }});
    }},
    onEachFeature: function(feat, lyr) {{
      lyr.bindPopup(function(layer) {{
        return propHtml(((layer.feature || feat).properties) || {{}});
      }});
    }}
  }}).addTo(map);

  if (src === OSM_SOURCE) {{
    layerNames[src] = osmLayerName;
  }} else {{
    layerNames[src] = src;
  }}
}});

let bounds = L.latLngBounds();
Object.keys(layers).forEach(function(src) {{
  const b = layers[src].getBounds();
  if (b.isValid()) bounds.extend(b);
}});
if (initialView && Number.isFinite(initialView.lat) && Number.isFinite(initialView.lng) && Number.isFinite(initialView.zoom)) {{
  map.setView([initialView.lat, initialView.lng], initialView.zoom);
}} else {{
  if (bounds.isValid()) {{
    map.fitBounds(bounds.pad(0.08));
  }} else {{
    map.setView([44.8, 142.5], 9);
  }}
}}

function emitCurrentView() {{
  const center = map.getCenter();
  document.title = "VIEW:" + center.lat.toFixed(7) + "," + center.lng.toFixed(7) + "," + map.getZoom();
}}
map.on('moveend zoomend', emitCurrentView);
setTimeout(emitCurrentView, 0);



// --- Active Selection Bounding Box Logic ---
let selectMode = false;
let activeRect = null;
let dragStartLatLng = null;
let activeHandles = [];
let fetchOsmMarker = null;

const SelectControl = L.Control.extend({{
  options: {{ position: 'topleft' }},
  onAdd: function(map) {{
    const btn = L.DomUtil.create('button', 'leaflet-bar');
    btn.id = 'select-bbox-btn';
    btn.innerHTML = '{select_btn_text}';
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
        btn.innerHTML = '{selecting_btn_text}';
        map.dragging.disable();
      }} else {{
        btn.style.backgroundColor = 'white';
        btn.innerHTML = '{select_btn_text}';
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
  if (fetchOsmMarker) {{
    map.removeLayer(fetchOsmMarker);
    fetchOsmMarker = null;
  }}
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

  Object.keys(corners).forEach(pos => {{
    const handleIcon = L.divIcon({{
      className: 'bbox-handle-icon ' + pos,
      html: '<div style="width:10px;height:10px;background:#3388ff;border:2px solid white;border-radius:50%;margin:-4px 0 0 -4px;"></div>',
      iconSize: [10, 10]
    }});
    const marker = L.marker(corners[pos], {{ icon: handleIcon, draggable: true }}).addTo(map);
    activeHandles.push({{ pos: pos, marker: marker }});

    marker.on('drag', function(e) {{
      const b = activeRect.getBounds();
      const currentLatlng = marker.getLatLng();
      let newW = b.getWest();
      let newE = b.getEast();
      let newS = b.getSouth();
      let newN = b.getNorth();

      if (pos.includes('w')) newW = currentLatlng.lng;
      if (pos.includes('e')) newE = currentLatlng.lng;
      if (pos.includes('s')) newS = currentLatlng.lat;
      if (pos.includes('n')) newN = currentLatlng.lat;

      // Prevent inversion
      if (newW > newE) {{ const t = newW; newW = newE; newE = t; }}
      if (newS > newN) {{ const t = newS; newS = newN; newN = t; }}

      const newBounds = L.latLngBounds([[newS, newW], [newN, newE]]);
      activeRect.setBounds(newBounds);
      
      // Sync other handles during drag
      syncHandlesExcept(pos);
      updateFetchOsmButton();
      updateAppTitle(newBounds);
    }});

    marker.on('dragend', function() {{
      createHandles();
    }});
  }});
  updateFetchOsmButton();
}}

function updateFetchOsmButton() {{
  if (!activeRect) return;
  const pos = activeRect.getBounds().getNorthEast();
  if (!fetchOsmMarker) {{
    const fetchIcon = L.divIcon({{
      className: 'bbox-fetch-osm-icon',
      html: '<button type="button" class="bbox-fetch-osm-btn">{fetch_osm_text}</button>',
      iconSize: [80, 28],
      iconAnchor: [-8, 28]
    }});
    fetchOsmMarker = L.marker(pos, {{ icon: fetchIcon, interactive: true }}).addTo(map);
    fetchOsmMarker.on('click', function(e) {{
      L.DomEvent.stopPropagation(e);
      document.title = "FETCH_OSM";
    }});
  }} else {{
    fetchOsmMarker.setLatLng(pos);
  }}
}}

function syncHandlesExcept(draggingPos) {{
  if (!activeRect) return;
  const b = activeRect.getBounds();
  const newCorners = {{
    nw: b.getNorthWest(),
    ne: b.getNorthEast(),
    sw: b.getSouthWest(),
    se: b.getSouthEast()
  }};
  activeHandles.forEach(h => {{
    if (h.pos !== draggingPos) {{
      h.marker.setLatLng(newCorners[h.pos]);
    }}
  }});
}}

function updateAppTitle(bounds) {{
  const w = bounds.getWest().toFixed(7);
  const s = bounds.getSouth().toFixed(7);
  const e = bounds.getEast().toFixed(7);
  const n = bounds.getNorth().toFixed(7);
  document.title = "BBOX:" + w + "," + s + "," + e + "," + n;
}}

map.on('mousedown', function(e) {{
  if (!selectMode) return;
  dragStartLatLng = e.latlng;
  if (activeRect) {{
    map.removeLayer(activeRect);
    activeRect = null;
    clearHandles();
  }}
  activeRect = L.rectangle([dragStartLatLng, dragStartLatLng], {{ color: "#3388ff", weight: 2, fillOpacity: 0.15 }}).addTo(map);
}});

map.on('mousemove', function(e) {{
  if (!selectMode || !dragStartLatLng || !activeRect) return;
  const bounds = L.latLngBounds(dragStartLatLng, e.latlng);
  activeRect.setBounds(bounds);
}});

map.on('mouseup', function(e) {{
  if (!selectMode || !dragStartLatLng || !activeRect) return;
  const bounds = L.latLngBounds(dragStartLatLng, e.latlng);
  activeRect.setBounds(bounds);
  
  dragStartLatLng = null;
  selectMode = false;
  
  // Reset UI
  const btn = document.getElementById('select-bbox-btn');
  if (btn) {{
    btn.style.backgroundColor = 'white';
    btn.innerHTML = '{select_btn_text}';
  }}
  map.dragging.enable();
  createHandles();
  updateAppTitle(bounds);
}});

window.setActiveBounds = function(w, s, e, n, fitMap) {{
  if (activeRect) {{
    map.removeLayer(activeRect);
    activeRect = null;
    clearHandles();
  }}
  const bounds = L.latLngBounds([[s, w], [n, e]]);
  activeRect = L.rectangle(bounds, {{ color: "#3388ff", weight: 2, fillOpacity: 0.15 }}).addTo(map);
  createHandles();
  if (fitMap === true) {{
    map.fitBounds(bounds.pad(0.15));
  }}
}};

window.clearActiveBounds = function() {{
  if (activeRect) {{
    map.removeLayer(activeRect);
    activeRect = null;
    clearHandles();
  }}
}};

window.addHistoryBounds = function(w, s, e, n, name) {{
  const bounds = L.latLngBounds([[s, w], [n, e]]);
  const rect = L.rectangle(bounds, {{
    color: "#ffd43b",
    weight: 2,
    fillOpacity: 0.08,
    dashArray: "4, 4",
    className: "history-rect"
  }}).addTo(historyLayer);
  
  rect.bindTooltip(escapeHtml(name), {{
    permanent: true,
    direction: 'top',
    className: 'history-label',
    interactive: true
  }});

  rect.on('click', function(e) {{
    L.DomEvent.stopPropagation(e);
    document.title = "SELECT_HISTORY:" + name + "," + w + "," + s + "," + e + "," + n;
  }});
}};

window.clearHistoryBounds = function() {{
  historyLayer.clearLayers();
}};

// --- Manual Line Drawing Logic ---
window.setDrawLineMode = function(enabled) {{
  // ロジック削除のため何もしない
}};
</script>
</body>
</html>"""
