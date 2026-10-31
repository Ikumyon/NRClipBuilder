import { test } from "node:test";
import assert from "node:assert/strict";
import { runInNewContext } from "node:vm";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import type { ControlsState } from "../src/types.ts";
import type { createMapController } from "../src/map-controller.ts";

// Exercise the real controller events and asynchronous elevation responses.
// Leaflet rendering is replaced so these checks run without a browser or network.
const bundle = await build({
  entryPoints: [
    fileURLToPath(new URL("../src/map-controller.ts", import.meta.url)),
  ],
  bundle: true,
  write: false,
  platform: "node",
  format: "cjs",
  plugins: [
    {
      name: "leaflet-test-map",
      setup(builder) {
        builder.onResolve({ filter: /^leaflet$/ }, () => ({
          path: "leaflet",
          namespace: "mock",
        }));
        builder.onLoad({ filter: /.*/, namespace: "mock" }, () => ({
          contents: `
          export const map = () => globalThis.testMap;
          const layer = () => ({ addTo() { return this; }, clearLayers() {}, setLatLngs() { return this; }, setStyle() { return this; }, bindTooltip() { return this; }, on() { return this; } });
          export const layerGroup = layer, polyline = layer, circleMarker = layer;
          const unused = () => { throw new Error("Unexpected Leaflet API in gradient test"); };
          export const divIcon = () => ({}), marker = layer, Polyline = class {}, tileLayer = unused, geoJSON = unused, rectangle = unused, latLngBounds = unused;
          export const latLng = (lat, lng) => ({ lat, lng });
          export const DomEvent = { preventDefault() {} };
        `,
        }));
      },
    },
  ],
});

function setup(fetcher: typeof fetch) {
  const handlers = new Map<string, ((event: unknown) => void)[]>();
  const timers = new Map<number, { callback: () => unknown; delay: number }>();
  const controls: ControlsState[] = [];
  let timerId = 0;
  const testMap = {
    doubleClickZoom: { enable() {}, disable() {} },
    dragging: { enable() {}, disable() {} },
    getContainer() {
      return { style: {} };
    },
    setView() {
      return this;
    },
    on(name: string, handler: (event: unknown) => void) {
      handlers.set(name, [...(handlers.get(name) ?? []), handler]);
    },
    distance(a: number[], b: { lat: number; lng: number } | number[]) {
      const lat = Array.isArray(b) ? b[0] : b.lat;
      const lng = Array.isArray(b) ? b[1] : b.lng;
      return Math.hypot(a[0] - lat, a[1] - lng) * 1000;
    },
    latLngToContainerPoint() {
      return {};
    },
    getCenter() {
      return { lat: 35, lng: 139 };
    },
    getZoom() {
      return 16;
    },
    removeLayer() {},
    remove() {},
  };
  const module = {
    exports: {} as { createMapController: typeof createMapController },
  };
  runInNewContext(bundle.outputFiles[0].text, {
    module,
    exports: module.exports,
    testMap,
    fetch: fetcher,
    AbortController,
    structuredClone,
    ResizeObserver: class {
      observe() {}
      disconnect() {}
    },
    console,
    setTimeout(callback: () => unknown, delay: number) {
      timers.set(++timerId, { callback, delay });
      return timerId;
    },
    clearTimeout(id: number) {
      timers.delete(id);
    },
  });
  const controller = module.exports.createMapController(
    {} as HTMLDivElement,
    () => {},
    (state) => controls.push(state),
  );
  controller.command({ type: "draw", active: true });
  return {
    controller,
    state: () => controls.at(-1)!,
    event(name: string, lng: number) {
      for (const handler of handlers.get(name) ?? [])
        handler({ latlng: { lat: 35, lng }, originalEvent: {} });
    },
    async flush() {
      const ready = [...timers].filter(([, timer]) => timer.delay < 5000);
      await Promise.all(
        ready.map(([id, timer]) => {
          timers.delete(id);
          return timer.callback();
        }),
      );
    },
  };
}

test("rubber band fetches both heights even when the cursor leaves the first click immediately", async () => {
  const requested: number[] = [];
  const app = setup(async (url) => {
    const lng = Number(new URL(String(url)).searchParams.get("lon"));
    requested.push(lng);
    return new Response(JSON.stringify({ elevation: lng === 139 ? 10 : 12 }));
  });
  app.event("click", 139);
  app.event("mousemove", 139.1);
  assert.match(app.state().elevation, /Gradient: …/);
  await app.flush();
  assert.ok(requested.includes(139));
  assert.ok(requested.includes(139.1));
  assert.match(app.state().elevation, /Distance: 100.0 m · Gradient: 20.0 ‰/);
  assert.equal(app.controller.inspect().points.length, 1);
  app.controller.destroy();
});

test("clicking the next vertex switches the gradient origin and zero distance is undefined", async () => {
  const app = setup(async (url) => {
    const lng = Number(new URL(String(url)).searchParams.get("lon"));
    return new Response(JSON.stringify({ elevation: lng === 139.1 ? 20 : 10 }));
  });
  app.event("click", 139);
  await app.flush();
  assert.match(app.state().elevation, /Gradient: —/);
  app.event("click", 139.1);
  app.event("mousemove", 139.2);
  await app.flush();
  assert.match(app.state().elevation, /Gradient: -100.0 ‰/);
  app.controller.destroy();
});

test("a late response cannot overwrite the current rubber band gradient", async () => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  const app = setup(async (url) => {
    const lng = Number(new URL(String(url)).searchParams.get("lon"));
    if (lng === 139.1) await pending;
    return new Response(JSON.stringify({ elevation: lng === 139.1 ? 99 : 10 }));
  });
  app.event("click", 139);
  await app.flush();
  app.event("mousemove", 139.1);
  const old = app.flush();
  app.event("mousemove", 139.2);
  await app.flush();
  const current = app.state().elevation;
  release();
  await old;
  assert.equal(app.state().elevation, current);
  assert.match(current, /Gradient: 0.0 ‰/);
  app.controller.destroy();
});
