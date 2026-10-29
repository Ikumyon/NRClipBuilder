import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "leaflet/dist/leaflet.css";
import "./map.css";
import { createMapController } from "./map-controller.ts";
import { connectBackend } from "./protocol.ts";
import type { ControlsState, MapEvent } from "./types.ts";

function App() {
  const container = useRef<HTMLDivElement>(null);
  const controller = useRef<ReturnType<typeof createMapController> | null>(
    null,
  );
  const emit = useRef<(event: MapEvent) => void>(() => {});
  const [connected, setConnected] = useState(false);
  const [controls, setControls] = useState<ControlsState>({
    strings: {},
    selecting: false,
    hint: "",
  });

  useEffect(() => {
    if (!container.current) return;
    const map = createMapController(
      container.current,
      (event) => emit.current(event),
      setControls,
    );
    controller.current = map;
    window.nrclipMap = { inspect: map.inspect };
    const backend = connectBackend(map.command, () => setConnected(true));
    emit.current = backend.emit;
    return () => {
      backend.dispose();
      map.destroy();
      controller.current = null;
      delete window.nrclipMap;
    };
  }, []);

  const label = (key: string, defaultValue: string) => {
    const value = controls.strings[key];
    return typeof value === "string" ? value : defaultValue;
  };
  return (
    <>
      <div id="map" ref={container} />
      <div id="controls">
        <button
          id="bbox"
          aria-pressed={controls.selecting}
          onClick={() => controller.current?.toggleBBox()}
        >
          {label("map_select_btn", "Select BBox")}
        </button>
        <button
          id="osm"
          disabled={!connected}
          onClick={() => emit.current({ type: "fetch_osm" })}
        >
          {label("map_fetch_osm_btn", "Fetch OSM")}
        </button>
        {controls.hint && (
          <span id="hint" role="status">
            {controls.hint}
          </span>
        )}
      </div>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
