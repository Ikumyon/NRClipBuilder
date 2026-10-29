import * as L from "leaflet";
import type * as GeoJSON from "geojson";
import type {
  BBox,
  Collection,
  Command,
  ControlsState,
  Draft,
  Geometry,
  LayerPayload,
  MapEvent,
  MapFeature,
  MapPayload,
  Position,
  Properties,
  Strings,
  Structure,
} from "./types.ts";

export function createMapController(
  container: HTMLDivElement,
  sendEvent: (event: MapEvent) => void,
  onControls: (state: ControlsState) => void,
) {
  const map = L.map(container, {
    preferCanvas: true,
    doubleClickZoom: false,
  }).setView([35.68, 139.76], 6);
  type FeatureEntry = { encoded: string; feature: MapFeature; path: L.Layer };
  type DataEntry = {
    group: L.GeoJSON<Properties>;
    features: Map<string, FeatureEntry>;
    kind: LayerPayload["kind"];
  };
  const dataLayers = new Map<string, DataEntry>(),
    tileLayers = new Map<string, { url: string; group: L.TileLayer }>();
  const historyBounds = L.layerGroup().addTo(map),
    boundsHandles = L.layerGroup().addTo(map);
  const drawing = L.layerGroup().addTo(map),
    vertices = L.layerGroup().addTo(map);
  let selected: string | null = null;
  let editTarget: { layerId: string; feature: MapFeature } | null = null;
  let bbox: BBox | null = null,
    bboxRect: L.Rectangle | null = null;
  let selecting = false,
    selectionStart: L.LatLng | null = null,
    selectionPreview: L.Rectangle | null = null;
  let drawMode = false,
    structure: Structure = "ground";
  let points: Position[] = [],
    segments: Draft["segments"] = [];
  let strings: Strings = {},
    initializedView = false,
    hint = "";
  let disposed = false;
  const elevationRequests = new Set<AbortController>();
  function notify() {
    onControls({ strings, selecting, hint });
  }
  function setHint(value: string) {
    hint = value;
    notify();
  }
  function label(key: string, defaultValue: string): string {
    const value = strings[key];
    return typeof value === "string" ? value : defaultValue;
  }
  function emit<T extends MapEvent["type"]>(
    type: T,
    payload?: Omit<Extract<MapEvent, { type: T }>, "type">,
  ) {
    if (!disposed) sendEvent({ type, ...payload } as MapEvent);
  }
  function color(props: Properties) {
    return props.tunnel === "yes" || Number(props.layer) < 0
      ? "#7950f2"
      : props.bridge === "yes" || Number(props.layer) > 0
        ? "#e8590c"
        : "#1971c2";
  }
  function style(feature: GeoJSON.Feature<Geometry, Properties> | undefined) {
    return { color: color(feature?.properties || {}), weight: 4 };
  }
  function pointIcon(className: string) {
    return L.divIcon({ className, iconSize: [10, 10], iconAnchor: [5, 5] });
  }
  function coordinate(latlng: L.LatLng): Position {
    return [latlng.lng, latlng.lat];
  }
  function latlng(p: Position): L.LatLngTuple {
    return [p[1], p[0]];
  }
  function escapeNode(text: string) {
    const node = document.createElement("span");
    node.textContent = String(text);
    return node;
  }

  function editableLines(geometry: Geometry): Position[][] {
    if (geometry.type === "LineString") return [geometry.coordinates];
    if (geometry.type === "MultiLineString") return geometry.coordinates;
    if (geometry.type === "GeometryCollection")
      return geometry.geometries.flatMap(editableLines);
    return [];
  }
  function selectFeature(layerId: string, feature: MapFeature) {
    vertices.clearLayers();
    if (
      drawMode ||
      layerId !== selected ||
      dataLayers.get(layerId)?.kind !== "data"
    )
      return;
    const target = { layerId, feature: structuredClone(feature) };
    editTarget = target;
    for (const line of editableLines(target.feature.geometry)) {
      for (let index = 0; index < line.length; index++) {
        const marker = L.marker(latlng(line[index]), {
          draggable: true,
          icon: pointIcon("vertex"),
        }).addTo(vertices);
        marker.on("dragend", () => {
          line[index] = coordinate(marker.getLatLng());
          emit("edit", { layer_id: layerId, feature: target.feature });
        });
      }
    }
  }

  function update(payload: MapPayload) {
    selected = payload.selected;
    const alive = new Set(payload.layers.map((layer) => layer.id));
    for (const [id, entry] of dataLayers)
      if (!alive.has(id)) {
        map.removeLayer(entry.group);
        dataLayers.delete(id);
      }
    for (const [id, entry] of tileLayers)
      if (!alive.has(id)) {
        map.removeLayer(entry.group);
        tileLayers.delete(id);
      }
    let order = 300;
    for (const layer of payload.layers) {
      if (layer.kind === "tile") {
        let entry = tileLayers.get(layer.id);
        if (entry && entry.url !== layer.url) {
          map.removeLayer(entry.group);
          tileLayers.delete(layer.id);
          entry = undefined;
        }
        if (!entry) {
          entry = {
            url: layer.url,
            group: L.tileLayer(layer.url, {
              attribution: layer.attribution,
              maxZoom: 20,
            }),
          };
          tileLayers.set(layer.id, entry);
        }
        entry.group.setOpacity(layer.opacity).setZIndex(order++);
        if (layer.visible) entry.group.addTo(map);
        else map.removeLayer(entry.group);
        continue;
      }
      const paneName = "layer-" + layer.id;
      const pane = map.getPane(paneName) ?? map.createPane(paneName);
      pane.style.zIndex = String(order++);
      pane.style.opacity = String(layer.opacity);
      let entry = dataLayers.get(layer.id);
      if (!entry) {
        const features = new Map<string, FeatureEntry>();
        const group = L.geoJSON<Properties>(undefined, {
          pane: paneName,
          style,
          pointToLayer: (_, p) =>
            L.circleMarker(p, { radius: 4, pane: paneName }),
          onEachFeature: (feature, path) => {
            const typed = feature as MapFeature;
            features.set(typed.id, {
              encoded: JSON.stringify(typed),
              feature: typed,
              path,
            });
            path.on("click", (event) => {
              L.DomEvent.stopPropagation(event.originalEvent);
              selectFeature(layer.id, typed);
            });
            path.bindTooltip(() =>
              escapeNode(
                Object.entries(feature.properties || {})
                  .map(([k, v]) => k + ": " + v)
                  .join("\n"),
              ),
            );
          },
        });
        entry = { group, features, kind: layer.kind };
        dataLayers.set(layer.id, entry);
      }
      if (layer.geojson) {
        const geojson: Collection =
          typeof layer.geojson === "string"
            ? JSON.parse(layer.geojson)
            : layer.geojson;
        const ids = new Set();
        for (const feature of geojson.features) {
          ids.add(feature.id);
          const encoded = JSON.stringify(feature),
            previous = entry.features.get(feature.id);
          if (previous && previous.encoded === encoded) continue;
          if (previous) entry.group.removeLayer(previous.path);
          entry.group.addData(feature);
        }
        for (const [id, previous] of entry.features)
          if (!ids.has(id)) {
            entry.group.removeLayer(previous.path);
            entry.features.delete(id);
          }
      }
      if (layer.visible) entry.group.addTo(map);
      else map.removeLayer(entry.group);
    }
    vertices.clearLayers();
    editTarget = null;
    setBBox(payload.bbox);
    historyBounds.clearLayers();
    const historyVisible = payload.layers.some(
      (layer) => layer.kind === "history" && layer.visible,
    );
    if (historyVisible)
      for (const item of payload.history) {
        const [w, s, e, n] = item.bbox;
        const rect = L.rectangle(
          [
            [s, w],
            [n, e],
          ],
          { color: "#f08c00", weight: 1, fillOpacity: 0.03 },
        ).addTo(historyBounds);
        rect.bindTooltip(() => escapeNode(item.name));
        rect.on("click", (event) => {
          L.DomEvent.stopPropagation(event);
          emit("history", { id: item.id });
        });
      }
    emit("rendered");
  }

  function setBBox(value: BBox | null) {
    bbox = value;
    if (bboxRect) map.removeLayer(bboxRect);
    bboxRect = null;
    boundsHandles.clearLayers();
    if (!bbox) return;
    const [w, s, e, n] = bbox;
    bboxRect = L.rectangle(
      [
        [s, w],
        [n, e],
      ],
      { color: "#f08c00", weight: 2, fillOpacity: 0.04, interactive: false },
    ).addTo(map);
    const corners: [number, number, number, number][] = [
      [s, w, 0, 1],
      [n, w, 0, 3],
      [s, e, 2, 1],
      [n, e, 2, 3],
    ];
    for (const [lat, lon, xi, yi] of corners) {
      const marker = L.marker([lat, lon], {
        draggable: true,
        icon: pointIcon("bbox-handle"),
      }).addTo(boundsHandles);
      marker.on("dragend", () => {
        if (!bbox) return;
        const b = [...bbox];
        b[xi] = marker.getLatLng().lng;
        b[yi] = marker.getLatLng().lat;
        const normalized: BBox = [
          Math.min(b[0], b[2]),
          Math.min(b[1], b[3]),
          Math.max(b[0], b[2]),
          Math.max(b[1], b[3]),
        ];
        setBBox(normalized);
        emit("bbox", { bbox: normalized });
      });
    }
  }

  function toggleBBox() {
    selecting = !selecting;
    setHint(
      selecting ? label("map_select_btn_active", "Drag to select BBox") : "",
    );
    if (selecting) map.dragging.disable();
    else map.dragging.enable();
  }
  map.on("mousedown", (event) => {
    if (selecting) selectionStart = event.latlng;
  });
  map.on("mousemove", (event) => {
    if (!selecting || !selectionStart) return;
    if (selectionPreview) map.removeLayer(selectionPreview);
    selectionPreview = L.rectangle(
      L.latLngBounds(selectionStart, event.latlng),
      { interactive: false },
    ).addTo(map);
  });
  map.on("mouseup", (event) => {
    if (!selecting || !selectionStart) return;
    const bounds = L.latLngBounds(selectionStart, event.latlng);
    if (selectionPreview) map.removeLayer(selectionPreview);
    selectionPreview = null;
    selectionStart = null;
    selecting = false;
    map.dragging.enable();
    setHint("");
    const b: BBox = [
      bounds.getWest(),
      bounds.getSouth(),
      bounds.getEast(),
      bounds.getNorth(),
    ];
    if (b[0] < b[2] && b[1] < b[3]) {
      setBBox(b);
      emit("bbox", { bbox: b });
    }
  });

  function nearestPoint(p: L.LatLng) {
    let result = p,
      best = 12;
    const origin = map.latLngToContainerPoint(p);
    for (const entry of dataLayers.values()) {
      if (!map.hasLayer(entry.group)) continue;
      for (const item of entry.features.values())
        for (const line of editableLines(item.feature.geometry))
          for (const c of line) {
            const q = L.latLng(c[1], c[0]),
              distance = origin.distanceTo(map.latLngToContainerPoint(q));
            if (distance < best) {
              best = distance;
              result = q;
            }
          }
    }
    return result;
  }
  function renderDraft() {
    drawing.clearLayers();
    for (const segment of segments)
      L.polyline(segment.coordinates.map(latlng), {
        color: color({
          layer: { ground: 0, bridge: 1, tunnel: -1 }[segment.structure],
        }),
        weight: 4,
      }).addTo(drawing);
    for (const p of points)
      L.circleMarker(latlng(p), { radius: 4, color: "#d6336c" }).addTo(drawing);
  }
  function draftChanged() {
    renderDraft();
    emit("draft", { draft: { points, segments } });
  }
  map.on("click", (event) => {
    if (!drawMode || selecting) return;
    const p = coordinate(nearestPoint(event.latlng));
    if (points.length && map.distance(latlng(points.at(-1)!), latlng(p)) < 0.05)
      return;
    if (points.length)
      segments.push({ structure, coordinates: [points.at(-1)!, p] });
    points.push(p);
    draftChanged();
  });
  map.on("dblclick", () => {
    if (drawMode && segments.length) emit("finish_draft");
  });
  map.on("contextmenu", (event) => {
    if (drawMode) {
      L.DomEvent.preventDefault(event.originalEvent);
      points.pop();
      segments.pop();
      draftChanged();
    }
  });
  let viewTimer: ReturnType<typeof setTimeout> | undefined;
  map.on("moveend", () => {
    clearTimeout(viewTimer);
    viewTimer = setTimeout(() => {
      const center = map.getCenter();
      emit("view", {
        view: { lat: center.lat, lng: center.lng, zoom: map.getZoom() },
      });
    }, 200);
  });
  let elevationTimer: ReturnType<typeof setTimeout> | undefined;
  map.on("mousemove", (event) => {
    clearTimeout(elevationTimer);
    elevationTimer = setTimeout(async () => {
      const p = event.latlng;
      const request = new AbortController();
      elevationRequests.add(request);
      const timeout = setTimeout(() => request.abort(), 5000);
      try {
        const response = await fetch(
          "https://cyberjapandata2.gsi.go.jp/general/dem/scripts/getelevation.php?lon=" +
            p.lng.toFixed(7) +
            "&lat=" +
            p.lat.toFixed(7) +
            "&outtype=JSON",
          { signal: request.signal },
        );
        const data: { elevation: number | string } = await response.json();
        if (disposed) return;
        let text =
          label("map_elevation", "Elevation") + ": " + data.elevation + " m";
        if (drawMode && points.length) {
          const distance = map.distance(latlng(points.at(-1)!), p);
          text +=
            " · " +
            label("map_distance", "Distance") +
            ": " +
            distance.toFixed(1) +
            " m";
          const previous = points.at(-1)!;
          if (
            previous.length > 2 &&
            distance > 0 &&
            typeof data.elevation === "number" &&
            Number.isFinite(data.elevation)
          )
            text +=
              " · " +
              label("map_slope", "Gradient") +
              ": " +
              (((data.elevation - previous[2]) / distance) * 1000).toFixed(1) +
              " ‰";
        }
        if (!selecting) setHint(text);
        if (
          drawMode &&
          points.length &&
          map.distance(latlng(points.at(-1)!), p) < 1 &&
          typeof data.elevation === "number" &&
          Number.isFinite(data.elevation)
        ) {
          points.at(-1)![2] = data.elevation as number;
          emit("draft", { draft: { points, segments } });
        }
      } catch {
        if (!disposed && !selecting)
          setHint(label("map_elevation_error", "Elevation unavailable"));
      } finally {
        clearTimeout(timeout);
        elevationRequests.delete(request);
      }
    }, 600);
  });

  function command(message: Command) {
    if (message.type === "update") update(message.payload);
    if (message.type === "draw") {
      drawMode = message.active;
      vertices.clearLayers();
    }
    if (message.type === "structure") {
      structure = message.value;
      if (editTarget) {
        const props = editTarget.feature.properties;
        delete props.bridge;
        delete props.tunnel;
        props.layer = String({ ground: 0, bridge: 1, tunnel: -1 }[structure]);
        if (structure !== "ground") props[structure] = "yes";
        emit("edit", {
          layer_id: editTarget.layerId,
          feature: editTarget.feature,
        });
      }
    }
    if (message.type === "draft") {
      points = message.draft.points || [];
      segments = message.draft.segments || [];
      renderDraft();
    }
    if (message.type === "fit" && message.bbox) {
      const [w, s, e, n] = message.bbox;
      map.fitBounds([
        [s, w],
        [n, e],
      ]);
    }
    if (message.type === "locale") {
      strings = message.strings;
      document.documentElement.lang = message.language;
      notify();
    }
    if (message.type === "view" && !initializedView) {
      map.setView([message.view.lat, message.view.lng], message.view.zoom);
      initializedView = true;
    }
  }

  return {
    command,
    toggleBBox,
    inspect: () => ({
      points: structuredClone(points),
      segments: structuredClone(segments),
      featureCount: [...dataLayers.values()].reduce(
        (sum, entry) => sum + entry.features.size,
        0,
      ),
    }),
    destroy() {
      disposed = true;
      clearTimeout(viewTimer);
      clearTimeout(elevationTimer);
      for (const request of elevationRequests) request.abort();
      map.remove();
    },
  };
}
