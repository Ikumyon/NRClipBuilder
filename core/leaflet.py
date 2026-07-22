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
    layer_opacities: dict[str, float] | None = None,
) -> str:
    if tile_configs is None:
        tile_configs = [{
            "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            "attribution": "&copy; OpenStreetMap contributors"
        }]
    tile_configs_json = json.dumps(tile_configs, ensure_ascii=False)
    if layer_opacities is None:
        layer_opacities = {}
    layer_opacities_json = json.dumps(layer_opacities, ensure_ascii=False)
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
    elevation_text = t.get("map_elevation", "標高" if lang == "ja" else "Elevation")
    elevation_no_data_text = t.get("map_elevation_no_data", "データ外" if lang == "ja" else "Out of range")
    elevation_error_text = t.get("map_elevation_error", "取得エラー" if lang == "ja" else "Error")
    distance_text = t.get("map_distance", "距離" if lang == "ja" else "Distance")
    slope_text = t.get("map_slope", "勾配" if lang == "ja" else "Gradient")

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
  .elevation-tooltip {{
    background-color: rgba(33, 37, 41, 0.9) !important;
    border: 1px solid #495057 !important;
    border-radius: 4px !important;
    color: #f8f9fa !important;
    font-size: 11px !important;
    font-weight: bold !important;
    padding: 4px 8px !important;
    box-shadow: 0 2px 5px rgba(0,0,0,0.2) !important;
  }}
  .elevation-tooltip::before {{
    border-top-color: rgba(33, 37, 41, 0.9) !important;
  }}
  .draw-node-icon {{
    background: transparent !important;
    border: none !important;
  }}
</style>
</head>
<body>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const data = {gj};
const lang = '{lang}';
const elevationLabel = '{elevation_text}';
const elevationNoDataLabel = '{elevation_no_data_text}';
const elevationErrorLabel = '{elevation_error_text}';
const distanceLabel = '{distance_text}';
const slopeLabel = '{slope_text}';
const initialView = {initial_view_json};
const map = L.map('map', {{ zoomControl: true, preferCanvas: true }});
const pointRenderer = L.canvas({{ padding: 0.5 }});
const tileConfigs = {tile_configs_json};
tileConfigs.forEach(cfg => {{
  if (cfg.url) {{
    L.tileLayer(cfg.url, {{
      attribution: cfg.attribution || '', maxZoom: 19,
      opacity: cfg.opacity !== undefined ? cfg.opacity : 1.0
    }}).addTo(map);
  }}
}});

// History layer group (controls will be added dynamically at the bottom left)
const historyLayer = L.layerGroup().addTo(map);

// --- Snapping Logic Setup ---
const snapNodes = [];
const SNAP_TOLERANCE_PX = 12;

function collectSnapNodes(geojson) {{
  if (!geojson || !geojson.features) return;
  geojson.features.forEach(f => {{
    if (!f.geometry) return;
    const type = f.geometry.type;
    const coords = f.geometry.coordinates;
    
    const addPt = (pt) => {{
      snapNodes.push(L.latLng(pt[1], pt[0]));
    }};
    
    if (type === "Point") {{
      addPt(coords);
    }} else if (type === "MultiPoint") {{
      coords.forEach(addPt);
    }} else if (type === "LineString") {{
      coords.forEach(addPt);
    }} else if (type === "MultiLineString") {{
      coords.forEach(line => line.forEach(addPt));
    }} else if (type === "Polygon") {{
      coords.forEach(ring => ring.forEach(addPt));
    }} else if (type === "MultiPolygon") {{
      coords.forEach(poly => poly.forEach(ring => ring.forEach(addPt)));
    }}
  }});
}}
collectSnapNodes(data);

function getSnappedLatLng(latlng) {{
  if (!drawMode || snapNodes.length === 0) return latlng;
  
  const mousePoint = map.latLngToLayerPoint(latlng);
  let minSnappedLatLng = null;
  let minDistance = Infinity;
  
  for (let i = 0; i < snapNodes.length; i++) {{
    const nodeLatLng = snapNodes[i];
    const nodePoint = map.latLngToLayerPoint(nodeLatLng);
    const dist = mousePoint.distanceTo(nodePoint);
    
    if (dist < SNAP_TOLERANCE_PX && dist < minDistance) {{
      minDistance = dist;
      minSnappedLatLng = nodeLatLng;
    }}
  }}
  
  return minSnappedLatLng ? minSnappedLatLng : latlng;
}}

function propHtml(props) {{
  const keys = Object.keys(props || {{}}).slice(0, 30);
  if (!keys.length) return '{no_attr_text}';
  return '<table>' + keys.map(k => '<tr><th style="text-align:left;padding-right:8px">' + escapeHtml(k) + '</th><td>' + escapeHtml(String(props[k] ?? '')) + '</td></tr>').join('') + '</table>';
}}
function escapeHtml(s) {{ return s.replace(/[&<>"']/g, m => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m])); }}

const layerOpacities = {layer_opacities_json};

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
      let color = featureColor(feat, src);
      let weight = featureWeight(feat, src);
      let dashArray = null;
      
      let baseOpacity = (layerOpacities && layerOpacities[src] !== undefined) ? layerOpacities[src] : 1.0;
      let opacity = 0.9 * baseOpacity;
      
      const props = feat.properties || {{}};
      const tunnel = props.tunnel === 'yes' || props.tunnel === 'true' || props.tunnel === '1' || props.tunnel === true;
      const bridge = props.bridge === 'yes' || props.bridge === 'true' || props.bridge === '1' || props.bridge === true;
      
      if (tunnel) {{
        dashArray = '5, 8';
        opacity = 0.65 * baseOpacity;
        color = '#7950f2';
      }} else if (bridge) {{
        weight = weight + 3;
        color = '#f76707';
      }}
      
      return {{ color: color, weight: weight, opacity: opacity, dashArray: dashArray, renderer: pointRenderer }};
    }},
    pointToLayer: function(feat, latlng) {{
      const color = featureColor(feat, src);
      let baseOpacity = (layerOpacities && layerOpacities[src] !== undefined) ? layerOpacities[src] : 1.0;
      return L.circleMarker(latlng, {{
        renderer: pointRenderer,
        radius: manyFeatures ? 3 : 5,
        color: color,
        fillColor: color,
        weight: manyFeatures ? 1 : 2,
        fillOpacity: 0.8 * baseOpacity,
        opacity: 0.9 * baseOpacity
      }});
    }},
    onEachFeature: function(feat, lyr) {{
      lyr.bindPopup(function(layer) {{
        return propHtml(((layer.feature || feat).properties) || {{}});
      }});
      if (feat.geometry && feat.geometry.type === 'LineString') {{
        lyr.on('click', function(e) {{
          if (drawMode && drawPoints.length === 0) {{
            L.DomEvent.stopPropagation(e);
            selectedPlacedLine = {{ feature: feat, layer: lyr }};
          }}
        }});
      }}
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
  if (!selectMode || drawMode) return;
  dragStartLatLng = e.latlng;
  if (activeRect) {{
    map.removeLayer(activeRect);
    activeRect = null;
    clearHandles();
  }}
  activeRect = L.rectangle([dragStartLatLng, dragStartLatLng], {{ color: "#3388ff", weight: 2, fillOpacity: 0.15 }}).addTo(map);
}});

map.on('mousemove', function(e) {{
  if (!selectMode || drawMode || !dragStartLatLng || !activeRect) return;
  const bounds = L.latLngBounds(dragStartLatLng, e.latlng);
  activeRect.setBounds(bounds);
}});

map.on('mouseup', function(e) {{
  if (!selectMode || drawMode || !dragStartLatLng || !activeRect) return;
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
let drawMode = false;
let drawPoints = [];
let drawSegments = [];
let drawStructure = 'ground';
let drawPreviewLine = null;
let drawMarkers = [];
let placedNodeMarkers = [];
let selectedPlacedLine = null;

window.setDrawLineMode = function(enabled) {{
  drawMode = enabled;
  if (enabled && selectMode) {{
    selectMode = false;
    dragStartLatLng = null;
    const bboxBtn = document.getElementById('select-bbox-btn');
    if (bboxBtn) {{
      bboxBtn.style.backgroundColor = 'white';
      bboxBtn.innerHTML = '{select_btn_text}';
    }}
    map.dragging.enable();
  }}
  if (enabled) {{
    clearDrawState();
    addPlacedNodeHandles();
    map.doubleClickZoom.disable();
    map.getContainer().style.cursor = 'crosshair';
  }} else {{
    clearDrawState();
    clearPlacedNodeHandles();
    map.doubleClickZoom.enable();
    map.getContainer().style.cursor = '';
    if (typeof elevationTooltip !== 'undefined') {{
      map.closeTooltip(elevationTooltip);
    }}
  }}
}};

window.setDrawStructure = function(struct) {{
  drawStructure = struct;
  if (drawPreviewLine) {{
    updatePolylineStyle(drawPreviewLine, drawStructure);
  }}
  if (selectedPlacedLine && drawPoints.length === 0) {{
    const feature = selectedPlacedLine.feature;
    const props = feature.properties || {{}};
    delete props.bridge;
    delete props.tunnel;
    if (struct === 'bridge') props.bridge = 'yes';
    if (struct === 'tunnel') props.tunnel = 'yes';
    feature.properties = props;
    updatePolylineStyle(selectedPlacedLine.layer, struct);
    document.title = 'EDITED_STRUCTURE:' + JSON.stringify({{
      source: (props._source || props.source || ''),
      coords: feature.geometry.coordinates,
      structure: struct
    }});
  }}
}};

function clearDrawState() {{
  drawPoints = [];
  drawSegments.forEach(s => map.removeLayer(s.polyline));
  drawSegments = [];
  if (drawPreviewLine) {{ map.removeLayer(drawPreviewLine); drawPreviewLine = null; }}
  drawMarkers.forEach(m => map.removeLayer(m));
  drawMarkers = [];
}}

function clearPlacedNodeHandles() {{
  placedNodeMarkers.forEach(marker => map.removeLayer(marker));
  placedNodeMarkers = [];
}}

function addPlacedNodeHandles() {{
  clearPlacedNodeHandles();
  Object.values(layers).forEach(layerGroup => layerGroup.eachLayer(layer => {{
    const feature = layer.feature;
    if (!feature || !feature.geometry || feature.geometry.type !== 'LineString') return;
    const coords = feature.geometry.coordinates || [];
    coords.forEach((coord, index) => {{
      const marker = L.marker([coord[1], coord[0]], {{ draggable: true, icon: L.divIcon({{
        className: 'draw-node-icon',
        html: '<div style="width:10px;height:10px;background:#fff;border:2px solid #e8590c;border-radius:50%;margin:-5px 0 0 -5px;box-shadow:0 1px 3px rgba(0,0,0,0.35);"></div>',
        iconSize: [10, 10]
      }}) }}).addTo(map);
      marker.on('dragstart', function() {{
        marker._editOriginalCoords = feature.geometry.coordinates.map(point => point.slice());
      }});
      marker.on('drag', function() {{
        const pos = marker.getLatLng();
        feature.geometry.coordinates[index] = [pos.lng, pos.lat];
        layer.setLatLngs(feature.geometry.coordinates.map(point => [point[1], point[0]]));
      }});
      marker.on('dragend', function() {{
        document.title = 'EDITED_LINE:' + JSON.stringify({{
          source: (feature.properties && (feature.properties._source || feature.properties.source)) || '',
          old_coords: marker._editOriginalCoords,
          coords: feature.geometry.coordinates
        }});
      }});
      placedNodeMarkers.push(marker);
    }});
  }}));
}}

window.restoreLineGeometry = function(source, currentCoords, previousCoords) {{
  Object.values(layers).forEach(layerGroup => layerGroup.eachLayer(layer => {{
    const feature = layer.feature;
    const props = (feature && feature.properties) || {{}};
    if (feature && feature.geometry && feature.geometry.type === 'LineString' &&
        (!source || (props._source || props.source || '') === source) &&
        JSON.stringify(feature.geometry.coordinates) === JSON.stringify(currentCoords)) {{
      feature.geometry.coordinates = previousCoords;
      layer.setLatLngs(previousCoords.map(point => [point[1], point[0]]));
    }}
  }}));
  if (drawMode) addPlacedNodeHandles();
}};

window.applyLineStructure = function(source, coords, structure) {{
  Object.values(layers).forEach(layerGroup => layerGroup.eachLayer(layer => {{
    const feature = layer.feature;
    const props = (feature && feature.properties) || {{}};
    if (feature && feature.geometry && feature.geometry.type === 'LineString' &&
        (!source || (props._source || props.source || '') === source) &&
        JSON.stringify(feature.geometry.coordinates) === JSON.stringify(coords)) {{
      delete props.bridge;
      delete props.tunnel;
      if (structure === 'bridge') props.bridge = 'yes';
      if (structure === 'tunnel') props.tunnel = 'yes';
      feature.properties = props;
      updatePolylineStyle(layer, structure);
    }}
  }}));
}};

function updatePolylineStyle(polyline, struct) {{
  let color = '#e8590c';
  let weight = 4;
  let dashArray = null;
  let opacity = 0.9;
  
  if (struct === 'tunnel') {{
    dashArray = '5, 8';
    opacity = 0.65;
    color = '#7950f2';
  }} else if (struct === 'bridge') {{
    weight = 7;
    color = '#f76707';
  }}
  
  polyline.setStyle({{
    color: color,
    weight: weight,
    opacity: opacity,
    dashArray: dashArray
  }});
}}

map.on('click', function(e) {{
  if (!drawMode) return;
  const latlng = getSnappedLatLng(e.latlng);
  
  latlng.elevation = null;
  fetchClickElevation(latlng);
  
  drawPoints.push(latlng);
  
  const marker = createDrawMarker(latlng);
  drawMarkers.push(marker);
  
  const len = drawPoints.length;
  if (len >= 2) {{
    const p1 = drawPoints[len - 2];
    const p2 = drawPoints[len - 1];
    const segmentLine = L.polyline([p1, p2]).addTo(map);
    updatePolylineStyle(segmentLine, drawStructure);
    
    const dist = p1.distanceTo(p2);
    const segObj = {{
      polyline: segmentLine,
      structure: drawStructure,
      coords: [[p1.lng, p1.lat], [p2.lng, p2.lat]],
      distance: dist
    }};
    drawSegments.push(segObj);
    updateDrawDraftTitle();
  }}
}});

function fetchClickElevation(latlngObj) {{
  const url = 'https://cyberjapandata2.gsi.go.jp/general/dem/scripts/getelevation.php?lon=' + latlngObj.lng.toFixed(7) + '&lat=' + latlngObj.lat.toFixed(7) + '&outtype=JSON';
  if (elevationAbortController) elevationAbortController.abort();
  elevationAbortController = new AbortController();
  fetch(url, {{ signal: elevationAbortController.signal }})
    .then(res => res.json())
    .then(data => {{
      if (data && data.elevation !== undefined && data.elevation !== "-----") {{
        const elevNum = parseFloat(data.elevation);
        if (!isNaN(elevNum)) {{
          latlngObj.elevation = elevNum;
        }}
      }}
    }})
    .catch(err => {{
      if (err.name !== 'AbortError') console.error('Error caching click elevation:', err);
    }});
}}

function createDrawMarker(latlng) {{
  const nodeIcon = L.divIcon({{
    className: 'draw-node-icon',
    html: '<div style="width:8px;height:8px;background:#fff;border:2px solid #e8590c;border-radius:50%;margin:-4px 0 0 -4px;box-shadow:0 1px 3px rgba(0,0,0,0.3);"></div>',
    iconSize: [8, 8]
  }});
  
  const marker = L.marker(latlng, {{ icon: nodeIcon, draggable: true }}).addTo(map);
  
  marker.on('drag', function(e) {{
    const index = drawMarkers.indexOf(marker);
    if (index !== -1) {{
      const snapped = getSnappedLatLng(marker.getLatLng());
      marker.setLatLng(snapped);
      drawPoints[index] = snapped;
      
      snapped.elevation = null;
      
      rebuildPolyline();
    }}
  }});
  
  marker.on('dragend', function() {{
    fetchClickElevation(drawPoints[drawMarkers.indexOf(marker)]);
    rebuildPolyline();
  }});
  
  marker.on('contextmenu', function(e) {{
    L.DomEvent.stopPropagation(e);
    const index = drawMarkers.indexOf(marker);
    if (index !== -1) {{
      map.removeLayer(marker);
      drawMarkers.splice(index, 1);
      drawPoints.splice(index, 1);
      rebuildPolyline();
      map.closeTooltip(elevationTooltip);
    }}
  }});
  
  return marker;
}}

function rebuildPolyline() {{
  const oldStructures = drawSegments.map(s => s.structure);
  
  drawSegments.forEach(s => map.removeLayer(s.polyline));
  drawSegments = [];
  
  if (drawPreviewLine) {{
    map.removeLayer(drawPreviewLine);
    drawPreviewLine = null;
  }}
  
  const len = drawPoints.length;
  for (let i = 1; i < len; i++) {{
    const p1 = drawPoints[i - 1];
    const p2 = drawPoints[i];
    const segmentLine = L.polyline([p1, p2]).addTo(map);
    
    let struct = drawStructure;
    if (i - 1 < oldStructures.length) {{
      struct = oldStructures[i - 1];
    }}
    updatePolylineStyle(segmentLine, struct);
    
    const dist = p1.distanceTo(p2);
    drawSegments.push({{
      polyline: segmentLine,
      structure: struct,
      coords: [[p1.lng, p1.lat], [p2.lng, p2.lat]],
      distance: dist
    }});
  }}
}}

function updateDrawnLineTitle() {{
  if (drawSegments.length >= 1) {{
    const data = drawSegments.map(s => ({{
      coords: s.coords,
      structure: s.structure,
      distance: s.distance
    }}));
    const payload = JSON.stringify(data);
    const chunkSize = 1200;
    if (payload.length <= chunkSize) {{
      document.title = "DRAWN_LINE:" + payload;
      return;
    }}
    const transferId = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    const total = Math.ceil(payload.length / chunkSize);
    for (let index = 0; index < total; index++) {{
      const chunk = payload.slice(index * chunkSize, (index + 1) * chunkSize);
      setTimeout(() => {{
        document.title = "DRAWN_LINE_CHUNK:" + transferId + ":" + index + ":" + total + ":" + encodeURIComponent(chunk);
      }}, index * 5);
    }}
  }} else {{
    document.title = "DRAW_LINE_END";
  }}
}}

function updateDrawDraftTitle() {{
  const data = drawSegments.map(s => ({{
    coords: s.coords,
    structure: s.structure,
    distance: s.distance
  }}));
  const payload = JSON.stringify(data);
  const chunkSize = 1200;
  if (payload.length <= chunkSize) {{
    document.title = "DRAWN_LINE_DRAFT:" + payload;
    return;
  }}
  const transferId = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  const total = Math.ceil(payload.length / chunkSize);
  for (let index = 0; index < total; index++) {{
    const chunk = payload.slice(index * chunkSize, (index + 1) * chunkSize);
    setTimeout(() => {{
      document.title = "DRAWN_LINE_DRAFT_CHUNK:" + transferId + ":" + index + ":" + total + ":" + encodeURIComponent(chunk);
    }}, index * 5);
  }}
}}

map.on('mousemove', function(e) {{
  if (!drawMode) return;
  const latlng = getSnappedLatLng(e.latlng);
  const hasStartNode = drawPoints.length > 0;
  const p1 = hasStartNode ? drawPoints[drawPoints.length - 1] : null;
  if (hasStartNode) {{
    const previewPoints = [p1, latlng];
    if (!drawPreviewLine) {{
      drawPreviewLine = L.polyline(previewPoints).addTo(map);
    }} else {{
      drawPreviewLine.setLatLngs(previewPoints);
    }}
    updatePolylineStyle(drawPreviewLine, drawStructure);
  }}
}});

map.on('dblclick', function(e) {{
  if (!drawMode) return;
  L.DomEvent.stopPropagation(e);
  
  if (drawPoints.length >= 3) {{
    drawPoints.pop();
    const lastSeg = drawSegments.pop();
    if (lastSeg) {{
      map.removeLayer(lastSeg.polyline);
    }}
    const lastMarker = drawMarkers.pop();
    if (lastMarker) {{
      map.removeLayer(lastMarker);
    }}
  }}
  updateDrawnLineTitle();
  updateDrawDraftTitle();
}});

map.on('contextmenu', function(e) {{
  if (!drawMode) return;
  L.DomEvent.stopPropagation(e);
  
  const len = drawPoints.length;
  if (len > 0) {{
    drawPoints.pop();
    const lastMarker = drawMarkers.pop();
    if (lastMarker) {{
      map.removeLayer(lastMarker);
    }}
    const lastSeg = drawSegments.pop();
    if (lastSeg) {{
      map.removeLayer(lastSeg.polyline);
    }}
    
    if (drawPoints.length > 0) {{
      const p1 = drawPoints[drawPoints.length - 1];
      const snapped = getSnappedLatLng(e.latlng);
      if (drawPreviewLine) {{
        drawPreviewLine.setLatLngs([p1, snapped]);
      }}
    }} else {{
      if (drawPreviewLine) {{
        map.removeLayer(drawPreviewLine);
        drawPreviewLine = null;
      }}
    }}
    
    map.closeTooltip(elevationTooltip);
    updateDrawDraftTitle();
  }} else {{
    document.title = "DRAW_LINE_CANCEL";
  }}
}});

// --- Elevation Lookup on Hover (400ms debounce) ---
let elevationTimeout = null;
let elevationAbortController = null;
const elevationTooltip = L.tooltip({{
  direction: 'top',
  permanent: false,
  sticky: false,
  opacity: 0.85,
  className: 'elevation-tooltip'
}});

map.on('mousemove', function(e) {{
  if (elevationTimeout) {{
    clearTimeout(elevationTimeout);
  }}
  map.closeTooltip(elevationTooltip);
  
  if (!drawMode) return;
  
  const latlng = getSnappedLatLng(e.latlng);
  
  elevationTimeout = setTimeout(function() {{
    const url = 'https://cyberjapandata2.gsi.go.jp/general/dem/scripts/getelevation.php?lon=' + latlng.lng.toFixed(7) + '&lat=' + latlng.lat.toFixed(7) + '&outtype=JSON';
    
    fetch(url)
      .then(response => {{
        if (!response.ok) {{
          throw new Error('HTTP ' + response.status + ' ' + response.statusText);
        }}
        return response.json();
      }})
      .then(data => {{
        if (data && data.elevation !== undefined) {{
          let elevStr = data.elevation;
          let elevNum = NaN;
          if (elevStr === "-----") {{
            elevStr = elevationNoDataLabel;
          }} else {{
            elevNum = parseFloat(elevStr);
            if (!isNaN(elevNum)) {{
              elevStr = elevNum.toFixed(1) + " m";
            }}
          }}
          
          let content = elevationLabel + ": " + elevStr;
          if (data.hsrc && data.hsrc !== "-----") {{
            content += " (" + data.hsrc + ")";
          }}
          
          if (drawPoints.length > 0) {{
            const lastPoint = drawPoints[drawPoints.length - 1];
            const dist = lastPoint.distanceTo(latlng);
            let distStr = dist.toFixed(1) + " m";
            if (dist >= 1000) {{
              distStr = (dist / 1000).toFixed(2) + " km";
            }}
            
            let slopeStr = "--";
            if (!isNaN(elevNum) && lastPoint.elevation !== undefined && lastPoint.elevation !== null) {{
              const diffH = elevNum - lastPoint.elevation;
              const slope = dist > 0 ? (diffH / dist) * 1000 : 0;
              slopeStr = slope.toFixed(1);
            }}
            content += "<br>" + distanceLabel + ": " + distStr + " / " + slopeLabel + ": " + slopeStr + " ‰";
          }}
          
          elevationTooltip
            .setLatLng(latlng)
            .setContent(content)
            .addTo(map);
        }} else {{
          elevationTooltip
            .setLatLng(latlng)
            .setContent(elevationErrorLabel + " (Invalid response data)")
            .addTo(map);
        }}
      }})
      .catch(error => {{
        console.error('Error fetching elevation:', error);
        elevationTooltip
          .setLatLng(latlng)
          .setContent(elevationErrorLabel + " (" + error.message + ")")
          .addTo(map);
      }});
  }}, 400);
}});

map.on('mouseout', function() {{
  if (elevationTimeout) {{
    clearTimeout(elevationTimeout);
  }}
  if (elevationAbortController) elevationAbortController.abort();
  map.closeTooltip(elevationTooltip);
}});
</script>
</body>
</html>"""
