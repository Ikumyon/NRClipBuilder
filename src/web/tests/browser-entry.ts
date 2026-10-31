import "leaflet/dist/leaflet.css";
import "../src/map.css";
import { createMapController } from "../src/map-controller.ts";
import type { ControlsState, MapEvent, Position } from "../src/types.ts";

// Test-only entry point: exercise the production controller with real Leaflet.
const events: MapEvent[] = [];
const controls: ControlsState[] = [];
const requested: { longitude: number; signal?: AbortSignal | null }[] = [];
window.fetch = async (url, options) => {
  const longitude = Number(new URL(String(url)).searchParams.get("lon"));
  requested.push({ longitude, signal: options?.signal });
  await new Promise((resolve) => setTimeout(resolve, 120));
  return new Response(
    JSON.stringify({ elevation: 100 + (longitude - 139) * 100000 }),
  );
};
const controller = createMapController(
  document.getElementById("map") as HTMLDivElement,
  (event) => events.push(event),
  (state) => controls.push(state),
);
controller.command({ type: "view", view: { lat: 35, lng: 139, zoom: 16 } });
controller.command({
  type: "locale",
  language: "ja-jp",
  strings: { map_slope: "勾配", map_distance: "距離", map_elevation: "標高" },
});
function pixel(position: Position) {
  return controller.containerPoint(position).map(Math.round);
}
Object.assign(window, {
  mapTest: { ...controller, events, controls, requested, pixel },
});
