import * as L from "leaflet";
import { formatElevationError } from "./elevation.ts";
import {
  createElevationSampler,
  ElevationSampleError,
} from "./elevation-sampler.ts";
import { draftSegments, geometryVertices, lineStyle } from "./geometry.ts";
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
  }).setView([35.68, 139.76], 6);
  const resize = new ResizeObserver(() => map.invalidateSize());
  resize.observe(container);
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
    draftVertices = L.layerGroup().addTo(map),
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
  let drawCursor: L.LatLng | null = null,
    drawPreview: L.Polyline | null = null;
  let strings: Strings = {},
    initializedView = false,
    hint = "";
  let elevation = "";
  let elevationFailure: {
    error: unknown;
    timedOut: boolean;
    latitude: number;
    longitude: number;
  } | null = null;
  let disposed = false;
  const sampler = createElevationSampler();
  function elevationErrorText() {
    if (!elevationFailure) return "";
    const { error, timedOut, latitude, longitude } = elevationFailure;
    return `${label("map_elevation_error", "Elevation unavailable")} (${latitude.toFixed(7)}, ${longitude.toFixed(7)}): ${formatElevationError(error, timedOut, strings)}`;
  }
  function notify() {
    onControls({
      strings,
      selecting,
      hint,
      elevation,
      elevationError: elevationErrorText(),
    });
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
  function segmentInfo(start: Position, end: Position): HTMLElement {
    const node = escapeNode("");
    const distance = map.distance(latlng(start), latlng(end));
    const prefix = `${label("map_distance", "Distance")}: ${distance.toFixed(1)} m`;
    node.textContent = `${prefix}\n${label("map_slope", "Gradient")}: …`;
    const first = start.slice(),
      last = end.slice();
    void Promise.all([sampler.sample(first), sampler.sample(last)])
      .then(([a, b]) => {
        if (disposed) return;
        node.textContent = `${label("map_elevation", "Elevation")}: ${a} m → ${b} m\n${prefix}\n${label("map_slope", "Gradient")}: ${distance > 0 ? (((b - a) / distance) * 1000).toFixed(1) + " ‰" : "—"}`;
      })
      .catch((error: unknown) => {
        if (!disposed)
          node.textContent = `${prefix}\n${sampleErrorText(error)}`;
      });
    return node;
  }
  function sampleErrorText(error: unknown) {
    return formatElevationError(
      error instanceof ElevationSampleError ? error.original : error,
      error instanceof ElevationSampleError && error.timedOut,
      strings,
    );
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
        const originalPosition = line[index].slice();
        const marker = L.marker(latlng(line[index]), {
          draggable: true,
          icon: pointIcon("vertex"),
        }).addTo(vertices);
        marker.on("drag", () => {
          const position = nearestPoint(marker.getLatLng(), originalPosition);
          marker.setLatLng(position);
          line[index] = coordinate(position);
          const path = dataLayers.get(layerId)?.features.get(feature.id)?.path;
          if (path instanceof L.Polyline)
            path.setLatLngs(
              editableLines(target.feature.geometry).map((part) =>
                part.map(latlng),
              ),
            );
        });
        marker.on("click", (event) => {
          if (drawMode) addPoint(marker.getLatLng());
          L.DomEvent.stopPropagation(event);
        });
        marker.on("dragend", () => {
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
          style: (feature) =>
            lineStyle(
              feature?.properties || {},
              layer.kind === "history",
              layer.name.toLowerCase().includes("osm"),
            ),
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
              if (drawMode) return;
              L.DomEvent.stopPropagation(event);
              selectFeature(layer.id, typed);
            });
            path.bindTooltip(() => {
              const node = escapeNode(
                Object.entries(feature.properties || {})
                  .map(([k, v]) => k + ": " + v)
                  .join("\n"),
              );
              if (typed.properties._manual_segment === true) {
                for (const line of editableLines(typed.geometry)) {
                  if (line.length > 1) {
                    node.append("\n");
                    node.appendChild(segmentInfo(line[0], line.at(-1)!));
                  }
                }
              }
              return node;
            });
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
        rect.bindTooltip(() => escapeNode(item.name), {
          permanent: true,
          direction: "top",
          className: "history-label",
        });
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
    const handles: { marker: L.Marker; xi: number; yi: number }[] = [];
    for (const [lat, lon, xi, yi] of corners) {
      const marker = L.marker([lat, lon], {
        draggable: true,
        icon: pointIcon("bbox-handle"),
      }).addTo(boundsHandles);
      handles.push({ marker, xi, yi });
      marker.on("drag", () => {
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
        bbox = normalized;
        bboxRect?.setBounds([
          [bbox[1], bbox[0]],
          [bbox[3], bbox[2]],
        ]);
        for (const handle of handles)
          if (handle.marker !== marker)
            handle.marker.setLatLng([bbox[handle.yi], bbox[handle.xi]]);
      });
      marker.on("dragend", () => {
        if (bbox && bbox[0] < bbox[2] && bbox[1] < bbox[3]) {
          const value = bbox;
          setBBox(value);
          emit("bbox", { bbox: value });
        }
      });
    }
  }

  function toggleBBox() {
    selecting = !selecting;
    if (selecting && drawMode) {
      drawMode = false;
      emit("draw_end");
    }
    syncMode();
    setHint(
      selecting ? label("map_select_btn_active", "Drag to select BBox") : "",
    );
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
    syncMode();
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

  function nearestPoint(p: L.LatLng, excluded?: Position) {
    let result = p,
      best = 12;
    const origin = map.latLngToContainerPoint(p);
    for (const entry of dataLayers.values()) {
      if (!map.hasLayer(entry.group)) continue;
      for (const item of entry.features.values())
        for (const c of geometryVertices(item.feature.geometry)) {
          if (excluded && c[0] === excluded[0] && c[1] === excluded[1])
            continue;
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
    draftVertices.clearLayers();
    for (const segment of segments)
      L.polyline(
        segment.coordinates.map(latlng),
        lineStyle({ [segment.structure]: "yes" }),
      )
        .bindTooltip(() =>
          segmentInfo(segment.coordinates[0], segment.coordinates[1]),
        )
        .addTo(drawing);
    points.forEach((p, index) => {
      const marker = L.marker(latlng(p), {
        draggable: drawMode,
        icon: pointIcon("vertex draft-vertex"),
      }).addTo(draftVertices);
      marker.on("drag", () => {
        marker.setLatLng(nearestPoint(marker.getLatLng()));
        points[index] = coordinate(marker.getLatLng());
        rebuildSegments();
        drawing.clearLayers();
        for (const segment of segments)
          L.polyline(
            segment.coordinates.map(latlng),
            lineStyle({ [segment.structure]: "yes" }),
          )
            .bindTooltip(() =>
              segmentInfo(segment.coordinates[0], segment.coordinates[1]),
            )
            .addTo(drawing);
        renderDrawPreview();
      });
      marker.on("dragend", () => {
        draftChanged();
        void sampler.sample(points[index]).catch(() => {});
        if (drawCursor) scheduleElevation(drawCursor, 0);
      });
      marker.on("contextmenu", (event) => {
        L.DomEvent.stopPropagation(event);
        L.DomEvent.preventDefault(event.originalEvent);
        if (!drawMode) return;
        points.splice(index, 1);
        segments.splice(index === 0 ? 0 : index - 1, 1);
        draftChanged();
        if (drawCursor) scheduleElevation(drawCursor, 0);
      });
      marker.on("click", (event) => {
        L.DomEvent.stopPropagation(event);
        if (drawMode) addPoint(marker.getLatLng());
      });
    });
    renderDrawPreview();
  }
  function renderDrawPreview() {
    if (!drawMode || selecting || !points.length || !drawCursor) {
      if (drawPreview) map.removeLayer(drawPreview);
      drawPreview = null;
      return;
    }
    const coordinates = [latlng(points.at(-1)!), nearestPoint(drawCursor)];
    const options: L.PolylineOptions = {
      ...lineStyle({ [structure]: "yes" }),
      interactive: false,
    };
    if (drawPreview) drawPreview.setLatLngs(coordinates).setStyle(options);
    else drawPreview = L.polyline(coordinates, options).addTo(map);
  }
  function draftChanged() {
    rebuildSegments();
    renderDraft();
    emit("draft", { draft: { points, segments } });
  }
  function rebuildSegments() {
    segments = draftSegments(
      points,
      segments.map((segment) => segment.structure),
    );
  }
  function syncMode() {
    if (drawMode) {
      selecting = false;
      selectionStart = null;
      if (selectionPreview) map.removeLayer(selectionPreview);
      selectionPreview = null;
    }
    if (drawMode || selecting) map.doubleClickZoom.disable();
    else map.doubleClickZoom.enable();
    if (selecting) map.dragging.disable();
    else map.dragging.enable();
    map.getContainer().style.cursor = drawMode ? "crosshair" : "";
    renderDraft();
  }
  function addPoint(cursor: L.LatLng) {
    if (!drawMode || selecting) return;
    drawCursor = cursor;
    const p = coordinate(nearestPoint(cursor));
    if (points.length && map.distance(latlng(points.at(-1)!), latlng(p)) < 0.05)
      return;
    if (points.length)
      segments.push({ structure, coordinates: [points.at(-1)!, p] });
    points.push(p);
    void sampler.sample(p).catch(() => {});
    draftChanged();
    scheduleElevation(cursor, 0);
  }
  map.on("click", (event) => {
    addPoint(event.latlng);
  });
  map.on("dblclick", () => {
    if (drawMode && segments.length) emit("finish_draft");
  });
  map.on("contextmenu", (event) => {
    if (drawMode) {
      drawCursor = event.latlng;
      L.DomEvent.preventDefault(event.originalEvent);
      if (!points.length) {
        drawMode = false;
        syncMode();
        notify();
        emit("draw_end");
        return;
      }
      points.pop();
      segments.pop();
      draftChanged();
      scheduleElevation(event.latlng, 0);
    }
  });
  map.on("mousemove", (event) => {
    if (!drawMode) return;
    drawCursor = event.latlng;
    renderDrawPreview();
  });
  map.on("mouseout", () => {
    drawCursor = null;
    renderDrawPreview();
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
  let elevationSequence = 0;
  function scheduleElevation(cursor: L.LatLng, delay = 600) {
    clearTimeout(elevationTimer);
    const sequence = ++elevationSequence;
    const p = drawMode ? nearestPoint(cursor) : cursor;
    const previous = drawMode ? points.at(-1)?.slice() : undefined;
    const distance = previous ? map.distance(latlng(previous), p) : 0;
    function measurementText(height: string, gradient: string) {
      let text = label("map_elevation", "Elevation") + ": " + height;
      if (previous)
        text +=
          " · " +
          label("map_distance", "Distance") +
          ": " +
          distance.toFixed(1) +
          " m · " +
          label("map_slope", "Gradient") +
          ": " +
          gradient;
      return text;
    }
    elevation = measurementText("…", distance > 0 ? "…" : "—");
    notify();
    elevationTimer = setTimeout(async () => {
      try {
        const heightRequest = sampler.sample(coordinate(p));
        const startRequest = previous
          ? sampler.sample(previous)
          : heightRequest;
        const [height, startHeight] = await Promise.all([
          heightRequest,
          startRequest,
        ]);
        if (disposed || sequence !== elevationSequence) return;
        elevation = measurementText(
          height + " m",
          distance > 0
            ? (((height - startHeight) / distance) * 1000).toFixed(1) + " ‰"
            : "—",
        );
        elevationFailure = null;
        notify();
      } catch (error) {
        if (!disposed && sequence === elevationSequence) {
          elevation = "";
          elevationFailure = {
            error:
              error instanceof ElevationSampleError ? error.original : error,
            timedOut: error instanceof ElevationSampleError && error.timedOut,
            latitude:
              error instanceof ElevationSampleError ? error.point[1] : p.lat,
            longitude:
              error instanceof ElevationSampleError ? error.point[0] : p.lng,
          };
          console.error("GSI elevation request failed", elevationErrorText());
          notify();
        }
      }
    }, delay);
  }
  map.on("mousemove", (event) => scheduleElevation(event.latlng));

  function command(message: Command) {
    if (message.type === "update") update(message.payload);
    if (message.type === "draw") {
      drawMode = message.active;
      if (!drawMode) drawCursor = null;
      syncMode();
      setHint(
        selecting ? label("map_select_btn_active", "Drag to select BBox") : "",
      );
      vertices.clearLayers();
      editTarget = null;
      scheduleElevation(drawCursor ?? map.getCenter(), 0);
    }
    if (message.type === "structure") {
      structure = message.value;
      renderDrawPreview();
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
      rebuildSegments();
      renderDraft();
      if (drawCursor) scheduleElevation(drawCursor, 0);
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
      renderDraft();
    }
    if (message.type === "view" && !initializedView) {
      map.setView([message.view.lat, message.view.lng], message.view.zoom);
      initializedView = true;
    }
  }

  return {
    command,
    toggleBBox,
    containerPoint: (position: Position) => {
      const p = map.latLngToContainerPoint(latlng(position));
      return [p.x, p.y];
    },
    inspect: () => ({
      points: structuredClone(points),
      segments: structuredClone(segments),
      featureCount: [...dataLayers.values()].reduce(
        (sum, entry) => sum + entry.features.size,
        0,
      ),
      view: { ...map.getCenter(), zoom: map.getZoom() },
      bbox: bbox?.slice() ?? null,
      drawMode,
      selecting,
    }),
    destroy() {
      disposed = true;
      clearTimeout(viewTimer);
      clearTimeout(elevationTimer);
      sampler.destroy();
      resize.disconnect();
      map.remove();
    },
  };
}
