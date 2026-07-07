import html
import json
from typing import Any

APP_TITLE = "NRClipBuilder"


def make_leaflet_html(geojson: dict[str, Any], title: str = APP_TITLE) -> str:
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
    html: '<div style="width:10px;height:10px;background:#3388ff;border:2px solid white;border-radius:50%;margin:-4px 0 0 -4px;"></div>',
    iconSize: [10, 10]
  }});

  Object.keys(corners).forEach(pos => {{
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
      updateAppTitle(newBounds);
    }});

    marker.on('dragend', function() {{
      createHandles();
    }});
  }});
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
  const btns = document.getElementsByClassName('leaflet-bar');
  for (let i=0; i<btns.length; i++) {{
    const btn = btns[i];
    if (btn.classList.contains('leaflet-bar')) {{
      btn.style.backgroundColor = 'white';
      btn.innerHTML = '範囲選択';
    }}
  }}
  map.dragging.enable();
  createHandles();
  updateAppTitle(bounds);
}});

window.setActiveBounds = function(w, s, e, n) {{
  if (activeRect) {{
    map.removeLayer(activeRect);
    activeRect = null;
    clearHandles();
  }}
  const bounds = L.latLngBounds([[s, w], [n, e]]);
  activeRect = L.rectangle(bounds, {{ color: "#3388ff", weight: 2, fillOpacity: 0.15 }}).addTo(map);
  createHandles();
  map.fitBounds(bounds.pad(0.15));
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
    color: "#4dabf7",
    weight: 1,
    fillOpacity: 0.05,
    dashArray: "4, 4"
  }}).addTo(historyLayer);
  
  rect.bindTooltip(escapeHtml(name), {{
    permanent: true,
    direction: 'top',
    className: 'history-label'
  }});

  rect.on('click', function(e) {{
    L.DomEvent.stopPropagation(e);
    document.title = "SELECT_HISTORY:" + name + "," + w + "," + s + "," + e + "," + n;
  }});
}};

window.clearHistoryBounds = function() {{
  historyLayer.clearLayers();
}};
</script>
</body>
</html>"""
